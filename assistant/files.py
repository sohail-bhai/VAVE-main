"""Your computer's files, reachable from your phone, without opening the lot.

The rule this module exists to enforce: a phone on the other side of the
country can reach exactly the folders you shared, and nothing else. Every path
that arrives from outside is resolved to a real location on disk and checked
against those shares before anything is opened, so `../../.ssh` and a symlink
pointing at `/etc` both fail the same way.

Sharing is opt-in. With no shares configured, the answer to every request is
"nothing is shared yet".
"""

import logging
import mimetypes
import os
import shutil
import time
import uuid
from pathlib import Path

from assistant.config import get_setting

logger = logging.getLogger(__name__)

# Names that are never listed or served, however they are asked for.
HIDDEN_NAMES = {".ssh", ".gnupg", ".aws", ".config/gcloud", "id_rsa", "id_ed25519",
                ".env", "secret.key", "control.db", "browser_profile"}

# One page of a directory. A phone cannot draw ten thousand rows anyway.
MAX_ENTRIES = 500

# Windows treats these names as devices however they are asked for: writing
# to CON prints at the console, COM1/AUX can hang the I/O thread, and a name
# with a colon is an NTFS alternate data stream. None of them is a file.
WINDOWS_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

# How many results a search will look at before giving up.
SEARCH_LIMIT = 200
SEARCH_MAX_VISITED = 20_000

# Dependency and tooling folders dwarf everything else in a walk; searching
# them wastes seconds and never finds a file the user means.
SEARCH_IGNORED_DIRS = frozenset({
    "node_modules", ".git", "venv", ".venv", "__pycache__", "dist", "build",
})


class FileAccessError(Exception):
    """The path is outside what is shared, or does not exist."""


def shares():
    """The folders you have chosen to make reachable, as real paths."""
    configured = get_setting("file_shares", []) or []
    resolved = []

    for entry in configured:
        try:
            path = Path(entry).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if path.is_dir():
            resolved.append(path)

    return resolved


def describe_shares():
    """What a client shows on its first screen."""
    return [{"name": path.name or str(path), "path": str(path)} for path in shares()]


def _is_hidden(path):
    parts = set(path.parts)
    if any(name in parts for name in HIDDEN_NAMES):
        return True
    return any(part.startswith(".") and part not in (".", "..")
               for part in path.parts)


def resolve(path):
    """Turn a requested path into a real one, or refuse it.

    Resolution happens before the check, so a symlink pointing outside a share
    is refused even though its name looked fine.
    """
    roots = shares()
    if not roots:
        raise FileAccessError(
            "Nothing is shared yet. Add folders to `file_shares` in config.json.")

    text = str(path or "").strip()
    if not text or text in (".", "/"):
        return roots[0]

    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        # A relative path names its share first - "Pictures/beach.jpg" - which
        # is what makes every share reachable rather than only the first one.
        # A path that names no share is read against the first share, so the
        # older single-share form still works.
        head, _, tail = text.replace("\\", "/").partition("/")
        base = next((root for root in roots if root.name == head), None)
        candidate = (base / tail if tail else base) if base else roots[0] / candidate

    try:
        real = candidate.resolve()
    except (OSError, RuntimeError) as error:
        raise FileAccessError(f"That path cannot be read: {error}") from error

    for root in roots:
        if real == root or root in real.parents:
            if _is_hidden(real.relative_to(root)):
                raise FileAccessError("That one is not shared.")
            return real

    raise FileAccessError("That path is outside the folders you shared.")


def share_relative(path):
    """Name a file the way a client may ask for it back.

    Every share contributes its own name as the first segment, so
    `Pictures/holiday/beach.jpg` is unambiguous across shares while never
    revealing where the share sits on disk.
    """
    for root in shares():
        if path == root:
            return root.name
        if root in path.parents:
            return f"{root.name}/{path.relative_to(root)}"
    return ""


def _entry(path, root=None):
    try:
        info = path.stat()
    except OSError:
        return None

    relative = share_relative(path)
    if not relative:
        relative = str(path.relative_to(root)) if root else path.name

    return {
        "name": path.name,
        "path": str(path),
        "relative": relative,
        "is_dir": path.is_dir(),
        "size": 0 if path.is_dir() else info.st_size,
        "modified": info.st_mtime,
        "kind": "folder" if path.is_dir() else (
            mimetypes.guess_type(path.name)[0] or "file"),
    }


def list_dir(path=""):
    """What is in a folder: folders first, then files, newest name order.

    An empty path lists the shares themselves, so a client that knows nothing
    can start at the top and walk down into any of them.
    """
    if not str(path or "").strip():
        roots = shares()
        if not roots:
            raise FileAccessError(
                "Nothing is shared yet. Add folders to `file_shares` in config.json.")
        return {"path": "", "parent": "",
                "entries": [entry for entry in (_entry(root) for root in roots)
                            if entry is not None],
                "truncated": False}

    target = resolve(path)
    if not target.is_dir():
        raise FileAccessError(f"{target.name} is a file, not a folder.")

    entries = []
    for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(),
                                                            item.name.lower())):
        if child.name.startswith("."):
            continue
        described = _entry(child, root=target)
        if described is not None:
            entries.append(described)
        if len(entries) >= MAX_ENTRIES:
            break

    return {"path": share_relative(target) or str(target),
            "parent": share_relative(target.parent),
            "entries": entries, "truncated": len(entries) >= MAX_ENTRIES}


