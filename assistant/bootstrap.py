"""Centralized bootstrap for VAVE safety, auditing, and event infrastructure.

Ensures that whether VAVE is launched via CLI, GUI dashboard, or API server,
the safety boundary (guard, audit, confirm, overwatch) is always initialized.
"""

import os
from pathlib import Path
from typing import Optional

from assistant import events
from assistant import logging_setup
from assistant import audit
from assistant import guard
from assistant import confirm
from assistant import overwatch

_bootstrapped = False


def bootstrap_safety(event_bus: Optional[events.EventBus] = None,
                     audit_path: Optional[Path] = None,
                     max_bytes: int = 5_000_000,
                     backup_count: int = 20) -> events.EventBus:
    """Configures the global event bus, logging, audit ledger, and safety subsystems."""
    global _bootstrapped

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

    _bootstrapped = True
    return bus


def is_bootstrapped() -> bool:
    """Returns True if the safety subsystem has been configured."""
    return _bootstrapped


def reset_bootstrap() -> None:
    """Resets bootstrap state for testing."""
    global _bootstrapped
    _bootstrapped = False
