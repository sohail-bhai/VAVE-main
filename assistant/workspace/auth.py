"""Google Workspace authentication and service factory.

One OAuth client covers Drive, Gmail, Calendar, Docs, Sheets and Slides.

Two rules shape this module:

* **Asking is never authorizing.** Reading the connection state, or building a
  service for a request that arrived over the API, must never open a browser.
  Only `authorize()` does that, and only when a person asked for it.
* **Demo is never silent.** `connection_state()` reports exactly one of three
  states, so a screen can say which one it is showing rather than presenting
  example data as the user's own.
"""

import os
import json
import logging
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/presentations",
]

# States reported by connection_state().
NOT_CONFIGURED = "not_configured"      # no OAuth client downloaded yet
NEEDS_AUTHORIZATION = "needs_authorization"  # client present, no usable token
LIVE = "live"                          # a token that Google will accept

# Refresh tokens live encrypted in the control plane vault, never as
# plaintext JSON next to the code. Files remain as a migration source.
OAUTH_SECRET_NAME = "google_workspace_oauth"
OAUTH_SECRET_SCOPES = "google.*"

_services_cache: Dict[str, Any] = {}
_lock = threading.Lock()


def get_project_root() -> str:
    """Returns root directory of the VAVE assistant project."""
    # current file is assistant/workspace/auth.py -> parent of parent of parent
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_token_paths() -> list:
    root = get_project_root()
    return [os.path.join(root, "token_workspace.json"), os.path.join(root, "token.json")]


def get_token_path() -> str:
    # Prefer existing token file if present, else default to token_workspace.json
    for p in get_token_paths():
        if os.path.exists(p):
            return p
    return os.path.join(get_project_root(), "token_workspace.json")


def get_credentials_path() -> str:
    return os.path.join(get_project_root(), "credentials.json")


def has_client_secrets() -> bool:
    """True once credentials.json from the Google Cloud Console is in place."""
    return os.path.exists(get_credentials_path())


def has_token() -> bool:
    """True once this machine has been authorized at least once."""
    return any(os.path.exists(p) for p in get_token_paths())


def google_libraries_available() -> bool:
    try:
        import google.oauth2.credentials  # noqa: F401
        import google_auth_oauthlib  # noqa: F401
        import googleapiclient  # noqa: F401
        return True
    except ImportError:
        return False


def _get_secret_store():
    """The shared encrypted vault. Patch point for tests."""
    from assistant.control.secrets import _shared_secret_store
    return _shared_secret_store()


def _save_token_secret(token_json: str) -> bool:
    """Persist the OAuth token encrypted. Never raises; files stay fallback."""
    try:
        store = _get_secret_store()
        store.put(OAUTH_SECRET_NAME, token_json,
                  description="Google Workspace OAuth token (auto refresh).",
                  allowed_capabilities=OAUTH_SECRET_SCOPES)
        return True
    except Exception as error:
        logger.debug("Could not store Google token in vault: %s", error)
        return False


def _load_token_secret() -> Optional[str]:
    """The vaulted OAuth token JSON, or None."""
    try:
        store = _get_secret_store()
        if not store.has(OAUTH_SECRET_NAME):
            return None
        return store.reveal(OAUTH_SECRET_NAME, capability="*")
    except Exception as error:
        logger.debug("Could not read Google token from vault: %s", error)
        return None


def _delete_token_secret() -> None:
    try:
        _get_secret_store().delete(OAUTH_SECRET_NAME)
    except Exception as error:
        logger.debug("Could not delete Google token from vault: %s", error)


