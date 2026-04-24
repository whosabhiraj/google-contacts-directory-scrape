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

For searching large datasets (380k+ contacts), use `filter.py` with multi-threaded queries:

```python
queries = [<many queries>]
```

Much faster than running queries one at a time.

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
