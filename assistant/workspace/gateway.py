"""Unified Google Workspace Gateway.

Provides a single facade over Google Drive, Gmail, Calendar, Docs, Sheets and
Slides. Every answer carries whether it came from Google or from example data,
so no screen can present a demo as the real thing.
"""

import logging
from typing import Dict, Any, List, Callable

from assistant.workspace import auth
from assistant.workspace.drive import search_drive, read_drive_file, upload_drive_file, list_drive_files
from assistant.workspace.drive_indexer import indexer
from assistant.workspace.gmail import search_emails, read_email, summarize_emails, draft_email, send_email
from assistant.workspace.calendar import get_upcoming_events, format_upcoming_events_summary, create_calendar_event, detect_scheduling_conflicts
from assistant.workspace.docs_sheets import create_google_doc, append_to_google_doc, read_google_sheet, append_to_google_sheet
from assistant.workspace.slides import create_google_slides

logger = logging.getLogger(__name__)

SERVICE_LABELS = {
    "drive": "Google Drive",
    "gmail": "Gmail",
    "calendar": "Google Calendar",
    "docs": "Google Docs",
    "sheets": "Google Sheets",
    "slides": "Google Slides",
}


class WorkspaceGateway:
    """Unified Gateway coordinating Google Workspace operations."""

    def __init__(self):
        self._capabilities: Dict[str, Callable] = {
            "google.drive.search": search_drive,
            "google.drive.read": read_drive_file,
            "google.drive.write": upload_drive_file,
            "google.drive.list": list_drive_files,
            "google.drive.sync": indexer.sync_drive_index,
            "google.drive.semantic_search": indexer.semantic_search,
            "google.drive.export": indexer.export_to_drive,
            "google.gmail.search": search_emails,
            "google.gmail.read": read_email,
            "google.gmail.summary": summarize_emails,
            "google.gmail.draft": draft_email,
            "google.gmail.send": send_email,
            "google.calendar.read": get_upcoming_events,
            "google.calendar.summary": format_upcoming_events_summary,
            "google.calendar.write": create_calendar_event,
            "google.calendar.conflicts": detect_scheduling_conflicts,
            "google.docs.create": create_google_doc,
            "google.docs.append": append_to_google_doc,
            "google.sheets.read": read_google_sheet,
            "google.sheets.write": append_to_google_sheet,
            "google.slides.create": create_google_slides,
        }

    def is_connected(self) -> bool:
        """True only when Google will actually answer a call."""
        return auth.is_workspace_live()

    def get_status(self) -> Dict[str, Any]:
        """Connection state in the words a screen can show directly."""
        state = auth.connection_state()
        live = state["state"] == auth.LIVE
        return {
            "connected": live,
            "state": state["state"],
            "detail": state["detail"],
            "mode": "live" if live else "demo",
            "account": self.account_email() if live else "",
            "services": {
                key: {"status": "online" if live else "demo", "label": label}
                for key, label in SERVICE_LABELS.items()
            },
            "capabilities": list(self._capabilities.keys()),
        }

    def account_email(self) -> str:
        """Which Google account is connected, for the UI to show. Never a token."""
        # Drive's "about" carries the signed-in user, and the Drive scope is
        # already granted - asking for a userinfo scope just to show an address
        # would be a wider grant than the feature needs.
        service = auth.get_google_service("drive", "v3")
        if service is None:
            return ""
        try:
            about = service.about().get(fields="user(emailAddress)").execute()
            return about.get("user", {}).get("emailAddress", "")
        except Exception as error:
            logger.debug("Could not read the connected Google account: %s", error)
            return ""

    def list_capabilities(self) -> List[str]:
        """Returns all capability IDs supported by the Google Workspace Gateway."""
        return list(self._capabilities.keys())

    def execute_capability(self, capability: str, **kwargs) -> Any:
        """Executes a registered capability with provided keyword arguments."""
        if capability not in self._capabilities:
            raise ValueError(f"Unknown Google Workspace capability: '{capability}'. Supported: {list(self._capabilities.keys())}")

        handler = self._capabilities[capability]
        # Arguments can carry the content of a mail or a document, so log the
        # capability and the shape of the call, never the values.
        logger.info("[Workspace Gateway] Executing '%s' with %s",
                    capability, sorted(kwargs))
        return handler(**kwargs)


# Global singleton instance
gateway = WorkspaceGateway()
