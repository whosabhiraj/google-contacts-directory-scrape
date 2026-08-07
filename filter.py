import argparse
import math
import os
import re
import sqlite3
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import (
    Callable,
    DefaultDict,
    Dict,
    Iterable,
    List,
    NamedTuple,
    Optional,
    Pattern,
    Sequence,
    Tuple,
)


DB_PATH = Path(__file__).with_name("directory.sqlite3")
SEPARATOR = "-" * 40
DEFAULT_WORKERS = min(8, os.cpu_count() or 4)
# Threads pay off for LIKE terms, where SQLite releases the GIL while stepping
# rows. A regex scan spends its time in Python's re module, which holds the GIL,
# so past a few threads the chunking overhead is all that is left.
SCAN_WORKER_CAP = 4

ContactRow = Tuple[str, str, str]
FIELD_INDEX = {"name": 0, "email": 1, "employee_id": 2}
ALL_FIELDS = tuple(FIELD_INDEX)
MATCH_MODES = ("contains", "exact", "regex")
FIELD_ALIASES = {
    "id": "employee_id",
    "emp_id": "employee_id",
    "empid": "employee_id",
    "employeeid": "employee_id",
    "mail": "email",
}
DEFAULT_SORT: Tuple[str, ...] = ("name",)
SORT_CYCLE: Tuple[Tuple[str, ...], ...] = (
    ("name",),
    ("email",),
    ("employee_id",),
    (),
)
MENU_CHOICES = {
    "1": (("name",), "name"),
    "2": (("email",), "email"),
    "3": (("employee_id",), "employee ID"),
    "4": (ALL_FIELDS, "all fields"),
}


class QuitSignal(Exception):
    """Raised when the user asks to leave the interactive session."""


class OrderedMatches(NamedTuple):
    """Matched rows keyed by search term, plus the original term order."""

    terms: List[str]
    by_term: Dict[str, List["ContactRow"]]

    def rows_for(self, term: str) -> List["ContactRow"]:
        return self.by_term.get(term, [])

    def all_rows(self) -> List["ContactRow"]:
        return [row for term in self.terms for row in self.rows_for(term)]


class RegexFilter(NamedTuple):
    """A compiled pattern and the contact fields it is tested against."""

    fields: Tuple[str, ...]
    pattern: Pattern[str]

    def matches(self, row: ContactRow) -> bool:
        return any(
            self.pattern.search(row[FIELD_INDEX[field]] or "")
            for field in self.fields
        )

    def describe(self) -> str:
        return f"{'/'.join(self.fields)} =~ /{self.pattern.pattern}/"


