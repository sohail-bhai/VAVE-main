"""Honest connection status for the things VAVE can talk to.

Every check answers "is this actually usable right now?" so the UI can show a
truthful state. Nothing here returns a credential value - only whether one is
present - because credentials must never reach the interface or the logs.
"""

import logging
import os
import shutil
import socket
import threading
import time
from pathlib import Path

from assistant.config import get_setting

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# config.json ships with placeholders so the repo carries no real credentials.
# A placeholder is not a connection, so it must not read as one.
_PLACEHOLDER_PREFIXES = ("DEMO_", "YOUR_", "CHANGE_ME", "REPLACE_", "<")
_PLACEHOLDER_VALUES = {"demo@example.com", "you@example.com", "none", "null", "todo"}


def _is_placeholder(value):
    text = str(value).strip()
    return (text.upper().startswith(_PLACEHOLDER_PREFIXES)
            or text.lower() in _PLACEHOLDER_VALUES)


def _has_setting(key):
    value = get_setting(key, "")
    if not value or not str(value).strip():
        return False
    return not _is_placeholder(value)


def google_status():
    """Google Workspace access, as the workspace layer actually finds it.

    A token file on disk is not a connection - it can be revoked or expired -
    so this asks whether Google would answer, which is the only thing worth
    showing a person.
    """
    from assistant.workspace import auth as workspace_auth

    state = workspace_auth.connection_state()
    labels = {
        workspace_auth.LIVE: "Connected",
        workspace_auth.NEEDS_AUTHORIZATION: "Needs sign-in",
        workspace_auth.NOT_CONFIGURED: "Not connected",
    }
    return {"connected": state["state"] == workspace_auth.LIVE,
            "label": labels.get(state["state"], "Not connected"),
            "detail": state["detail"]}


def gmail_status():
    """Mail currently goes through IMAP with an app password, not OAuth."""
    if _has_setting("email_address") and _has_setting("email_app_password"):
        return {"connected": True, "label": "Connected",
                "detail": "Mail is set up for reading and sending."}
    return {"connected": False, "label": "Not connected",
            "detail": "Add your email address and app password in Settings."}


def phone_status():
    """The phone link is the Telegram bridge."""
    if _has_setting("telegram_bot_token") and _has_setting("telegram_chat_id"):
        return {"connected": True, "label": "Connected",
                "detail": "You can send VAVE commands from your phone."}
    return {"connected": False, "label": "Not connected",
            "detail": "Connect a Telegram bot to use your phone."}


def internet_status():
    """A quick reachability check, kept short so it never blocks the UI."""
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=1.5).close()
        return {"connected": True, "label": "Ready",
                "detail": "VAVE can search and read the web."}
    except OSError:
        return {"connected": False, "label": "Offline",
                "detail": "No internet connection right now."}


def local_ai_status():
    """The local brain is Ollama; report whether it is actually running."""
    model = get_setting("llm_model", "qwen2.5:3b")
    try:
        import requests
        response = requests.get("http://localhost:11434/api/tags", timeout=1.5)
        if response.status_code == 200:
            return {"connected": True, "label": "Ready",
                    "detail": f"Thinking locally with {model}."}
    except Exception:
        pass
    return {"connected": False, "label": "Not running",
            "detail": "Start Ollama to let VAVE think locally."}


def featherless_status():
    """The optional hosted brain. Off unless the switch is on and a key is set."""
    if not get_setting("featherless_enabled", False):
        return {"connected": False, "label": "Off",
                "detail": "VAVE is thinking locally. Turn this on to use Featherless."}
    if not _has_setting("featherless_api_key"):
        return {"connected": False, "label": "Needs a key",
                "detail": "Add your Featherless key in Settings to use it."}
    model = get_setting("featherless_model", "")
    return {"connected": True, "label": "On",
            "detail": f"Thinking with {model} on Featherless."}


def computer_status():
    return {"connected": True, "label": "Online",
            "detail": "This computer is ready for VAVE to use."}


def overwatch_status():
    from assistant.overwatch.engine import is_available
    if is_available():
        return {"connected": True, "label": "Available",
                "detail": "VAVE can watch windows on this computer."}
    return {"connected": False, "label": "Unavailable",
            "detail": "Watching windows needs Windows."}


