"""Cross-process singleton locks for long-lived VAVE roles."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _runtime_dir():
    override = os.environ.get("VAVE_DATA_DIR", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "data"


def _pid_alive(pid):
    if pid <= 0:
        return False
    try:
        import psutil

        return psutil.pid_exists(pid)
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except (OSError, ProcessLookupError):
            return False


class InstanceLock:
    def __init__(self, role, directory=None):
        safe_role = "".join(
            char for char in str(role).lower() if char.isalnum() or char in "-_"
        ) or "runtime"
        self.role = safe_role
        self.directory = Path(directory) if directory else _runtime_dir()
        self.path = self.directory / f".vave-{safe_role}.pid"
        self.acquired = False
        self.owner_pid = None

    def _read_owner(self):
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return int(payload.get("pid", -1))
        except Exception:
            try:
                return int(self.path.read_text(encoding="utf-8").strip())
            except Exception:
                return -1

    def acquire(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        for _attempt in range(2):
            try:
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
            except FileExistsError:
                owner = self._read_owner()
                self.owner_pid = owner
                if owner == os.getpid():
                    return False
                if _pid_alive(owner):
                    return False
                try:
                    self.path.unlink()
                except OSError:
                    return False
                continue
            payload = json.dumps({
                "pid": os.getpid(),
                "role": self.role,
                "created_at": time.time(),
            }).encode("utf-8")
            try:
                os.write(descriptor, payload)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self.owner_pid = os.getpid()
            self.acquired = True
            return True
        return False

    def release(self):
        if not self.acquired:
            return False
        try:
            if self._read_owner() == os.getpid():
                self.path.unlink(missing_ok=True)
        finally:
            self.acquired = False
        return True

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError(
                f"VAVE role '{self.role}' is already running under PID {self.owner_pid}."
            )
        return self

    def __exit__(self, *_exc):
        self.release()
