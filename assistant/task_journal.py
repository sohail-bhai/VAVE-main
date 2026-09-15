"""Task journal recording every high-level user request and step outcome.

Reuses assistant.audit infrastructure for append-only storage, log rotation,
thread-safety, and recursive secret redaction.
"""

import datetime
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

from assistant import audit

def record_task_attempt(
    request: str,
    origin: str = "voice",
    model: str = "",
    tools_run: Optional[List[str]] = None,
    step_count: int = 0,
    outcome: str = "completed",
    reason: str = "",
    task_id: str = "",
) -> str:
    """Records one user request outcome to the audit ledger."""
    clean_tools = [str(t) for t in (tools_run or [])]
    # Filter out arguments, preserving clean tool names only
    tool_names = [t.split("(")[0].strip() if "(" in t else t for t in clean_tools]

    entry = {
        "event": "task_journal",
        "task_id": task_id,
        "request": audit.redact(request, max_len=300),
        "origin": origin,
        "model": model,
        "tools_run": tool_names,
        "step_count": step_count,
        "outcome": outcome,  # completed, out_of_steps, error, cancelled, stopped_short
        "reason": audit.redact(reason, max_len=200) if reason else "",
    }
    return audit.append(entry)


def get_weekly_failure_summary(days: int = 7) -> Dict[str, Any]:
    """Parses task_journal records from the audit ledger and aggregates failures."""
    path = audit._audit_path
    if not path or not Path(path).exists():
        return {"total_tasks": 0, "failures_by_outcome": {}, "failures_by_tool": {}}

    cutoff = datetime.datetime.now().timestamp() - (days * 86400)
    total_tasks = 0
    failures_by_outcome: Dict[str, int] = {}
    failures_by_tool: Dict[str, int] = {}

    # Read active audit log and backups
    log_files = [Path(path)]
    for i in range(1, audit._backup_count + 1):
        bfn = Path(path).with_name(f"{Path(path).name}.{i}")
        if bfn.exists():
            log_files.append(bfn)

    for lf in log_files:
        try:
            with open(lf, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or '"task_journal"' not in line:
                        continue
                    try:
                        record = json.loads(line)
                    except Exception:
                        continue

                    if record.get("event") != "task_journal":
                        continue

                    ts = record.get("ts", 0)
                    if ts < cutoff:
                        continue

                    total_tasks += 1
                    outcome = record.get("outcome", "completed")
                    if outcome != "completed":
                        failures_by_outcome[outcome] = failures_by_outcome.get(outcome, 0) + 1
                        tools = record.get("tools_run") or []
                        failing_tool = tools[-1] if tools else "none"
                        failures_by_tool[failing_tool] = failures_by_tool.get(failing_tool, 0) + 1
        except Exception:
            pass

    return {
        "total_tasks": total_tasks,
        "failures_by_outcome": failures_by_outcome,
        "failures_by_tool": failures_by_tool,
    }


def recent_entries(limit: int = 50) -> List[Dict[str, Any]]:
    """Newest task_journal records from the ACTIVE ledger only, newest first.

    Only the live file is read (never the rotated backups), and only the tail
    is parsed, so this stays fast enough for a GUI refresh. Used by the
    desktop Activity page's Journal view.
    """
    entries: List[Dict[str, Any]] = []
    path = audit._audit_path
    if not path or not Path(path).exists():
        return entries

    try:
        with open(path, "r", encoding="utf-8") as f:
            tail = f.readlines()[-500:]
    except Exception:
        return entries

    for line in reversed(tail):
        line = line.strip()
        if not line or '"task_journal"' not in line:
            continue
        try:
            record = json.loads(line)
        except Exception:
            continue
        if record.get("event") != "task_journal":
            continue
        entries.append({
            "ts": record.get("ts", 0),
            "request": record.get("request", ""),
            "origin": record.get("origin", ""),
            "tools_run": record.get("tools_run") or [],
            "step_count": record.get("step_count", 0),
            "outcome": record.get("outcome", "completed"),
            "reason": record.get("reason", ""),
        })
        if len(entries) >= max(1, limit):
            break
    return entries
