import os
import io
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError

SCOPES = ['https://www.googleapis.com/auth/drive.file']
FOLDER_NAME = "Memories-App"

def get_drive_service():
    creds = None
    token_path = os.path.join(os.path.dirname(__file__), 'token.json')
    creds_path = os.path.join(os.path.dirname(__file__), 'credentials.json')

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(creds_path):
                raise FileNotFoundError(f"Missing {creds_path}. Please place your Google OAuth client ID credentials here.")
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            # Use console flow since this is running on a backend
            creds = flow.run_local_server(port=0)
        with open(token_path, 'w') as token:
            token.write(creds.to_json())

    return build('drive', 'v3', credentials=creds)

def get_or_create_folder(service, folder_name, parent_id=None):
    # Check if folder exists
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
        
    try:
        response = service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        files = response.get('files', [])
        if files:
            return files[0].get('id')
        
        # Create folder
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            file_metadata['parents'] = [parent_id]
            
        file = service.files().create(body=file_metadata, fields='id').execute()
        return file.get('id')
    except HttpError as error:
        print(f"An error occurred: {error}")
        return None

def create_folder(folder_name, parent_id=None):
    """Creates a new folder in Drive, optionally under a parent_id."""
    service = get_drive_service()
    if not parent_id:
        # Default to root DocKeeper folder
        parent_id = get_or_create_folder(service, FOLDER_NAME)
    return get_or_create_folder(service, folder_name, parent_id)

def rename_file(file_id, new_name):
    """Renames a file or folder in Google Drive."""
    try:
        service = get_drive_service()
        file_metadata = {'name': new_name}
        service.files().update(fileId=file_id, body=file_metadata).execute()
        return True
    except HttpError as error:
        print(f"An error occurred during rename: {error}")
        return False

def upload_file(filename, file_data, mime_type='application/octet-stream', parent_id=None):
    """Uploads a file to Google Drive and returns the file ID."""
    try:
        service = get_drive_service()
        if not parent_id:
            parent_id = get_or_create_folder(service, FOLDER_NAME)
        
        file_metadata = {
            'name': filename,
            'parents': [parent_id] if parent_id else []
        }
        
        media = MediaIoBaseUpload(io.BytesIO(file_data), mimetype=mime_type, resumable=True)
        
        file = service.files().create(
            body=file_metadata, media_body=media, fields='id'
        ).execute()
        
        return file.get('id')
    except HttpError as error:
        print(f"An error occurred during upload: {error}")
        return None

def download_file(file_id):
    """Downloads a file from Google Drive and returns bytes."""
    try:
        service = get_drive_service()
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while done is False:
            status, done = downloader.next_chunk()
        return fh.getvalue()
    except HttpError as error:
        print(f"An error occurred during download: {error}")
        return None

def delete_file(file_id):
    """Deletes a file from Google Drive."""
    try:
        service = get_drive_service()
        service.files().delete(fileId=file_id).execute()
        return True
    except HttpError as error:
        print(f"An error occurred during delete: {error}")
        return False
