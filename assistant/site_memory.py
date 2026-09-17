"""What VAVE learned about a website last time.

The second visit to a site should not start from nothing. When a flow works -
"the issues list is under /-/issues", "the search box is element 3", "this one
needs a login first" - it is written down against the domain and handed back
the next time that domain is opened.

Notes are keyed by domain rather than searched by meaning, because "which site
am I on" is an exact question. A small JSON file is enough, and it works with
no model and no network.
"""

import json
import logging
import os
import tempfile
import threading
import urllib.parse
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
NOTES_PATH = PROJECT_ROOT / "data" / "site_notes.json"

# Enough to be useful on the next visit, few enough to stay in a prompt.
MAX_NOTES_PER_SITE = 8
MAX_NOTE_CHARS = 200


def domain_of(url):
    """`https://gitlab.com/group/repo/-/issues` -> `gitlab.com`."""
    text = str(url or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    host = urllib.parse.urlparse(text).netloc.lower()
    return host[4:] if host.startswith("www.") else host


class SiteMemory:
    """Per-domain notes, stored as one small JSON file."""

    def __init__(self, path=None):
        self.path = Path(path or NOTES_PATH)
        self._lock = threading.RLock()

    def _load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=str(self.path.parent),
                                            prefix=".site_notes.",
                                            suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    file.write(json.dumps(data, indent=2))
                os.replace(tmp_name, self.path)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except OSError:
            logger.exception("Could not write site notes")

    def remember(self, url, note):
        """Write down something that worked, so the next visit is faster."""
        site = domain_of(url)
        text = " ".join(str(note).split())[:MAX_NOTE_CHARS]
        if not site or not text:
            return []

        with self._lock:
            data = self._load()
            notes = [item for item in data.get(site, []) if item != text]
            notes.append(text)
            data[site] = notes[-MAX_NOTES_PER_SITE:]
            self._save(data)
            return data[site]

    def recall(self, url):
        """What is known about this domain, oldest first."""
        site = domain_of(url)
        return self._load().get(site, []) if site else []

    def forget(self, url):
        site = domain_of(url)
        with self._lock:
            data = self._load()
            removed = data.pop(site, [])
            self._save(data)
            return removed

    def sites(self):
        return sorted(self._load())

    def as_hint(self, url):
        """The notes, phrased for the model that is about to open the page."""
        notes = self.recall(url)
        if not notes:
            return ""
        lines = "\n".join(f"- {note}" for note in notes)
        return f"What you learned about {domain_of(url)} before:\n{lines}"


_memory = None
_memory_lock = threading.Lock()


def get_site_memory():
    global _memory
    with _memory_lock:
        if _memory is None:
            _memory = SiteMemory()
        return _memory


def set_site_memory(memory):
    """Used by tests, and by anything wanting its own notes file."""
    global _memory
    with _memory_lock:
        _memory = memory
        return _memory
