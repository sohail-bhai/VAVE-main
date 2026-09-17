import logging
logger = logging.getLogger(__name__)

import os
import datetime
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    GOOGLE_CALENDAR_AVAILABLE = True
except ImportError:
    GOOGLE_CALENDAR_AVAILABLE = False
    Request = None
    Credentials = None
    InstalledAppFlow = None
    build = None
    HttpError = Exception
from assistant.speech import speak

# If modifying these scopes, delete the file token.json.
SCOPES = ["https://www.googleapis.com/auth/calendar"]

def get_calendar_service():
    """Shows basic usage of the Google Calendar API."""
    creds = None

    # Prefer the shared Workspace credentials (encrypted vault, auto refresh)
    # over this module's legacy token.json.
    try:
        from assistant.workspace import auth as workspace_auth
        creds = workspace_auth.load_saved_credentials()
    except Exception:
        creds = None

    if creds is not None:
        try:
            return build("calendar", "v3", credentials=creds)
        except Exception:
            creds = None

    # Paths for credentials and tokens
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    token_path = os.path.join(project_root, "token.json")
    creds_path = os.path.join(project_root, "credentials.json")
    
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except Exception:
            os.remove(token_path)
            
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None
                
        if not creds:
            if not os.path.exists(creds_path):
                speak("I need access to your Google Calendar. Please download your credentials dot json from the Google Cloud Console and place it in the project folder.")
                return None
            try:
                speak("A browser window will open for you to authorize Google Calendar access.")
                flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
                creds = flow.run_local_server(port=0)
            except Exception as e:
                speak("Failed to authenticate with Google Calendar.")
                logger.info(f"[Calendar Auth Error] {e}")
                return None
                
        # Save the credentials for the next run
        with open(token_path, "w") as token:
            token.write(creds.to_json())

    try:
        service = build("calendar", "v3", credentials=creds)
        return service
    except HttpError as error:
        logger.info(f"An error occurred: {error}")
        return None

def get_upcoming_events(max_results=5):
    """Retrieves upcoming events from the user's primary calendar."""
    service = get_calendar_service()
    if not service:
        return "Could not connect to Google Calendar."
        
    try:
        now = datetime.datetime.utcnow().isoformat() + "Z"  # 'Z' indicates UTC time
        speak("Checking your schedule.")
        
        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=now,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        events = events_result.get("items", [])

        if not events:
            speak("You have no upcoming events.")
            return "No upcoming events found."

        result_str = "Here is your schedule:\\n"
        for event in events:
            start = event["start"].get("dateTime", event["start"].get("date"))
            # Format time slightly for TTS reading if possible, or just return it to LLM
            result_str += f"- {event['summary']} at {start}\\n"
            
        speak(f"You have {len(events)} upcoming events.")
        return result_str
        
    except HttpError as error:
        return f"An error occurred accessing Calendar: {error}"

def schedule_event(summary, start_time_str, duration_minutes=60):
    """
    Schedules an event on the user's primary calendar.
    start_time_str should be parsable by LLM (e.g. ISO 8601 or similar context).
    For simplicity, if it fails to parse perfectly, we use a basic parse or let the LLM pass ISO.
    """
    service = get_calendar_service()
    if not service:
        return "Could not connect to Google Calendar."
        
    try:
        # We expect the LLM to provide ISO format or something clean, e.g. "2024-05-15T10:00:00-07:00"
        # If the LLM passes a loose string, we might need dateparser. 
        # But for now we trust the LLM JSON schema instructions to pass strict ISO format.
        
        # Simple validation
        from dateutil import parser
        try:
            start_time = parser.parse(start_time_str)
        except:
            return f"Failed to parse time string: {start_time_str}. Please use ISO format like YYYY-MM-DDTHH:MM:SS"
            
        end_time = start_time + datetime.timedelta(minutes=int(duration_minutes))
        
        event = {
            'summary': summary,
            'start': {
                'dateTime': start_time.isoformat(),
                'timeZone': 'UTC',
            },
            'end': {
                'dateTime': end_time.isoformat(),
                'timeZone': 'UTC',
            },
        }

        event = service.events().insert(calendarId='primary', body=event).execute()
        speak(f"Event {summary} scheduled successfully.")
        return f"Event created: {event.get('htmlLink')}"
        
    except Exception as e:
        speak("I ran into an error scheduling the event.")
        return f"Error scheduling event: {e}"
