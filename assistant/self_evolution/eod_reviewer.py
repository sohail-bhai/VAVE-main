"""End-of-Day (EOD) Reviewer for VAVE Capability Gaps."""

from typing import Any, Dict

from assistant.self_evolution.gaps import (
    CapabilityGap,
    get_capability_gap,
    list_capability_gaps,
    update_gap_status,
)

def generate_eod_summary() -> str:
    """Generate an actionable End-of-Day summary of pending capability gaps."""
    pending = list_capability_gaps(status="pending")
    if not pending:
        return "🌙 *End-of-Day Self-Evolution Report*\n\nNo pending capability gaps logged today! All systems operating at full capacity."

    lines = [
        "🌙 *End-of-Day Self-Evolution Review*",
        f"Found *{len(pending)}* capability gap(s) logged during operations today:",
        "",
    ]

    for idx, gap in enumerate(pending, 1):
        lines.append(f"*{idx}. {gap.missing_capability}* (`{gap.id}`)")
        lines.append(f"• *Trigger Task*: {gap.task_description}")
        lines.append(f"• *Limitation*: {gap.reason}")
        lines.append(f"• *Proposed Blueprint*: {gap.proposed_solution}")
        lines.append(f"• *To Approve*: `/approve_gap {gap.id}`")
        lines.append("")

    lines.append("Reply with `/approve_gap <id>` or `/approve_all_gaps` to trigger autonomous implementation and verification.")
    return "\n".join(lines)


def approve_gap(gap_id: str) -> Dict[str, Any]:
    """Approve a gap for self-evolution."""
    gap = get_capability_gap(gap_id)
    if not gap:
        return {"ok": False, "error": f"Capability gap '{gap_id}' not found."}
    if gap.status not in ("pending", "failed"):
        return {"ok": False, "error": f"Gap '{gap_id}' is already {gap.status}."}

    changed = update_gap_status(
        gap_id,
        status="approved",
        expected_statuses=[gap.status],
    )
    if not changed:
        return {
            "ok": False,
            "error": f"Gap '{gap_id}' changed while approval was being recorded.",
        }
    updated = get_capability_gap(gap_id)
    return {
        "ok": True,
        "gap": updated.to_dict() if updated else {**gap.to_dict(), "status": "approved"},
        "message": f"Gap '{gap_id}' approved for autonomous implementation.",
    }


def reject_gap(gap_id: str, reason: str = "") -> Dict[str, Any]:
    """Reject a gap so it won't be implemented."""
    gap = get_capability_gap(gap_id)
    if not gap:
        return {"ok": False, "error": f"Capability gap '{gap_id}' not found."}
    if gap.status not in ("pending", "approved", "failed"):
        return {
            "ok": False,
            "error": f"Gap '{gap_id}' cannot be rejected while status is '{gap.status}'.",
        }

    changed = update_gap_status(
        gap_id,
        status="rejected",
        implementation_log=f"Rejected by user. {reason}".strip(),
        expected_statuses=[gap.status],
    )
    if not changed:
        return {
            "ok": False,
            "error": f"Gap '{gap_id}' changed while rejection was being recorded.",
        }
    updated = get_capability_gap(gap_id)
    return {
        "ok": True,
        "gap": updated.to_dict() if updated else {**gap.to_dict(), "status": "rejected"},
        "message": f"Gap '{gap_id}' rejected.",
    }
