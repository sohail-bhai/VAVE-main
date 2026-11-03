"""VAVE Self-Evolution and Capability Gap Discovery package."""

import json
from typing import Any, Dict, Optional

from assistant.self_evolution.auto_implementer import (
    implement_all_approved_gaps,
    implement_capability,
)
from assistant.self_evolution.eod_reviewer import (
    approve_gap,
    generate_eod_summary,
    reject_gap,
)
from assistant.self_evolution.gaps import (
    CapabilityGap,
    get_capability_gap,
    list_capability_gaps,
    record_capability_gap,
    update_gap_status,
)


def record_capability_gap_tool(
    task_description: str,
    missing_capability: str,
    reason: str,
    proposed_solution: str,
) -> str:
    """Tool for AI Brain to log when it encounters an unsupported capability or failure."""
    gap = record_capability_gap(
        task_description=task_description,
        missing_capability=missing_capability,
        reason=reason,
        proposed_solution=proposed_solution,
    )
    return json.dumps({
        "status": "recorded",
        "gap_id": gap.id,
        "message": f"Recorded capability gap '{gap.missing_capability}'. It will be reviewed at End-of-Day.",
    })


def list_capability_gaps_tool(status: Optional[str] = "pending") -> str:
    """Tool to view logged capability gaps."""
    gaps = list_capability_gaps(status=status if status != "all" else None)
    return json.dumps([g.to_dict() for g in gaps], indent=2)


def approve_and_implement_gap_tool(gap_id: str) -> str:
    """Implement a gap only after approval through a user-facing channel."""
    gap = get_capability_gap(gap_id)
    if not gap:
        return json.dumps({"ok": False, "error": f"Capability gap '{gap_id}' not found."})
    if gap.status != "approved":
        return json.dumps({
            "ok": False,
            "error": (
                f"Gap '{gap_id}' requires explicit approval in the GUI or "
                "Telegram before implementation (current status: '{gap.status}')."
            ),
        })
    impl_res = implement_capability(gap_id)
    return json.dumps(impl_res, indent=2)


__all__ = [
    "CapabilityGap",
    "record_capability_gap",
    "list_capability_gaps",
    "get_capability_gap",
    "update_gap_status",
    "generate_eod_summary",
    "approve_gap",
    "reject_gap",
    "implement_capability",
    "implement_all_approved_gaps",
    "record_capability_gap_tool",
    "list_capability_gaps_tool",
    "approve_and_implement_gap_tool",
]
