"""Background health sweep: mark quiet agents and devices offline on a timer.

Agent and device health is otherwise swept on read only, so a client that
never lists them sees stale "online" rows. This daemon thread calls the same
`sweep()` the read paths use, at a fixed interval.

The sweep never creates a control plane: it only touches one that already
exists. Short-lived processes (one-shot CLI, tests) therefore pay nothing —
the loop also sleeps first, so a process that exits before the first interval
never sweeps at all. Every failure is swallowed and logged at debug level;
the sweep must never take anything else down with it.
"""

import logging
import threading

logger = logging.getLogger(__name__)

_thread = None
_lock = threading.Lock()
_stop = threading.Event()


def _loop(interval_seconds):
    while not _stop.wait(interval_seconds):
        try:
            from assistant.control import service
            plane = service._control_plane
            if plane is None:
                continue
            plane.sweep()
        except Exception:
            logger.debug("Health sweep skipped.", exc_info=True)


def start(interval_seconds=None):
    """Start the sweep thread once and return True. 0 disables and returns False."""
    global _thread
    if interval_seconds is None:
        from assistant.config import get_setting
        try:
            interval_seconds = float(get_setting("health_sweep_seconds", 60))
        except (TypeError, ValueError):
            interval_seconds = 60.0
    if interval_seconds <= 0:
        return False
    with _lock:
        if _thread is not None and _thread.is_alive():
            return True
        _stop.clear()
        _thread = threading.Thread(target=_loop, args=(interval_seconds,),
                                   daemon=True, name="vave-health-sweep")
        _thread.start()
        return True


def stop():
    """Stop the sweep thread. Tests only; production threads live forever."""
    global _thread
    _stop.set()
    with _lock:
        thread, _thread = _thread, None
    if thread is not None:
        thread.join(timeout=5)


def running():
    """True while a live sweep thread exists."""
    with _lock:
        return _thread is not None and _thread.is_alive()