def load_saved_credentials(refresh: bool = True):
    """Load the stored token. Never prompts, never opens a browser.

    The encrypted vault is home now; legacy token files are a one-time
    migration source. Returns valid credentials, or None when the user
    still has to authorize.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        logger.warning("Google auth libraries not available.")
        return None

    creds = _creds_from_json(_load_token_secret())
    if creds is not None and creds.valid:
        return creds
    if refresh and creds is not None and creds.expired \
            and creds.refresh_token:
        refreshed = _try_refresh(creds, Request())
        if refreshed is not None:
            return refreshed

    # Legacy path: plaintext token files from before the vault.
    token_path = get_token_path()
    if not os.path.exists(token_path):
        return None

    try:
        with open(token_path, "r", encoding="utf-8") as handle:
            file_json = handle.read()
        creds = _creds_from_json(file_json)
    except Exception as error:
        logger.warning("Stored Google token is unreadable: %s", error)
        return None

    if creds is not None and creds.valid:
        _save_token_secret(file_json)
        return creds

    if refresh and creds is not None and creds.expired \
            and creds.refresh_token:
        refreshed = _try_refresh(creds, Request())
        if refreshed is not None:
            return refreshed

    return None


def _creds_from_json(token_json):
    """Credentials from stored JSON, or None when there is nothing usable."""
    if not token_json:
        return None
    try:
        from google.oauth2.credentials import Credentials
        return Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
    except Exception as error:
        logger.warning("Stored Google token is unreadable: %s", error)
        return None


def _try_refresh(creds, request):
    """Refresh in place, persisting to vault and file. None on failure."""
    try:
        creds.refresh(request)
    except Exception as error:
        logger.warning("Could not refresh Google credentials: %s", error)
        return None
    try:
        _save_token_secret(creds.to_json())
    except Exception as error:
        logger.debug("Could not vault refreshed Google token: %s", error)
    _write_token(creds)
    return creds


def _write_token(creds) -> None:
    token_path = os.path.join(get_project_root(), "token_workspace.json")
    if os.path.exists(os.path.join(get_project_root(), "token.json")):
        token_path = get_token_path()
    with open(token_path, "w", encoding="utf-8") as handle:
        handle.write(creds.to_json())
    try:
        os.chmod(token_path, 0o600)
    except OSError:
        pass


def connection_state() -> Dict[str, str]:
    """What Google access this machine actually has, in one call.

    Cheap enough to call from a screen refresh: it reads files and, at most,
    refreshes an expired token. It never starts an authorization flow.
    """
    if not google_libraries_available():
        return {
            "state": NOT_CONFIGURED,
            "detail": "Google libraries are not installed. "
                      "Run: pip install -r requirements.txt",
        }

    if not has_client_secrets() and not has_token():
        return {
            "state": NOT_CONFIGURED,
            "detail": "Add credentials.json from the Google Cloud Console to "
                      "the VAVE folder, then connect.",
        }

    if load_saved_credentials() is not None:
        return {"state": LIVE, "detail": "Connected to your Google account."}

    return {
        "state": NEEDS_AUTHORIZATION,
        "detail": "Sign in to Google once to finish connecting.",
    }


def is_workspace_live() -> bool:
    """True only when Google will actually answer. Not "a file exists"."""
    return connection_state()["state"] == LIVE


def is_workspace_connected() -> bool:
    """Kept for callers that only ask whether Google is usable right now."""
    return is_workspace_live()


def authorize(open_browser: bool = True) -> Dict[str, str]:
    """Run the OAuth consent flow. Blocks until the person finishes it.

    Only call this from an explicit user action - a button, or a CLI command -
    never from a request handler, which would hang the API on a browser.
    """
    if not google_libraries_available():
        return {"state": NOT_CONFIGURED,
                "detail": "Google libraries are not installed."}

    if not has_client_secrets():
        return {"state": NOT_CONFIGURED,
                "detail": "credentials.json is missing. Download the OAuth "
                          "client from the Google Cloud Console first."}

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        return {"state": NOT_CONFIGURED,
                "detail": "google-auth-oauthlib is not installed."}

    try:
        flow = InstalledAppFlow.from_client_secrets_file(get_credentials_path(), SCOPES)
        # access_type=offline + prompt=consent is what guarantees a refresh
        # token: without it Google may hand back an access token only, and the
        # connection silently dies an hour later. (The loopback exchange
        # itself uses PKCE inside google-auth-oauthlib.)
        creds = flow.run_local_server(
            port=0, open_browser=open_browser,
            access_type="offline", prompt="consent")
        _write_token(creds)
        _save_token_secret(creds.to_json())
    except Exception as error:
        logger.error("Google authorization failed: %s", error)
        return {"state": NEEDS_AUTHORIZATION, "detail": f"Sign-in failed: {error}"}

    with _lock:
        _services_cache.clear()
    return {"state": LIVE, "detail": "Connected to your Google account."}


def disconnect() -> Dict[str, str]:
    """Forget the token on this machine. The Google account is untouched."""
    _delete_token_secret()
    for path in get_token_paths():
        try:
            os.remove(path)
        except OSError:
            pass
    with _lock:
        _services_cache.clear()
    return connection_state()


def get_google_service(service_name: str, version: str):
    """Returns a Google API client, or None when Google is not connected.

    Never interactive: a caller that gets None falls back to demo data and has
    to say so.
    """
    cache_key = f"{service_name}_{version}"
    with _lock:
        cached = _services_cache.get(cache_key)
    if cached is not None:
        return cached

    creds = load_saved_credentials()
    if not creds:
        return None

    try:
        from googleapiclient.discovery import build
        service = build(service_name, version, credentials=creds,
                        cache_discovery=False)
    except Exception as error:
        logger.error("Failed to build Google service %s (%s): %s",
                     service_name, version, error)
        return None

    with _lock:
        _services_cache[cache_key] = service
    return service


def get_oauth_credentials():
    """Older name kept for existing callers. Non-interactive."""
    return load_saved_credentials()
