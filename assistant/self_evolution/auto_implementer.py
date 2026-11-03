"""Autonomous Self-Evolution Implementation Engine.

Takes user-approved capability gaps, executes code synthesis and tests
via the ProjectManager, and verifies clean deployment into VAVE.
"""

import logging
from typing import Any, Dict

from assistant.config import PROJECT_ROOT
from assistant.project_manager.manager import ProjectManager
from assistant.self_evolution.gaps import (
    get_capability_gap,
    list_capability_gaps,
    update_gap_status,
)

logger = logging.getLogger(__name__)


def implement_capability(
    gap_id: str,
    project_path: str = None,
    max_loops: int = 3,
) -> Dict[str, Any]:
    """Execute autonomous implementation and verification for an approved capability gap."""
    gap = get_capability_gap(gap_id)
    if not gap:
        return {"ok": False, "error": f"Capability gap '{gap_id}' not found."}

    if gap.status != "approved":
        return {
            "ok": False,
            "error": (
                f"Gap '{gap_id}' must be explicitly approved before implementation "
                f"(current status: '{gap.status}')."
            ),
        }

    if not update_gap_status(
        gap_id,
        status="implementing",
        expected_statuses=["approved"],
    ):
        return {
            "ok": False,
            "error": f"Gap '{gap_id}' is already being handled by another worker.",
        }

    logger.info("AutoImplementer: Starting autonomous build for gap %s: %s", gap.id, gap.missing_capability)

    # Formulate engineering prompt for ProjectManager
    engineering_goal = (
        f"Implement new VAVE capability: '{gap.missing_capability}'.\n"
        f"Background / Triggering task: {gap.task_description}\n"
        f"Limitation / Missing feature: {gap.reason}\n"
        f"Proposed Blueprint:\n{gap.proposed_solution}\n\n"
        f"Requirements:\n"
        f"- Write clean, modular Python code.\n"
        f"- Add comprehensive unit tests in tests/.\n"
        f"- Verify all tests pass cleanly."
    )

    try:
        manager = ProjectManager()
        result = manager.run_managed_project(
            goal=engineering_goal,
            project_path=str(project_path or PROJECT_ROOT),
            max_loops=max_loops,
        )
    except Exception as exc:
        log_msg = f"Self-implementation raised an error: {exc}"
        update_gap_status(
            gap_id,
            status="failed",
            implementation_log=log_msg,
            expected_statuses=["implementing"],
        )
        logger.exception("AutoImplementer: Gap %s crashed", gap_id)
        return {
            "ok": False,
            "gap_id": gap_id,
            "status": "failed",
            "message": log_msg,
        }

    if result.get("ok"):
        log_msg = f"Successfully self-implemented in {result.get('cycles_run')} cycle(s). Summary: {result.get('summary')}"
        saved = update_gap_status(
            gap_id,
            status="implemented",
            implementation_log=log_msg,
            expected_statuses=["implementing"],
        )
        if not saved:
            return {
                "ok": False,
                "gap_id": gap_id,
                "status": "implementing",
                "message": "Implementation succeeded, but registry status could not be saved.",
                "project_result": result,
            }
        logger.info("AutoImplementer: Gap %s successfully IMPLEMENTED.", gap_id)
        return {
            "ok": True,
            "gap_id": gap_id,
            "status": "implemented",
            "message": log_msg,
            "project_result": result,
        }
    else:
        log_msg = f"Self-implementation failed after {result.get('cycles_run')} cycle(s). Last issue: {result.get('summary')}"
        update_gap_status(
            gap_id,
            status="failed",
            implementation_log=log_msg,
            expected_statuses=["implementing"],
        )
        logger.warning("AutoImplementer: Gap %s FAILED implementation.", gap_id)
        return {
            "ok": False,
            "gap_id": gap_id,
            "status": "failed",
            "message": log_msg,
            "project_result": result,
        }


def implement_all_approved_gaps(project_path: str = None, max_loops: int = 3) -> Dict[str, Any]:
    """Execute implementation across all currently approved gaps."""
    approved_gaps = list_capability_gaps(status="approved")
    if not approved_gaps:
        return {"ok": True, "count": 0, "message": "No approved gaps pending implementation."}

    results = []
    success_count = 0

    for gap in approved_gaps:
        res = implement_capability(gap.id, project_path=project_path, max_loops=max_loops)
        results.append(res)
        if res.get("ok"):
            success_count += 1

    return {
        "ok": success_count == len(approved_gaps),
        "total": len(approved_gaps),
        "successful": success_count,
        "results": results,
    }