def browser_automation_status():
    try:
        import playwright  # noqa: F401
        return {"connected": True, "label": "Ready",
                "detail": "VAVE can carry out steps in a browser."}
    except ImportError:
        return {"connected": False, "label": "Not installed",
                "detail": "Install Playwright to let VAVE use a browser."}


def screen_reading_status():
    """OCR needs both the Python binding and the Tesseract binary."""
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return {"connected": False, "label": "Not installed",
                "detail": "Install pytesseract to let VAVE read the screen."}

    if shutil.which("tesseract") or os.path.exists(
            r"C:\Program Files\Tesseract-OCR\tesseract.exe"):
        return {"connected": True, "label": "Ready",
                "detail": "VAVE can read what is on your screen."}
    return {"connected": False, "label": "Needs Tesseract",
            "detail": "Install the Tesseract program to enable screen reading."}


# Some of these checks talk to the network or to a local server, and they used
# to run on the UI thread while a page was being built, which froze the window
# for as long as the timeouts allowed. Answers are cached for a few seconds and
# refreshed in the background instead, so a page draws immediately from the
# last known state.
_CACHE_TTL_SECONDS = 20.0
_cache: dict = {}
_inflight: set = set()
_cache_lock = threading.Lock()


def cached(name, on_refresh=None):
    """The last known answer for one check, refreshing it in the background.

    `on_refresh` is called from the worker thread when a newer answer arrives,
    so a page can redraw itself; use gui.ui_queue to get back to the UI thread.
    """
    checker = _CHECKS.get(name)
    if checker is None:
        raise KeyError(name)

    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(name)
        fresh = entry is not None and (now - entry[0]) < _CACHE_TTL_SECONDS
        if not fresh and name not in _inflight:
            _inflight.add(name)
            start = True
        else:
            start = False

    if start:
        def _work():
            try:
                value = checker()
            except Exception:
                value = {"connected": False, "label": "Unavailable",
                         "detail": "VAVE could not check this right now."}
            with _cache_lock:
                _cache[name] = (time.monotonic(), value)
                _inflight.discard(name)
            if on_refresh:
                try:
                    on_refresh(value)
                except Exception:
                    logging.getLogger(__name__).debug(
                        "status refresh callback failed", exc_info=True)

        threading.Thread(target=_work, daemon=True).start()

    if entry is not None:
        return entry[1]
    return {"connected": False, "label": "Checking\u2026",
            "detail": "VAVE is checking this now."}


_CHECKS = {}


def all_integrations():
    """Rows for the Settings > Connected Services list."""
    return [
        ("Google", google_status()),
        ("Email", gmail_status()),
        ("Phone", phone_status()),
        ("Local AI", local_ai_status()),
        ("Featherless", featherless_status()),
        ("Internet", internet_status()),
        ("Screen reading", screen_reading_status()),
        ("Browser tasks", browser_automation_status()),
        ("Window watching", overwatch_status()),
    ]


def system_health_status():
    """Returns real-time CPU, RAM, Disk, and Battery metrics."""
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory().percent
        disk_c = shutil.disk_usage("C:\\") if os.path.exists("C:\\") else None
        c_free_gb = round(disk_c.free / (1024**3), 1) if disk_c else 0.0
        battery = psutil.sensors_battery()
        bat_pct = round(battery.percent) if battery else None
        bat_plugged = battery.power_plugged if battery else None
        return {
            "cpu": cpu,
            "ram": ram,
            "c_free_gb": c_free_gb,
            "battery_pct": bat_pct,
            "battery_plugged": bat_plugged,
        }
    except Exception:
        return {"cpu": 0, "ram": 0, "c_free_gb": 0.0, "battery_pct": None, "battery_plugged": None}


def drive_sync_status():
    """Retrieve indexed document count, chunks, and last sync timestamp."""
    try:
        from assistant.control.service import get_control_plane
        plane = get_control_plane()
        return plane.store.get_drive_sync_summary()
    except Exception:
        return {"total_files": 0, "total_chunks": 0, "last_sync_at": 0.0}


_CHECKS.update({
    "google": google_status,
    "gmail": gmail_status,
    "phone": phone_status,
    "internet": internet_status,
    "local_ai": local_ai_status,
    "featherless": featherless_status,
    "computer": computer_status,
    "overwatch": overwatch_status,
    "browser": browser_automation_status,
    "screen": screen_reading_status,
    "system_health": system_health_status,
    "drive_sync": drive_sync_status,
})