def configure_stdout() -> None:
    """Print UTF-8 regardless of the console codepage.

    Windows consoles default to cp1252, which raises UnicodeEncodeError on the
    characters some directory names contain.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def configure_search_connection(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -64000")


class ConnectionPool:
    """One SQLite connection per worker thread.

    A connection cannot be shared between threads, so each thread lazily opens
    its own. They are opened with check_same_thread=False purely so the pool can
    close them all at the end from the main thread; every connection is still
    used by exactly one thread.
    """

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._local = threading.local()
        self._connections: List[sqlite3.Connection] = []
        self._lock = threading.Lock()

    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn

        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        configure_search_connection(conn)
        self._local.conn = conn
        with self._lock:
            self._connections.append(conn)

        return conn

    def close(self) -> None:
        with self._lock:
            for conn in self._connections:
                conn.close()
            self._connections.clear()

    def __enter__(self) -> "ConnectionPool":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def run_in_threads(
    task: Callable,
    items: Sequence,
    workers: int = DEFAULT_WORKERS,
) -> List:
    """Map `task` over `items`, preserving input order."""
    if not items:
        return []

    if workers <= 1 or len(items) == 1:
        return [task(item) for item in items]

    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as executor:
        return list(executor.map(task, items))


def parse_field_list(raw: str) -> List[str]:
    """Turn 'email,name' into a validated list of column names."""
    fields: List[str] = []

    for part in raw.replace(" ", ",").split(","):
        name = part.strip().lower()
        name = FIELD_ALIASES.get(name, name)
        if not name:
            continue
        if name not in FIELD_INDEX:
            raise SystemExit(
                f"Unknown field {part.strip()!r}. Choose from: {', '.join(ALL_FIELDS)}"
            )
        if name not in fields:
            fields.append(name)

    return fields


def sort_rows(
    rows: Sequence[ContactRow],
    sort_by: Sequence[str] = DEFAULT_SORT,
) -> List[ContactRow]:
    """Sort rows ascending, case-insensitively, by the given fields."""
    if not sort_by:
        return list(rows)

    def key(row: ContactRow):
        return tuple((row[FIELD_INDEX[field]] or "").casefold() for field in sort_by)

    return sorted(rows, key=key)


def describe_sort(sort_by: Sequence[str]) -> str:
    return ", ".join(sort_by) if sort_by else "off"


def has_like_wildcard(search_term: str) -> bool:
    return "%" in search_term or "_" in search_term


def normalize(search_term: str) -> str:
    return search_term.casefold()


def format_results(search_term: str, rows: Sequence[ContactRow]) -> str:
    if not rows:
        return ""

    lines = [
        f"\nFound {len(rows)} matching record(s) for '{search_term}':",
        SEPARATOR,
    ]
    for name, email, employee_id in rows:
        lines.extend((
            f"Name: {name}",
            f"Email: {email}",
            f"ID: {employee_id}",
            SEPARATOR,
        ))

    return "\n".join(lines) + "\n"


def compile_regex_filters(args: argparse.Namespace) -> List[RegexFilter]:
    """Build the regex filters requested on the command line."""
    flags = 0 if args.regex_case_sensitive else re.IGNORECASE
    specs = (
        (ALL_FIELDS, args.regex),
        (("name",), args.name_regex),
        (("email",), args.email_regex),
        (("employee_id",), args.id_regex),
    )

    filters: List[RegexFilter] = []
    for fields, patterns in specs:
        for pattern in patterns:
            try:
                filters.append(RegexFilter(fields, re.compile(pattern, flags)))
            except re.error as e:
                raise SystemExit(f"Invalid regex {pattern!r}: {e}")

    return filters


def row_matches(
    row: ContactRow,
    filters: Sequence[RegexFilter],
    require_all: bool = False,
) -> bool:
    if not filters:
        return True

    results = (regex_filter.matches(row) for regex_filter in filters)
    return all(results) if require_all else any(results)


def apply_regex_filters(
    rows: Sequence[ContactRow],
    filters: Sequence[RegexFilter],
    require_all: bool = False,
) -> List[ContactRow]:
    if not filters:
        return list(rows)

    return [row for row in rows if row_matches(row, filters, require_all)]


def describe_filters(
    filters: Sequence[RegexFilter],
    require_all: bool = False,
) -> str:
    joiner = " AND " if require_all else " OR "
    return joiner.join(regex_filter.describe() for regex_filter in filters)


def rowid_ranges(conn: sqlite3.Connection, workers: int) -> List[Tuple[int, int]]:
    """Split the table into contiguous rowid chunks, one per worker."""
    bounds = conn.execute("SELECT MIN(rowid), MAX(rowid) FROM contacts").fetchone()
    if not bounds or bounds[0] is None:
        return []

    low, high = bounds
    if workers <= 1:
        return [(low, high)]

    chunk = max(1, math.ceil((high - low + 1) / workers))
    return [
        (start, min(start + chunk - 1, high))
        for start in range(low, high + 1, chunk)
    ]


def scan_rows_by_regex(
    filters: Sequence[RegexFilter],
    require_all: bool = False,
    db_path: Path = DB_PATH,
    limit: Optional[int] = None,
    workers: int = DEFAULT_WORKERS,
    sort_by: Sequence[str] = DEFAULT_SORT,
) -> List[ContactRow]:
    """Scan every contact in parallel and return the rows matching the filters."""
    if not filters:
        return []

    query = """
        SELECT name, email, employee_id
        FROM contacts
        WHERE rowid BETWEEN ? AND ?
    """

    scan_workers = min(workers, SCAN_WORKER_CAP)

    with ConnectionPool(db_path) as pool:
        ranges = rowid_ranges(pool.connection(), scan_workers)

        def scan_chunk(bounds: Tuple[int, int]) -> List[ContactRow]:
            cursor = pool.connection().execute(query, bounds)
            return [row for row in cursor if row_matches(row, filters, require_all)]

        chunks = run_in_threads(scan_chunk, ranges, scan_workers)

    rows = sort_rows([row for chunk in chunks for row in chunk], sort_by)
    return rows[:limit] if limit is not None else rows


def scan_contacts_by_regex(
    filters: Sequence[RegexFilter],
    require_all: bool = False,
    db_path: Path = DB_PATH,
    limit: Optional[int] = None,
    workers: int = DEFAULT_WORKERS,
    sort_by: Sequence[str] = DEFAULT_SORT,
) -> str:
    if not filters:
        return ""

    try:
        rows = scan_rows_by_regex(filters, require_all, db_path, limit, workers, sort_by)
    except sqlite3.Error as e:
        return f"Database error: {e}"

    return format_results(describe_filters(filters, require_all), rows)


def search_contacts(search_term: str, db_path: Path = DB_PATH) -> str:
    return search_contacts_many([search_term], db_path)


def add_exact_matches(
    conn: sqlite3.Connection,
    search_terms: Sequence[str],
    matches: DefaultDict[str, List[ContactRow]],
) -> None:
    unique_terms = list(dict.fromkeys(search_terms))
    terms_by_key: DefaultDict[str, List[str]] = defaultdict(list)
    for term in unique_terms:
        terms_by_key[normalize(term)].append(term)

    conn.execute(
        "CREATE TEMP TABLE exact_terms("
        "term TEXT COLLATE NOCASE PRIMARY KEY"
        ")"
    )
    conn.executemany(
        "INSERT OR IGNORE INTO exact_terms(term) VALUES (?)",
        ((term,) for term in unique_terms),
    )

    query = """
        SELECT name, email, employee_id
        FROM contacts
        WHERE email COLLATE NOCASE IN (SELECT term FROM exact_terms)
        OR employee_id COLLATE NOCASE IN (SELECT term FROM exact_terms)
        OR name COLLATE NOCASE IN (SELECT term FROM exact_terms)
    """

    for row in conn.execute(query):
        row_terms = set()
        for value in row:
            if value is not None:
                row_terms.update(terms_by_key.get(normalize(value), ()))

        for term in row_terms:
            matches[term].append(row)


def add_like_matches(
    pool: "ConnectionPool",
    search_terms: Sequence[str],
    matches: DefaultDict[str, List[ContactRow]],
    workers: int = DEFAULT_WORKERS,
) -> None:
    """Run one LIKE scan per term, spread across worker threads.

    Each term costs a full table scan, so this is where threading pays off:
    SQLite releases the GIL while stepping through rows.
    """
    query = """
        SELECT name, email, employee_id
        FROM contacts
        WHERE email LIKE ?
        OR employee_id LIKE ?
        OR name LIKE ?
    """

    terms = list(dict.fromkeys(search_terms))

    def run(term: str) -> List[ContactRow]:
        return pool.connection().execute(query, (term, term, term)).fetchall()

    for term, rows in zip(terms, run_in_threads(run, terms, workers)):
        matches[term].extend(rows)


def collect_matches(
    search_terms: Iterable[str],
    db_path: Path = DB_PATH,
    filters: Sequence[RegexFilter] = (),
    require_all: bool = False,
    limit: Optional[int] = None,
    workers: int = DEFAULT_WORKERS,
    sort_by: Sequence[str] = DEFAULT_SORT,
) -> "OrderedMatches":
    """Search many terms and return the matched rows keyed by search term.

    Regex filters are applied to the matched rows, so a term search can be
    narrowed to the contacts whose name/email/employee ID fit a pattern.
    """
    ordered_terms = [term for term in search_terms if term]
    exact_terms = [term for term in ordered_terms if not has_like_wildcard(term)]
    like_terms = [term for term in ordered_terms if has_like_wildcard(term)]
    matches: DefaultDict[str, List[ContactRow]] = defaultdict(list)

    if not ordered_terms:
        return OrderedMatches([], {})

    with ConnectionPool(db_path) as pool:
        if exact_terms:
            add_exact_matches(pool.connection(), exact_terms, matches)
        if like_terms:
            add_like_matches(pool, like_terms, matches, workers)

    filtered = {}
    for term in ordered_terms:
        if term not in matches:
            continue

        rows = sort_rows(
            apply_regex_filters(matches[term], filters, require_all),
            sort_by,
        )
        filtered[term] = rows[:limit] if limit is not None else rows

    return OrderedMatches(ordered_terms, filtered)


def search_contacts_many(
    search_terms: Iterable[str],
    db_path: Path = DB_PATH,
    filters: Sequence[RegexFilter] = (),
    require_all: bool = False,
    limit: Optional[int] = None,
    workers: int = DEFAULT_WORKERS,
    sort_by: Sequence[str] = DEFAULT_SORT,
) -> str:
    """Search many terms across email, employee ID, and name."""
    try:
        result = collect_matches(
            search_terms, db_path, filters, require_all, limit, workers, sort_by
        )
    except sqlite3.Error as e:
        return f"Database error: {e}"

    return "".join(
        format_results(term, result.rows_for(term)) for term in result.terms
    )


def search_employee_ids(
    employee_ids: Iterable[str],
    db_path: Path = DB_PATH,
) -> str:
    return search_contacts_many(employee_ids, db_path)


def ensure_search_indexes(db_path: Path = DB_PATH) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_contacts_email_nocase
            ON contacts(email COLLATE NOCASE)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_contacts_name_nocase
            ON contacts(name COLLATE NOCASE)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_contacts_employee_id_nocase
            ON contacts(employee_id COLLATE NOCASE)
        """)


