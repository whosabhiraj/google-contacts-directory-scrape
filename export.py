"""Export matching contacts from directory.sqlite3 to CSV or a plain list.

Takes the same search terms and regex filters as filter.py, but writes the
selected fields to a file instead of printing formatted records.
"""

import argparse
import csv
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional, Sequence, TextIO, Tuple

from filter import (
    ALL_FIELDS,
    DEFAULT_WORKERS,
    FIELD_INDEX,
    ContactRow,
    RegexFilter,
    add_output_arguments,
    add_query_arguments,
    add_regex_arguments,
    configure_stdout,
    collect_matches,
    collect_queries,
    compile_regex_filters,
    parse_field_list,
    resolve_sort,
    scan_rows_by_regex,
    sort_rows,
)


FORMATS = ("csv", "tsv", "lines")
DELIMITERS = {"csv": ",", "tsv": "\t"}

Record = Tuple[str, ...]


def select_values(row: ContactRow, fields: Sequence[str]) -> Record:
    return tuple((row[FIELD_INDEX[field]] or "").strip() for field in fields)


def deduplicate(records: Sequence[Record]) -> List[Record]:
    seen = set()
    unique = []

    for record in records:
        if record not in seen:
            seen.add(record)
            unique.append(record)

    return unique


def gather_rows(
    args: argparse.Namespace,
    filters: Sequence[RegexFilter],
    require_all: bool,
    workers: int = DEFAULT_WORKERS,
) -> List[ContactRow]:
    """Collect rows for the search terms, or scan the table when only regexes are given.

    Rows come back unsorted; the caller sorts the combined result once so that
    rows from different search terms interleave correctly.
    """
    queries = collect_queries(args, allow_generated_default=False)

    if queries:
        matches = collect_matches(
            queries, args.db, filters, require_all, workers=workers, sort_by=()
        )
        return matches.all_rows()

    return scan_rows_by_regex(filters, require_all, args.db, workers=workers, sort_by=())


def write_records(
    records: Sequence[Record],
    fields: Sequence[str],
    output_format: str,
    stream: TextIO,
    header: bool = True,
) -> None:
    if output_format == "lines":
        for record in records:
            stream.write(f"{record[0]}\n")
        return

    writer = csv.writer(stream, delimiter=DELIMITERS[output_format], lineterminator="\n")
    if header:
        writer.writerow(fields)
    writer.writerows(records)


def open_output(path: Optional[Path]) -> TextIO:
    if path is None:
        return sys.stdout

    return path.open("w", encoding="utf-8", newline="")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export matching contacts to CSV, TSV, or a one-value-per-line list.",
        epilog="Example: python export.py --email-regex '^f2025' "
        "--format lines -o emails.txt",
    )
    add_query_arguments(parser)
    add_regex_arguments(parser)
    add_output_arguments(parser)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="File to write. Defaults to stdout.",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=FORMATS,
        default="csv",
        help="csv (default), tsv, or lines for one value per line with no commas.",
    )
    parser.add_argument(
        "--fields",
        help="Comma-separated fields to export: name, email, employee_id. "
        "Defaults to all three, or just email for --format lines.",
    )
    parser.add_argument(
        "--no-header",
        action="store_true",
        help="Skip the CSV/TSV header row.",
    )
    parser.add_argument(
        "--keep-duplicates",
        action="store_true",
        help="Keep repeated rows (duplicates are dropped by default).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Cap the total number of exported rows.",
    )
    return parser.parse_args()


def resolve_fields(args: argparse.Namespace) -> List[str]:
    if args.fields:
        fields = parse_field_list(args.fields)
        if not fields:
            raise SystemExit("No fields selected.")
    elif args.format == "lines":
        fields = ["email"]
    else:
        fields = list(ALL_FIELDS)

    if args.format == "lines" and len(fields) > 1:
        raise SystemExit(
            "--format lines writes one value per line, so pick a single field "
            f"(got: {', '.join(fields)})."
        )

    return fields


def main() -> None:
    args = parse_args()
    configure_stdout()
    filters = compile_regex_filters(args)
    require_all = args.regex_mode == "all"
    fields = resolve_fields(args)
    workers = max(1, args.workers)
    sort_by = resolve_sort(args, default=fields)

    if not (args.queries or args.queries_file or args.generate or filters):
        raise SystemExit(
            "Nothing to export. Give search terms, or a --regex / --name-regex / "
            "--email-regex / --id-regex filter."
        )

    try:
        rows = gather_rows(args, filters, require_all, workers)
    except sqlite3.Error as e:
        raise SystemExit(f"Database error: {e}")

    records = [select_values(row, fields) for row in sort_rows(rows, sort_by)]

    if args.format == "lines":
        records = [record for record in records if record[0]]

    if not args.keep_duplicates:
        records = deduplicate(records)

    if args.limit is not None:
        records = records[: args.limit]

    stream = open_output(args.output)
    try:
        write_records(records, fields, args.format, stream, not args.no_header)
    finally:
        if stream is not sys.stdout:
            stream.close()

    destination = args.output if args.output else "stdout"
    print(
        f"Exported {len(records)} row(s) ({', '.join(fields)}) to {destination}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExport cancelled.", file=sys.stderr)

    except BrokenPipeError:
        # Downstream command (head, less, ...) closed the pipe.
        sys.stderr.close()
