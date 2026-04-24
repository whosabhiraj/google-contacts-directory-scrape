from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import os.path
import sqlite3

# If modifying these scopes, delete the file token.json.
SCOPES = ["https://www.googleapis.com/auth/directory.readonly"]


def wrap_error(func):
    """
    Used to ensure other function execution doesnt stop due to occurence of an exception
    Thanks a lot: https://stackoverflow.com/a/40102885/21615084
    """
    def func_wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            print(e)
    return func_wrapper


def authenticate() -> None:
    """
    Authenticate user using Google OAuth
    """

    creds = None
    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        print("User not authenticated... Authentication flow Started...")
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=8080)

        # Save the credentials for the next run
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    if creds.valid:
        print("User authenticated")

    try:
        service = build("people", "v1", credentials=creds)

        fetch_directory_contacts(service)

    except Exception as err:
        print("Error: ", err)


@wrap_error
def fetch_directory_contacts(service) -> None:
    """
    Fetch all the contacts from Directory
    """
    finished: bool = False
    next_page_token: str = None
    data: list = []
    count: int = 0

    print("Fetching Directory Contacts...", end='\r')

    while not finished:

        # Fetch results of listing directory
        results = service.people().listDirectoryPeople(
            readMask='names,emailAddresses,externalIds',
            pageSize=1000,
            sources="DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE",
            pageToken=next_page_token,
        ).execute()

        result = results.get('people', [])

        count += len(result)  # Increment the count of records fetched
        print(f"Fetching Directory Contacts {count}...", end='\r')

        for person in result:
            # Store email addresses (if multiple) in a list after fetching from dict
            emailAddresses: list = person.get("emailAddresses", [])
            names: list = person.get("names", [])
            externalIds: list = person.get("externalIds", [])
            
            name = names[0].get("displayName", "N/A") if names else "N/A"
            emp_id = "N/A"
            
            for ext_id in externalIds:
                if ext_id.get('type') in ['organization', 'employee', 'account']:
                    emp_id = ext_id.get('value', 'N/A')
                    break

            # Check if the value exists
            if emailAddresses:
                for email in emailAddresses:  # Iterate over lists
                    data.append((name, email['value'].strip(), emp_id))

        # Fetch the next page of results (1k)
        next_page_token = results.get('nextPageToken')

        # If no more results then exit the while loop
        finished = not next_page_token

    conn = sqlite3.connect("directory.sqlite3")
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS contacts (
            name TEXT,
            email TEXT UNIQUE,
            employee_id TEXT
        )
    ''')
    cursor.executemany('''
        INSERT OR REPLACE INTO contacts (name, email, employee_id)
        VALUES (?, ?, ?)
    ''', data)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    authenticate()