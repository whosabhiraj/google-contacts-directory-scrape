# Google Contacts Directory Scraper

A tool to export contact information from your Google Workspace directory and save it to SQLite for searching.

(Fork of [Google Contacts Email Scraper](https://github.com/aryanranderiya/GoogleContactsEmailScraper))

## Features

- Exports names, emails, and employee IDs from your organization directory
- Saves to a local SQLite database for quick lookups
- Secure OAuth authentication
- Multi-threaded querying for large datasets

## Prerequisites

- Python 3.7+
- Google Cloud project with People API enabled
- OAuth 2.0 credentials for the People API
- Access to your Google Workspace directory

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set up Google OAuth

Create a Google Cloud project, enable the People API, and download your OAuth 2.0 credentials. Include these scopes:
- `https://www.googleapis.com/auth/directory.readonly`
- `https://www.googleapis.com/auth/contacts.readonly`

### 3. Save credentials
Put your credentials JSON file in the project directory as `credentials.json`.

### 4. Run it
```bash
python app.py
```

First run opens a browser to authenticate. After that, `token.json` is created and used automatically.

## Output

The script creates `directory.sqlite3` with a `contacts` table containing:

```
name        - Contact name
email       - Email address
employee_id - Employee ID
```

Query examples:
```sql
SELECT * FROM contacts WHERE email LIKE '%@pilani.bits-pilani.ac.in';
SELECT * FROM contacts WHERE name LIKE '%Abhiraj%';
```

## Using filter.py

`filter.py` searches the local database. Run it with no arguments for the interactive menu:

```bash
python filter.py
```

```
Search by:
  1) Name
  2) Email
  3) Employee ID
  4) All fields
  5) Match mode: contains
  6) Result limit: none
  q) Quit
```

Pick a field, then type search terms one after another - blank input goes back to the
menu, `q` quits. Option 5 cycles the match mode between `contains`, `exact`, and `regex`;
option 6 caps how many results each search prints.

### Regex filters

Keep only the contacts whose fields follow a pattern:

```bash
python filter.py --id-regex '^2025B2PS[0-9]{4}P$'
python filter.py --email-regex '^f2025[0-9]{4}@pilani'
python filter.py --regex 'sahoo'                          # any of the three fields
python filter.py --name-regex '^TANISHA' --email-regex '@pilani' --regex-mode all
```

Regexes are case-insensitive unless you pass `--regex-case-sensitive`. Multiple flags are
OR-ed by default; `--regex-mode all` requires every pattern to match. Combined with search
terms, the regexes narrow the term results instead of scanning the whole table:

```bash
python filter.py '%tanisha%' --email-regex 'pilani\.bits' --limit 5
```

### Batch queries

Many terms at once is much faster than running them one at a time:

```bash
python filter.py --queries-file list.txt
python filter.py --generate --prefix 2025B2PS --suffix P --start 1 --stop 2000
python filter.py --contains 'sharma' 'gupta'
```

### Ordering and threads

Results come back sorted ascending by name. Both scripts share these flags:

| Flag | Meaning |
| --- | --- |
| `--sort-by` | Sort ascending by `name`, `email`, `employee_id`, or a comma-separated list |
| `--no-sort` | Leave rows in database order |
| `--workers` | Search threads (defaults to CPU count capped at 8; `1` is serial) |

Sorting is case-insensitive. In the interactive menu, option 7 cycles the sort field.

Each search term costs a full table scan, so terms are spread across worker threads -
SQLite releases the GIL while stepping rows. Measured on 399k contacts with 16 terms:

| Workers | filter.py | export.py |
| --- | --- | --- |
| 1 | 1.34s | 1.56s |
| 4 | 0.64s | 0.83s |
| 8 | 0.63s | 0.85s |

Regex scans are the exception: matching happens in Python's `re`, which holds the GIL, so
extra threads do not help there and the scan caps itself at 4 workers.

## Exporting with export.py

`export.py` takes the same search terms and regex filters as `filter.py`, but writes the
selected fields to a file instead of printing records.

```bash
# just the emails, one per line, no commas
python export.py --email-regex '^f2025' --format lines -o emails.txt

# CSV with all three columns
python export.py --id-regex '^2025B2PS[0-9]{4}P$' -o batch2025.csv

# pick your columns
python export.py '%tanisha%' --fields name,email --sort -o tanisha.csv
```

| Flag | Meaning |
| --- | --- |
| `-f, --format` | `csv` (default), `tsv`, or `lines` for one value per line |
| `--fields` | `name`, `email`, `employee_id` (aliases: `id`, `mail`); defaults to all three, or `email` for `lines` |
| `-o, --output` | Output file; defaults to stdout |
| `--no-header` | Skip the CSV/TSV header row |
| `--limit` | Cap the total rows written |
| `--keep-duplicates` | Keep repeated rows (duplicates are dropped by default) |

Exports are sorted ascending by the selected fields (so `--fields email` gives a sorted
email list); `--sort-by` and `--no-sort` override that.

`--format lines` writes a single field per line, so it only accepts one field. The
"exported N rows" summary goes to stderr, which keeps piped output clean:

```bash
python export.py --email-regex '@pilani' --format lines > emails.txt
```

## Notes

- You need directory access on your Google Workspace account for this to work
- Keep `token.json` safe—it contains your OAuth tokens
- Tokens refresh automatically
- Delete `token.json` to force re-authentication

## Troubleshooting

- **`403 Forbidden`**: Make sure your Google account has access to the organization directory
- **Authentication fails**: Delete `token.json` and run again
- **Slow queries**: Use `filter.py` with multi-threaded queries for faster results

## License

See LICENSE file for details.
