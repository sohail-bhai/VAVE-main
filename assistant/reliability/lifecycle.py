"""Registry for long-lived threads and their deterministic shutdown hooks."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class ManagedThread:
    name: str
    thread: threading.Thread
    stop: Optional[Callable[[], None]] = None
    registered_at: float = 0.0


_resources: Dict[str, ManagedThread] = {}
_lock = threading.RLock()


def register_thread(name, thread, stop=None):
    resource = ManagedThread(
        name=str(name),
        thread=thread,
        stop=stop,
        registered_at=time.time(),
    )
    with _lock:
        _resources[resource.name] = resource
    return thread


def unregister(name):
    with _lock:
        return _resources.pop(str(name), None) is not None


def snapshot():
    with _lock:
        resources = list(_resources.values())
    return [
        {
            "name": item.name,
            "alive": item.thread.is_alive(),
            "daemon": item.thread.daemon,
            "thread_name": item.thread.name,
            "registered_at": item.registered_at,
        }
        for item in resources
    ]


def shutdown_all(join_timeout=5.0):
    """Ask every managed service to stop, then join its worker thread."""
    with _lock:
        resources = list(reversed(list(_resources.values())))
    for item in resources:
        if item.stop is None:
            continue
        try:
            item.stop()
        except Exception:
            logger.debug("Could not stop managed resource %s", item.name, exc_info=True)
    current = threading.current_thread()
    for item in resources:
        if item.thread is current or not item.thread.is_alive():
            continue
        try:
            item.thread.join(timeout=max(0.0, float(join_timeout)))
        except Exception:
            logger.debug("Could not join managed resource %s", item.name, exc_info=True)
    with _lock:
        for item in resources:
            if not item.thread.is_alive():
                _resources.pop(item.name, None)
    return snapshot()


def clear_for_tests():
    with _lock:
        _resources.clear()