def query_contacts(
    term: str,
    fields: Sequence[str],
    mode: str = "contains",
    db_path: Path = DB_PATH,
    limit: Optional[int] = None,
    sort_by: Sequence[str] = DEFAULT_SORT,
    workers: int = DEFAULT_WORKERS,
) -> str:
    """Search `term` against the chosen fields using the chosen match mode."""
    if mode == "regex":
        try:
            pattern = re.compile(term, re.IGNORECASE)
        except re.error as e:
            return f"\nInvalid regex: {e}\n"

        return scan_contacts_by_regex(
            [RegexFilter(tuple(fields), pattern)],
            db_path=db_path,
            limit=limit,
            workers=workers,
            sort_by=sort_by,
        )

    if mode == "exact":
        clause = " OR ".join(f"{field} COLLATE NOCASE = ?" for field in fields)
        params: List[object] = [term] * len(fields)
    else:
        clause = " OR ".join(f"{field} LIKE ?" for field in fields)
        params = [f"%{term}%"] * len(fields)

    sql = f"SELECT name, email, employee_id FROM contacts WHERE {clause}"
    if sort_by:
        sql += " ORDER BY " + ", ".join(f"{field} COLLATE NOCASE ASC" for field in sort_by)
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    try:
        with sqlite3.connect(db_path) as conn:
            configure_search_connection(conn)
            rows = conn.execute(sql, params).fetchall()
    except sqlite3.Error as e:
        return f"Database error: {e}"

    return format_results(term, rows)


