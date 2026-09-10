"""Google Workspace Gateway Package for VAVE."""

from assistant.workspace.gateway import WorkspaceGateway, gateway
from assistant.workspace.drive import search_drive, read_drive_file, upload_drive_file, list_drive_files
from assistant.workspace.gmail import search_emails, read_email, summarize_emails, draft_email, send_email
from assistant.workspace.calendar import get_upcoming_events, format_upcoming_events_summary, create_calendar_event, detect_scheduling_conflicts
from assistant.workspace.docs_sheets import create_google_doc, append_to_google_doc, read_google_sheet, append_to_google_sheet
from assistant.workspace.slides import create_google_slides
from assistant.workspace.auth import connection_state, authorize, disconnect, is_workspace_live
from assistant.workspace.drive_indexer import indexer, DriveSemanticIndexer

def sync_drive_index(full_reindex: bool = False, limit: int = 50):
    return indexer.sync_drive_index(full_reindex=full_reindex, limit=limit)

def semantic_search_drive(query: str, limit: int = 5, mime_type: str = None):
    return indexer.semantic_search(query=query, limit=limit, mime_type=mime_type)

def export_to_drive(name: str, content: str, mime_type: str = "text/plain"):
    return indexer.export_to_drive(name=name, content=content, mime_type=mime_type)

__all__ = [
    "WorkspaceGateway",
    "gateway",
    "search_drive",
    "read_drive_file",
    "upload_drive_file",
    "list_drive_files",
    "sync_drive_index",
    "semantic_search_drive",
    "export_to_drive",
    "indexer",
    "DriveSemanticIndexer",
    "search_emails",
    "read_email",
    "summarize_emails",
    "draft_email",
    "send_email",
    "get_upcoming_events",
    "format_upcoming_events_summary",
    "create_calendar_event",
    "detect_scheduling_conflicts",
    "create_google_doc",
    "append_to_google_doc",
    "read_google_sheet",
    "append_to_google_sheet",
    "create_google_slides",
    "connection_state",
    "authorize",
    "disconnect",
    "is_workspace_live",
]