def stat(path):
    target = resolve(path)
    described = _entry(target)
    if described is None:
        raise FileAccessError("That file has gone.")
    return described


def open_for_download(path):
    """The real path of a file that may be sent, and how to name it."""
    target = resolve(path)
    if target.is_dir():
        raise FileAccessError("That is a folder. Ask for a file inside it.")
    if not target.exists():
        raise FileAccessError("There is no file there.")

    return target, (mimetypes.guess_type(target.name)[0] or "application/octet-stream")


def search(query, path="", limit=SEARCH_LIMIT):
    """Find files by name inside the shares - what "where did I put it" means."""
    wanted = str(query or "").strip().lower()
    if not wanted:
        return []

    roots = [resolve(path)] if path else shares()
    if not roots:
        raise FileAccessError("Nothing is shared yet.")

    found, visited = [], 0
    for root in roots:
        for folder, directories, files in os.walk(root):
            directories[:] = [name for name in directories
                              if not name.startswith(".")
                              and name not in SEARCH_IGNORED_DIRS]
            for name in files:
                visited += 1
                if visited > SEARCH_MAX_VISITED:
                    return found
                if wanted in name.lower():
                    described = _entry(Path(folder) / name, root=root)
                    if described is not None:
                        found.append(described)
                    if len(found) >= limit:
                        return found

    return found


def save_upload(folder, filename, stream, overwrite=False):
    """Put a file from the phone onto the computer, inside a shared folder."""
    target_dir = resolve(folder)
    if not target_dir.is_dir():
        raise FileAccessError("Uploads go into a folder.")

    safe_name = Path(str(filename or "upload")).name
    if not safe_name or safe_name in (".", ".."):
        raise FileAccessError("That file needs a name.")
    if ":" in safe_name:
        raise FileAccessError("That file needs a name without a colon.")
    if Path(safe_name).stem.upper() in WINDOWS_RESERVED_STEMS:
        raise FileAccessError(
            f"'{safe_name}' is a reserved device name on Windows.")
    if _is_hidden(Path(safe_name)):
        raise FileAccessError("That one is not shared.")

    destination = target_dir / safe_name
    if destination.exists() and not overwrite:
        stem, suffix = destination.stem, destination.suffix
        destination = target_dir / f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"

    with open(destination, "wb") as handle:
        shutil.copyfileobj(stream, handle)

    logger.info("Received %s into %s", destination.name, target_dir)
    return _entry(destination, root=target_dir)


def make_folder(path):
    target = resolve(Path(path).parent if Path(path).name else path)
    name = Path(str(path)).name
    if not name:
        raise FileAccessError("That folder needs a name.")

    created = target / name
    created.mkdir(parents=True, exist_ok=True)
    return _entry(created, root=target)


def move(source, destination):
    """Rename or move, both ends inside what is shared."""
    from_path = resolve(source)
    leaf = Path(str(destination)).name
    if not leaf or leaf in (".", ".."):
        raise FileAccessError("That destination needs a real name.")
    to_parent = resolve(str(Path(destination).parent))
    to_path = (to_parent / leaf).resolve()
    roots = shares()
    if not any(to_path == root or root in to_path.parents
               for root in roots):
        raise FileAccessError("That path is outside the folders you shared.")

    if to_path.exists():
        raise FileAccessError("Something is already called that.")

    shutil.move(str(from_path), str(to_path))
    return _entry(to_path, root=to_parent)


def delete(path):
    """Remove a file or an empty folder. The consequential one."""
    target = resolve(path)

    if target in shares():
        raise FileAccessError("That is a shared folder itself, not something in it.")

    if target.is_dir():
        try:
            target.rmdir()
        except OSError as error:
            raise FileAccessError(
                f"{target.name} is not empty, so it was left alone.") from error
    else:
        target.unlink()

    return {"path": str(target), "deleted": True}


# -- the tools the model calls ---------------------------------------------

def list_shared_files(path=""):
    """List what is in a shared folder on this computer."""
    try:
        listing = list_dir(path)
    except FileAccessError as error:
        return str(error)

    if not listing["entries"]:
        return f"{listing['path']} is empty."

    lines = [f"{'[dir] ' if item['is_dir'] else '      '}{item['name']}"
             + ("" if item["is_dir"] else f"  ({_readable_size(item['size'])})")
             for item in listing["entries"]]
    return f"{listing['path']}:\n" + "\n".join(lines[:60])


def find_shared_file(query):
    """Find a file by name in the shared folders."""
    try:
        hits = search(query)
    except FileAccessError as error:
        return str(error)

    if not hits:
        return f"Nothing shared is called anything like '{query}'."

    lines = [f"{item['path']}  ({_readable_size(item['size'])})" for item in hits[:20]]
    return f"Found {len(hits)} file(s) matching '{query}':\n" + "\n".join(lines)


def shared_folders():
    """Say which folders are reachable from the phone."""
    listed = describe_shares()
    if not listed:
        return ("No folders are shared yet. Add them to `file_shares` in "
                "config.json, or from the desktop app.")
    return "Shared folders:\n" + "\n".join(f"- {item['path']}" for item in listed)


def _readable_size(size):
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