def prompt(message: str) -> str:
    try:
        return input(message).strip()
    except EOFError:
        print()
        raise QuitSignal


def print_menu(mode: str, limit: Optional[int], sort_by: Sequence[str]) -> None:
    print("\nSearch by:")
    for key, (_, label) in MENU_CHOICES.items():
        print(f"  {key}) {label[0].upper()}{label[1:]}")
    print(f"  5) Match mode: {mode}")
    print(f"  6) Result limit: {limit if limit is not None else 'none'}")
    print(f"  7) Sort by: {describe_sort(sort_by)}")
    print("  q) Quit")


def ask_limit(current: Optional[int]) -> Optional[int]:
    answer = prompt("New limit (blank for no limit): ")
    if not answer:
        return None

    if answer.isdigit() and int(answer) > 0:
        return int(answer)

    print("Not a positive number, keeping the current limit.")
    return current


def run_search_loop(
    fields: Sequence[str],
    label: str,
    mode: str,
    db_path: Path,
    limit: Optional[int],
    sort_by: Sequence[str],
    workers: int,
) -> None:
    print(f"\nSearching {label} ({mode}). Blank input returns to the menu.")

    while True:
        term = prompt(f"{label} > ")
        if not term:
            return

        print(
            query_contacts(term, fields, mode, db_path, limit, sort_by, workers)
            or f"\nNo matches for '{term}'.\n",
            end="",
        )


def run_interactive(
    db_path: Path = DB_PATH,
    limit: Optional[int] = None,
    sort_by: Sequence[str] = DEFAULT_SORT,
    workers: int = DEFAULT_WORKERS,
) -> None:
    """Menu-driven search session: pick a field, then search it repeatedly."""
    mode = "contains"
    sort_by = tuple(sort_by)

    try:
        while True:
            print_menu(mode, limit, sort_by)
            choice = prompt("Select an option: ").lower()

            if choice in {"q", "quit", "exit"}:
                return

            if choice in MENU_CHOICES:
                fields, label = MENU_CHOICES[choice]
                run_search_loop(fields, label, mode, db_path, limit, sort_by, workers)
            elif choice == "5":
                mode = MATCH_MODES[(MATCH_MODES.index(mode) + 1) % len(MATCH_MODES)]
            elif choice == "6":
                limit = ask_limit(limit)
            elif choice == "7":
                index = SORT_CYCLE.index(sort_by) if sort_by in SORT_CYCLE else -1
                sort_by = SORT_CYCLE[(index + 1) % len(SORT_CYCLE)]
            elif choice:
                print("Pick 1-7, or q to quit.")
    except QuitSignal:
        return


