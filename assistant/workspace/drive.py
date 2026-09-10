"""Google Drive Gateway Engine.

Supports searching files, reading content/metadata, uploading documents,
and listing drive directories with graceful demo simulation when offline.
"""

import io
import logging
from typing import List, Dict, Any, Optional

from assistant.workspace.auth import get_google_service

logger = logging.getLogger(__name__)

# In-memory mock storage for demo mode
_INITIAL_MOCK_DRIVE_FILES: List[Dict[str, Any]] = [
    {
        "id": "mock_drive_001",
        "name": "Hackwave_Final_Presentation.pptx",
        "mimeType": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "modifiedTime": "2026-09-04T12:00:00Z",
        "size": "4251820",
        "content": "Hackwave 2026 Final Pitch Deck: AI Control Plane Architecture & Differentiators."
    },
    {
        "id": "mock_drive_002",
        "name": "System_Architecture_Specs.pdf",
        "mimeType": "application/pdf",
        "modifiedTime": "2026-09-03T18:30:00Z",
        "size": "1048576",
        "content": "Control Core specifications, Zero-Trust Safety Gate, and Agent Mesh Protocols."
    },
    {
        "id": "mock_drive_003",
        "name": "Project_Roadmap_Q3.docx",
        "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "modifiedTime": "2026-09-02T15:15:00Z",
        "size": "512000",
        "content": "Roadmap: Google Workspace Gateway, Autonomous Overwatch, and Phone Approval."
    }
]

_mock_drive_files: List[Dict[str, Any]] = [dict(f) for f in _INITIAL_MOCK_DRIVE_FILES]


def reset_mock_drive_files():
    """Resets mock drive files to pristine state for test isolation."""
    global _mock_drive_files
    _mock_drive_files = [dict(f) for f in _INITIAL_MOCK_DRIVE_FILES]


def search_drive(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Searches Google Drive files by name or full-text query."""
    service = get_google_service("drive", "v3")
    if service is not None:
        try:
            escaped_q = query.replace("'", "\\'")
            q_str = f"name contains '{escaped_q}' or fullText contains '{escaped_q}'"
            results = service.files().list(
                q=q_str,
                pageSize=limit,
                fields="files(id, name, mimeType, modifiedTime, size, webViewLink)"
            ).execute()
            files = results.get("files", [])
            logger.info(f"Google Drive search found {len(files)} files for '{query}'.")
            return files
        except Exception as e:
            logger.error(f"Google Drive API error: {e}")

    # Fallback to demo mode
    q_lower = query.lower()
    matches = [
        f for f in _mock_drive_files
        if q_lower in f["name"].lower() or q_lower in f.get("content", "").lower()
    ]
    logger.info(f"[Demo Mode] Drive search matched {len(matches)} files.")
    return [dict(f) for f in matches[:limit]]


def read_drive_file(file_id: str) -> Dict[str, Any]:
    """Retrieves file metadata and content snippet by ID."""
    service = get_google_service("drive", "v3")
    if service is not None:
        try:
            metadata = service.files().get(fileId=file_id, fields="id, name, mimeType, size").execute()
            mime = metadata.get("mimeType", "")
            content = ""
            if "google-apps.document" in mime:
                export = service.files().export(fileId=file_id, mimeType="text/plain").execute()
                content = export.decode("utf-8", errors="replace") if isinstance(export, bytes) else str(export)
            elif "google-apps.spreadsheet" in mime:
                export = service.files().export(fileId=file_id, mimeType="text/csv").execute()
                content = export.decode("utf-8", errors="replace") if isinstance(export, bytes) else str(export)
            elif "google-apps.presentation" in mime:
                export = service.files().export(fileId=file_id, mimeType="text/plain").execute()
                content = export.decode("utf-8", errors="replace") if isinstance(export, bytes) else str(export)
            elif "google-apps.folder" in mime:
                content = f"Google Drive Folder: {metadata.get('name', 'Untitled Folder')}"
            elif "google-apps." in mime:
                content = f"Google Workspace item: {metadata.get('name', 'Untitled')} ({mime})"
            else:
                try:
                    from googleapiclient.http import MediaIoBaseDownload
                    req = service.files().get_media(fileId=file_id)
                    fh = io.BytesIO()
                    downloader = MediaIoBaseDownload(fh, req)
                    done = False
                    while not done:
                        _, done = downloader.next_chunk()
                    fh.seek(0)
                    content = fh.read().decode("utf-8", errors="replace")[:4000]
                except Exception as dl_err:
                    logger.debug(f"Media download failed for {file_id}, using metadata fallback: {dl_err}")
                    content = f"Drive file: {metadata.get('name')} ({mime})"

            metadata["content"] = content
            return metadata
        except Exception as e:
            logger.error(f"Failed to read Drive file {file_id}: {e}")
            return {"id": file_id, "name": "File", "content": f"Drive file {file_id}", "error": str(e)}

    # Mock retrieval
    for f in _mock_drive_files:
        if f["id"] == file_id:
            return dict(f)
    return {"id": file_id, "name": "Unknown File", "content": "File content simulated for demo."}


def upload_drive_file(name: str, content: str, mime_type: str = "text/plain") -> Dict[str, Any]:
    """Uploads a new file to Google Drive."""
    service = get_google_service("drive", "v3")
    if service is not None:
        try:
            from googleapiclient.http import MediaInMemoryUpload
            file_metadata = {"name": name}
            media = MediaInMemoryUpload(content.encode("utf-8"), mimetype=mime_type)
            file = service.files().create(body=file_metadata, media_body=media, fields="id, name, webViewLink").execute()
            logger.info(f"Uploaded file to Google Drive: {name} (ID: {file.get('id')})")
            return file
        except Exception as e:
            logger.error(f"Failed to upload to Google Drive: {e}")
            return {"error": str(e)}

    # Mock upload
    import uuid, datetime
    new_id = f"mock_{uuid.uuid4().hex[:8]}"
    entry = {
        "id": new_id,
        "name": name,
        "mimeType": mime_type,
        "modifiedTime": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "size": str(len(content)),
        "content": content
    }
    _mock_drive_files.append(entry)
    logger.info(f"[Demo Mode] Created Drive file: {name} (ID: {new_id})")
    return dict(entry)


def list_drive_files(limit: int = 10) -> List[Dict[str, Any]]:
    """Lists most recent files in Google Drive."""
    service = get_google_service("drive", "v3")
    if service is not None:
        try:
            results = service.files().list(
                pageSize=limit,
                fields="files(id, name, mimeType, modifiedTime, size, webViewLink)"
            ).execute()
            return results.get("files", [])
        except Exception as e:
            logger.error(f"Drive list error: {e}")

    return [dict(f) for f in _mock_drive_files[:limit]]
