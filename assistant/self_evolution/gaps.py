"""Capability gap recording and persistence for VAVE Self-Evolution."""

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_GAPS_FILE = os.path.join("data", "capability_gaps.json")
# Kept public for backward compatibility and test overrides. At runtime the
# default resolves through VAVE_DATA_DIR instead of depending on process cwd.
GAPS_FILE = _DEFAULT_GAPS_FILE

VALID_STATUSES = frozenset({
    "pending",
    "approved",
    "implementing",
    "rejected",
    "implemented",
    "failed",
})

_GAPS_LOCK = threading.RLock()


def _redacted_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    try:
        from assistant import audit

        text = str(audit._redact_value(text))
    except Exception:
        pass
    return text[:limit]


@dataclass
class CapabilityGap:
    id: str
    timestamp: float
    task_description: str
    missing_capability: str
    reason: str
    proposed_solution: str
    status: str = "pending"
    implementation_log: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CapabilityGap":
        status = str(data.get("status", "pending") or "pending").lower().strip()
        if status not in VALID_STATUSES:
            status = "pending"
        try:
            timestamp = float(data.get("timestamp", time.time()))
        except (TypeError, ValueError):
            timestamp = time.time()
        return cls(
            id=str(data.get("id", str(uuid.uuid4())[:8])),
            timestamp=timestamp,
            task_description=_redacted_text(data.get("task_description"), 2000),
            missing_capability=_redacted_text(data.get("missing_capability"), 300),
            reason=_redacted_text(data.get("reason"), 4000),
            proposed_solution=_redacted_text(data.get("proposed_solution"), 8000),
            status=status,
            implementation_log=_redacted_text(data.get("implementation_log"), 12000),
        )


def _gaps_path() -> Path:
    if GAPS_FILE != _DEFAULT_GAPS_FILE:
        return Path(GAPS_FILE)
    try:
        from assistant.memory import get_data_dir

        return Path(get_data_dir()) / "capability_gaps.json"
    except Exception:
        return Path(_DEFAULT_GAPS_FILE)


def _ensure_data_dir() -> Path:
    path = _gaps_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_gaps() -> List[CapabilityGap]:
    with _GAPS_LOCK:
        path = _ensure_data_dir()
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return [
                        CapabilityGap.from_dict(d)
                        for d in data
                        if isinstance(d, dict)
                    ]
        except Exception as exc:
            logger.error("Failed to load capability gaps from %s: %s", path, exc)
        return []


def _save_gaps(gaps: List[CapabilityGap]) -> bool:
    with _GAPS_LOCK:
        path = _ensure_data_dir()
        tmp_file = path.with_name(
            f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            data = [g.to_dict() for g in gaps]
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, path)
            return True
        except Exception as exc:
            logger.error("Failed to save capability gaps to %s: %s", path, exc)
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except Exception:
                    pass
            return False


def record_capability_gap(
    task_description: str,
    missing_capability: str,
    reason: str,
    proposed_solution: str,
) -> CapabilityGap:
    """Record a newly discovered capability gap into the registry."""
    with _GAPS_LOCK:
        gaps = _load_gaps()
        clean_task = _redacted_text(task_description, 2000)
        clean_capability = _redacted_text(missing_capability, 300)
        for existing in gaps:
            if (
                existing.status in ("pending", "approved", "implementing")
                and existing.task_description.casefold() == clean_task.casefold()
                and existing.missing_capability.casefold()
                == clean_capability.casefold()
            ):
                return existing
        gap_id = f"gap_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        gap = CapabilityGap(
            id=gap_id,
            timestamp=time.time(),
            task_description=clean_task,
            missing_capability=clean_capability,
            reason=_redacted_text(reason, 4000),
            proposed_solution=_redacted_text(proposed_solution, 8000),
            status="pending",
        )
        gaps.append(gap)
        if not _save_gaps(gaps):
            raise OSError("Could not persist capability gap registry.")
    logger.info("Recorded capability gap: %s (%s)", gap.id, gap.missing_capability)
    return gap


def list_capability_gaps(status: Optional[str] = None) -> List[CapabilityGap]:
    """Retrieve capability gaps, optionally filtered by status."""
    gaps = _load_gaps()
    if status is None:
        return gaps
    norm_status = status.lower().strip()
    return [g for g in gaps if g.status.lower() == norm_status]


def get_capability_gap(gap_id: str) -> Optional[CapabilityGap]:
    """Retrieve a single capability gap by ID."""
    gaps = _load_gaps()
    for g in gaps:
        if g.id == gap_id:
            return g
    return None


def update_gap_status(
    gap_id: str,
    status: str,
    implementation_log: str = "",
    expected_statuses: Optional[List[str]] = None,
) -> bool:
    """Atomically update status, optionally only from expected states."""
    normalized = str(status or "").lower().strip()
    if normalized not in VALID_STATUSES:
        return False
    expected = None
    if expected_statuses is not None:
        if isinstance(expected_statuses, str):
            expected_statuses = [expected_statuses]
        expected = {str(item).lower().strip() for item in expected_statuses}

    with _GAPS_LOCK:
        gaps = _load_gaps()
        found = False
        for gap in gaps:
            if gap.id != gap_id:
                continue
            if expected is not None and gap.status not in expected:
                return False
            gap.status = normalized
            if implementation_log:
                gap.implementation_log = _redacted_text(implementation_log, 12000)
            found = True
            break
        if found:
            return _save_gaps(gaps)
        return False