def read_query_file(path: Path) -> List[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def generated_queries(args: argparse.Namespace) -> List[str]:
    return [
        f"{args.prefix}{i:0{args.width}d}{args.suffix}"
        for i in range(args.start, args.stop)
    ]


def collect_queries(
    args: argparse.Namespace,
    allow_generated_default: bool = True,
) -> List[str]:
    queries = list(args.queries)

    if args.queries_file:
        queries.extend(read_query_file(args.queries_file))

    if args.generate or (not queries and allow_generated_default):
        queries.extend(generated_queries(args))

    if args.contains:
        return [f"%{query}%" for query in queries]

    return queries


def add_query_arguments(parser: argparse.ArgumentParser) -> None:
    """Search-term flags, shared by filter.py and export.py."""
    parser.add_argument(
        "queries",
        nargs="*",
        help="Search terms. Use %% or _ for LIKE patterns.",
    )
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument(
        "--queries-file",
        type=Path,
        help="Read search terms from a file, one per line.",
    )
    parser.add_argument(
        "--contains",
        action="store_true",
        help="Wrap each query in %%...%% for substring matching.",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Append the generated prefix/number/suffix range to the query list.",
    )
    parser.add_argument("--prefix", default="2025B3PS")
    parser.add_argument("--suffix", default="P")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--stop", type=int, default=2000)
    parser.add_argument("--width", type=int, default=4)


def add_output_arguments(parser: argparse.ArgumentParser) -> None:
    """Threading and ordering flags, shared by filter.py and export.py."""
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Worker threads for searching (default: {DEFAULT_WORKERS}, 1 = serial).",
    )
    parser.add_argument(
        "--sort-by",
        default=None,
        metavar="FIELDS",
        help="Comma-separated fields to sort ascending by: name, email, employee_id.",
    )
    parser.add_argument(
        "--no-sort",
        action="store_true",
        help="Leave results in database order instead of sorting ascending.",
    )


def resolve_sort(args: argparse.Namespace, default: Sequence[str] = DEFAULT_SORT) -> Tuple[str, ...]:
    if args.no_sort:
        return ()

    if args.sort_by:
        return tuple(parse_field_list(args.sort_by))

    return tuple(default)


def add_regex_arguments(parser: argparse.ArgumentParser) -> None:
    """Regex filter flags, shared by filter.py and export.py."""
    parser.add_argument(
        "--regex",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Keep contacts whose name, email, or employee ID matches PATTERN. "
        "Repeatable.",
    )
    parser.add_argument(
        "--name-regex",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Keep contacts whose name matches PATTERN. Repeatable.",
    )
    parser.add_argument(
        "--email-regex",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Keep contacts whose email matches PATTERN. Repeatable.",
    )
    parser.add_argument(
        "--id-regex",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Keep contacts whose employee ID matches PATTERN. Repeatable.",
    )
    parser.add_argument(
        "--regex-mode",
        choices=("any", "all"),
        default="any",
        help="Match any regex (default) or require every regex to match.",
    )
    parser.add_argument(
        "--regex-case-sensitive",
        action="store_true",
        help="Match regexes case-sensitively (case-insensitive by default).",
    )

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search directory.sqlite3 quickly.")
    add_query_arguments(parser)
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Start the menu-driven search session (the default with no queries).",
    )
    add_regex_arguments(parser)
    add_output_arguments(parser)
    parser.add_argument(
        "--limit",
        type=int,
        help="Cap the number of results (per search term when terms are given).",
    )
    parser.add_argument(
        "--ensure-index",
        action="store_true",
        help="Create search indexes before searching. Useful for repeated runs.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    configure_stdout()

    print("--- Directory Search ---")

    regex_filters = compile_regex_filters(args)
    require_all = args.regex_mode == "all"
    sort_by = resolve_sort(args)
    workers = max(1, args.workers)
    batch_requested = bool(
        args.queries or args.queries_file or args.generate or regex_filters
    )
    interactive = args.interactive or not batch_requested
    queries = (
        []
        if interactive
        else collect_queries(args, allow_generated_default=not regex_filters)
    )

    try:
        if args.ensure_index:
            ensure_search_indexes(args.db)

        if interactive:
            run_interactive(args.db, args.limit, sort_by, workers)
        elif queries:
            print(
                search_contacts_many(
                    queries,
                    args.db,
                    regex_filters,
                    require_all,
                    args.limit,
                    workers,
                    sort_by,
                ),
                end="",
            )
        else:
            print(
                scan_contacts_by_regex(
                    regex_filters,
                    require_all,
                    args.db,
                    args.limit,
                    workers,
                    sort_by,
                ),
                end="",
            )

    except KeyboardInterrupt:
        print("\nExiting search. Goodbye!")

    except BrokenPipeError:
        # Downstream command (head, less, ...) closed the pipe.
        sys.stderr.close()
