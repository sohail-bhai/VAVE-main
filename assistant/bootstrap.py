"""Centralized bootstrap for VAVE safety, auditing, and event infrastructure.

Ensures that whether VAVE is launched via CLI, GUI dashboard, or API server,
the safety boundary (guard, audit, confirm, overwatch) is always initialized.
"""

import os
import atexit
from pathlib import Path
from typing import Optional

from assistant import events
from assistant import logging_setup
from assistant import audit
from assistant import guard
from assistant import confirm
from assistant import overwatch

_bootstrapped = False
_shutdown_registered = False


def bootstrap_safety(event_bus: Optional[events.EventBus] = None,
                     audit_path: Optional[Path] = None,
                     max_bytes: int = 5_000_000,
                     backup_count: int = 20,
                     start_runtime_services: bool = True) -> events.EventBus:
    """Configures the global event bus, logging, audit ledger, and safety subsystems."""
    global _bootstrapped, _shutdown_registered

    bus = event_bus or events.get_global_event_bus()
    if bus is None:
        bus = events.EventBus(maxsize=2000)
        events.set_global_event_bus(bus)

    logging_setup.configure_logging(event_bus=bus)

    if audit_path is not None:
        log_path = Path(audit_path)
    else:
        data_dir_env = os.environ.get("VAVE_DATA_DIR")
        if data_dir_env:
            log_path = Path(data_dir_env) / "audit.jsonl"
        else:
            log_path = Path("logs/audit.jsonl")

    audit.configure(log_path, max_bytes=max_bytes, backup_count=backup_count)
    guard.configure(event_bus=bus)
    confirm.configure(event_bus=bus)
    overwatch.configure(event_bus=bus)

    # The health sweep only touches a control plane that already exists and
    # sleeps before its first run, so short-lived processes pay nothing and a
    # sweep failure can never block startup.
    if start_runtime_services:
        try:
            from assistant import health_sweep
            health_sweep.start()
        except Exception:
            pass

    # Runtime supervisor is intentionally lightweight and sleeps before its
    # first check. Full microphone/network probes run only via `vave doctor`.
    if start_runtime_services:
        try:
            from assistant.reliability import supervisor

            supervisor.start()
        except Exception:
            pass

    if not _shutdown_registered:
        atexit.register(shutdown_runtime)
        _shutdown_registered = True

    _bootstrapped = True
    return bus


def is_bootstrapped() -> bool:
    """Returns True if the safety subsystem has been configured."""
    return _bootstrapped


def resume_interrupted_tasks() -> int:
    """Resume work left running by a previous process. Returns the count.

    Long-lived entry points (voice loop, GUI dashboard) call this at startup;
    one-shot modes (--text, --once, --smoke-test) must not, since a resumed
    background task would outlive them pointlessly. Never raises: a resume
    failure must not block startup.
    """
    try:
        from assistant.control.executor import get_executor
        resumed = get_executor().resume_interrupted()
    except Exception as error:
        import logging
        logging.getLogger(__name__).info("Task auto-resume skipped: %s", error)
        return 0
    if resumed:
        print(f"Resuming {len(resumed)} interrupted task(s).")
    return len(resumed)


def reset_bootstrap() -> None:
    """Resets bootstrap state for testing."""
    global _bootstrapped
    try:
        from assistant.reliability import supervisor

        supervisor.reset_for_tests()
    except Exception:
        pass
    try:
        from assistant import health_sweep

        health_sweep.stop()
    except Exception:
        pass
    _bootstrapped = False


def shutdown_runtime() -> None:
    """Stop managed background workers. Safe to call repeatedly."""
    try:
        from assistant.reliability import lifecycle

        lifecycle.shutdown_all(join_timeout=5)
    except Exception:
        pass
