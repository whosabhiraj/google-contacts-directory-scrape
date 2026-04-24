# Google Contacts Email Scraper

This is a fork of [Google Contacts Email Scraper](https://github.com/aryanranderiya/GoogleContactsEmailScraper).

## Functionality

- Fetches email addresses, name, employee ID from organization directory contacts.
- Exports results to SQLite db for easy reuse.

## How to Use

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Configure Google Cloud OAuth credentials for the People API.

3. Ensure your OAuth scopes include:
- `https://www.googleapis.com/auth/contacts.readonly`
- `https://www.googleapis.com/auth/directory.readonly`

4. Save the credentials as credentials.json

5. Run the script:

   ```bash
   python app.py
   ```

6. Complete browser authentication on first run.

7. Run the script again: 

   ```bash
   python app.py
   ```

6. Check generated db in the project directory:
- `directory.sqlite3`

## Notes

- `token.json` is created after successful authentication.
- Directory export works only if your Google account has access to an organization directory.
