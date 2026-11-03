"""VAVE Smart Project Manager: Vision Specification, Delegation, and Critique Loop."""

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from assistant.config import get_setting
from assistant.project_manager.spec import (
    EvaluationResult,
    ManagerTask,
    VisionSpec,
)

logger = logging.getLogger(__name__)

_MAX_MANAGER_LOOPS = 10
_MAX_HISTORY_OUTPUT = 8000
_MANAGER_LOCK = threading.Lock()


def _stop_requested() -> bool:
    try:
        from assistant import guard

        return guard.is_killed()
    except Exception:
        return False


def is_manager_busy() -> bool:
    """Whether another managed project currently owns execution."""
    return _MANAGER_LOCK.locked()


def _audit_project(goal: str, project_path: str, **fields: Any) -> None:
    try:
        from assistant import audit, call_context

        record = {
            "event": "managed_project",
            "origin": call_context.get_origin(),
            "goal": str(goal or "")[:200],
            "project": os.path.abspath(project_path or "."),
            **fields,
        }
        audit.append(audit._redact_value(record))
    except Exception:
        pass

SPEC_SYNTHESIZER_PROMPT = """You are an Elite AI Project Manager and Chief Architect.
Analyze the project goal and generate an exacting Vision Specification.
Output ONLY a JSON object with this exact schema:
{
  "summary": "High-level technical architectural summary of what needs to be created or refactored",
  "acceptance_criteria": [
    "Criterion 1 (specific and verifiable)",
    "Criterion 2"
  ],
  "test_command": "Command to run tests (e.g. 'pytest tests/test_app.py' or 'python -m unittest tests/test_feature.py')",
  "tasks": [
    {
      "id": "task_1",
      "engine": "antigravity" | "opencode" | "openclaw" | "vave_swarm",
      "prompt": "Detailed instruction for the assigned coding tool",
      "target_files": ["relative/path/to/file.py"]
    }
  ]
}
Engine Selection Policy (Division of Labor):
- LOCAL GPU MODELS (Qwen2.5-14B / DeepSeek-R1-14B): Sole role is MANAGER BRAIN. They plan, critique, evaluate tests, and route. They DO NOT write code.
- "antigravity" (PRIMARY CODING ENGINE): High limits, Google DeepMind models (Flash/Pro), top-tier code generation. Default engine for all code writing, scripts, features, and tests. Always assign coding tasks to antigravity by default.
- "opencode" (RESTRICTED - PAID MODEL): STRICTLY RESERVED for hyper-complex full-stack scaffolding or when explicitly commanded by user. Never use for standard/routine tasks.
- "openclaw": Web automation, scraping, and multi-channel messaging.
- "vave_swarm": Internal fallback multi-agent pipeline.
No markdown wrappers, no commentary - only the JSON object.
"""

CRITIQUE_PROMPT = """You are a Strict Code Reviewer and Quality Assurance Lead.
Compare the executed project deliverables and test results against the Vision Specification.
Reply with ONLY a JSON object:
{
  "approved": true | false,
  "remarks": "Specific, actionable critique and guidance explaining exactly what is missing, broken, or needs rework.",
  "missing_criteria": ["unfulfilled criterion 1", "unfulfilled criterion 2"]
}
If all acceptance criteria are met and tests passed, set approved to true.
"""


class ProjectManager:
    """Orchestrates multi-tool software development with iterative review loops."""

    def __init__(
        self,
        llm_caller: Optional[Callable[[List[Dict[str, str]], str], str]] = None,
        test_executor: Optional[Callable[[str, str], Dict[str, Any]]] = None,
    ):
        self._llm_caller = llm_caller
        self._test_executor = test_executor or self._default_test_executor

    def _query_model(self, prompt: str, system_prompt: str = "", model: str = "") -> str:
        if self._llm_caller is not None:
            return self._llm_caller([{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}], model)

        from assistant.ai_brain import query_local_llm_chat
        chosen_model = model or get_setting("llm_model_smart", "qwen2.5:14b")
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        resp = query_local_llm_chat(messages, model=chosen_model, tools=False)
        if isinstance(resp, dict):
            return str(resp.get("content", "") or "")
        return str(resp or "")

    def _default_test_executor(self, command: str, cwd: str) -> Dict[str, Any]:
        """Run model-proposed tests through coding workflow safety checks."""
        if not command:
            return {"ok": True, "output": "No test command specified.", "exit_code": 0}

        try:
            from assistant.coding_workflow import run_test_command

            result = run_test_command(command, cwd=cwd)
            return {
                "ok": bool(result.get("passed")),
                "output": str(result.get("output", "")),
                "exit_code": result.get("exit_code", -1),
                "blocked": str(result.get("blocked", "")),
            }
        except Exception as exc:
            return {
                "ok": False,
                "output": f"Test execution failed: {exc}",
                "exit_code": -1,
            }

    def synthesize_spec(self, goal: str, project_path: str = ".") -> VisionSpec:
        """Create a structured VisionSpec from a high-level goal."""
        logger.info("ProjectManager: Synthesizing Vision Spec for: %s", goal[:80])
        raw = self._query_model(
            prompt=f"Project Goal: {goal}\nWorking Directory: {project_path}",
            system_prompt=SPEC_SYNTHESIZER_PROMPT,
        )

        data = self._extract_json(raw)
        if not data or not isinstance(data, dict):
            # Fallback spec if model output is malformed
            return VisionSpec(
                goal=goal,
                summary=f"Automated execution plan for {goal}",
                acceptance_criteria=[f"Implement {goal} successfully", "Pass automated tests"],
                test_command="",
                tasks=[
                    ManagerTask(
                        id="primary_task",
                        engine="vave_swarm",
                        prompt=goal,
                    )
                ],
            )

        data["goal"] = goal
        spec = VisionSpec.from_dict(data)
        if not spec.summary:
            spec.summary = f"Automated execution plan for {goal}"
        if not spec.acceptance_criteria:
            spec.acceptance_criteria = [f"Implement {goal} successfully", "Pass automated tests"]
        if not spec.tasks:
            spec.tasks = [
                ManagerTask(
                    id="primary_task",
                    engine="vave_swarm",
                    prompt=goal,
                )
            ]
        seen_ids = set()
        for index, task in enumerate(spec.tasks, 1):
            base_id = re.sub(r"[^A-Za-z0-9_-]+", "_", task.id).strip("_")
            base_id = base_id[:60] or f"task_{index}"
            task_id = base_id
            suffix = 2
            while task_id in seen_ids:
                task_id = f"{base_id[:54]}_{suffix}"
                suffix += 1
            seen_ids.add(task_id)
            task.id = task_id
            task.prompt = task.prompt or goal
            task.status = "pending"
            task.result = ""
        return spec

    @staticmethod
    def _finish_delegation(
        task: ManagerTask,
        ok: bool,
        output: str = "",
        error: str = "",
    ) -> str:
        """Record an engine's real result while keeping public output textual."""
        fallback = "Task completed successfully." if ok else "Task failed without details."
        message = str(output or (error if not ok else "") or fallback).strip()
        task.result = message
        task.status = "completed" if ok else "failed"
        return message

    def delegate_task(self, task: ManagerTask, project_path: str = ".", extra_context: str = "") -> str:
        """Route task to the designated tool engine."""
        prompt = task.prompt
        if extra_context:
            prompt = f"{prompt}\n\n[MANAGER CRITIQUE FROM PREVIOUS CYCLE]\n{extra_context}"

        engine = (task.engine or "antigravity").lower().strip()
        logger.info("ProjectManager: Delegating '%s' to engine '%s'", task.id, engine)
        task.status = "running"

        if engine in ("antigravity", "agy"):
            from assistant.integrations.antigravity import is_antigravity_available, run_antigravity_task
            if is_antigravity_available():
                res = run_antigravity_task(prompt, directory=project_path)
                return self._finish_delegation(
                    task,
                    bool(res.get("ok")),
                    str(res.get("output", "")),
                    str(res.get("error", "Antigravity task failed.")),
                )
            logger.warning("Antigravity CLI not available, falling back to vave_swarm")

        if engine == "opencode":
            from assistant.config import get_setting
            only_complex = get_setting("opencode_only_for_complex", True)
            is_complex = any(k in prompt.lower() for k in [
                "complex", "full stack", "scaffold", "boilerplate", "opencode",
                "microservice", "large refactor", "architecture", "enterprise"
            ])
            if only_complex and not is_complex:
                logger.info(
                    "ProjectManager: Task '%s' is standard/routine. Preserving paid OpenCode quota and diverting to free local vave_swarm.",
                    task.id,
                )
                engine = "vave_swarm"
            else:
                from assistant.integrations.opencode import is_opencode_available, run_opencode
                if is_opencode_available():
                    res = run_opencode(prompt, directory=project_path)
                    return self._finish_delegation(
                        task,
                        bool(res.get("ok")),
                        str(res.get("output", "")),
                        str(res.get("error", "OpenCode execution failed.")),
                    )
                logger.warning("OpenCode not available, falling back to vave_swarm")

        if engine == "openclaw":
            from assistant.integrations.openclaw import is_openclaw_available, run_openclaw
            if is_openclaw_available():
                res = run_openclaw("agent", args=[prompt], directory=project_path)
                return self._finish_delegation(
                    task,
                    bool(res.get("ok")),
                    str(res.get("output", "")),
                    str(res.get("error", "OpenClaw execution failed.")),
                )
            logger.warning("OpenClaw not available, falling back to vave_swarm")

        # Default / vave_swarm
        from assistant.coding_workflow import run_workflow

        result = run_workflow(
            goal=prompt,
            project_path=project_path,
            max_cycles=1,
        )
        return self._finish_delegation(
            task,
            bool(result.get("ok")),
            str(result.get("report", "")),
            "VAVE coding workflow did not complete successfully.",
        )

    @staticmethod
    def _invalid_target_files(task: ManagerTask, project_path: str) -> List[str]:
        """Return targets that are absolute or escape project root."""
        root = Path(project_path).resolve()
        invalid = []
        for raw in task.target_files:
            text = str(raw or "").strip()
            if not text:
                continue
            path = Path(text)
            if path.is_absolute():
                invalid.append(text)
                continue
            try:
                (root / path).resolve().relative_to(root)
            except (OSError, ValueError):
                invalid.append(text)
        return invalid

    def evaluate(self, spec: VisionSpec, project_path: str, cycle: int = 1) -> EvaluationResult:
        """Evaluate deliverables and test command against the VisionSpec."""
        test_res = {"ok": True, "output": "", "exit_code": 0}
        if spec.test_command:
            try:
                test_res = self._test_executor(spec.test_command, project_path)
            except Exception as exc:
                test_res = {
                    "ok": False,
                    "output": f"Test executor failed: {exc}",
                    "exit_code": -1,
                }

        # Inspect deliverable files existence
        missing_deliverables = []
        for task in spec.tasks:
            for tf in task.target_files:
                if tf in self._invalid_target_files(task, project_path):
                    missing_deliverables.append(f"invalid target path: {tf}")
                    continue
                p = (Path(project_path) / tf).resolve()
                if not p.exists():
                    missing_deliverables.append(tf)

        failed_tasks = [task for task in spec.tasks if task.status == "failed"]
        problems = []
        if not bool(test_res.get("ok")):
            problems.append(
                f"Tests failed with exit code {test_res.get('exit_code')}:\n"
                f"{test_res.get('output', '')}"
            )
        if failed_tasks:
            problems.append(
                "Delegation failed: "
                + "; ".join(
                    f"{task.id}: {str(task.result)[:500]}" for task in failed_tasks
                )
            )
        if missing_deliverables:
            problems.append(f"Missing target files: {', '.join(missing_deliverables)}")

        if problems:
            remarks = "\n".join(problems)
            return EvaluationResult(
                approved=False,
                test_output=test_res.get("output", ""),
                test_passed=bool(test_res.get("ok")),
                remarks=remarks,
                missing_criteria=(
                    [f"task failed: {task.id}" for task in failed_tasks]
                    + missing_deliverables
                ),
                cycle=cycle,
            )

        # LLM semantic check against acceptance criteria
        task_evidence = [
            {
                "id": task.id,
                "engine": task.engine,
                "status": task.status,
                "target_files": task.target_files,
                "result": str(task.result)[-1000:],
            }
            for task in spec.tasks
        ]
        eval_prompt = (
            f"Goal: {spec.goal}\n"
            f"Acceptance Criteria: {json.dumps(spec.acceptance_criteria)}\n"
            f"Test Output: {test_res.get('output', 'Passed')}\n"
            f"Task Evidence: {json.dumps(task_evidence, ensure_ascii=False)}"
        )
        try:
            raw_eval = self._query_model(eval_prompt, system_prompt=CRITIQUE_PROMPT)
        except Exception as exc:
            return EvaluationResult(
                approved=False,
                test_output=test_res.get("output", ""),
                test_passed=True,
                remarks=f"Reviewer could not evaluate the result: {exc}",
                missing_criteria=["reviewer verification"],
                cycle=cycle,
            )
        data = self._extract_json(raw_eval)

        if isinstance(data, dict):
            approved = data.get("approved") is True
            remarks = str(data.get("remarks", ""))
            raw_missing = data.get("missing_criteria", [])
            if isinstance(raw_missing, str):
                raw_missing = [raw_missing]
            missing = (
                [str(item) for item in raw_missing]
                if isinstance(raw_missing, list)
                else []
            )
            if not approved and not remarks:
                remarks = "Reviewer did not explicitly approve the result."
            return EvaluationResult(
                approved=approved,
                test_output=test_res.get("output", ""),
                test_passed=True,
                remarks=remarks,
                missing_criteria=missing,
                cycle=cycle,
            )

        return EvaluationResult(
            approved=False,
            test_output=test_res.get("output", ""),
            test_passed=True,
            remarks="Reviewer returned an invalid response; approval withheld.",
            missing_criteria=["reviewer verification"],
            cycle=cycle,
        )

    def run_managed_project(self, goal: str, project_path: str = ".", max_loops: int = 3) -> Dict[str, Any]:
        """Execute one exclusive managed project, or fail fast when busy."""
        if not _MANAGER_LOCK.acquire(blocking=False):
            clean_goal = str(goal or "").strip()
            result = {
                "ok": False,
                "busy": True,
                "goal": clean_goal,
                "cycles_run": 0,
                "max_cycles": max_loops,
                "spec": {},
                "final_evaluation": {},
                "history": [],
                "summary": "Another managed project is already running.",
            }
            _audit_project(clean_goal, project_path, ok=False, busy=True, cycles=0)
            return result
        result = None
        try:
            result = self._run_managed_project(goal, project_path, max_loops)
            return result
        except Exception as exc:
            _audit_project(
                goal,
                project_path,
                ok=False,
                error=type(exc).__name__,
            )
            raise
        finally:
            _MANAGER_LOCK.release()
            if result is not None:
                _audit_project(
                    goal,
                    project_path,
                    ok=bool(result.get("ok")),
                    busy=bool(result.get("busy", False)),
                    cycles=int(result.get("cycles_run", 0) or 0),
                )

    def _run_managed_project(self, goal: str, project_path: str = ".", max_loops: int = 3) -> Dict[str, Any]:
        """Synthesize spec, delegate work, test, critique, and loop back."""
        goal = str(goal or "").strip()
        project_dir = os.path.abspath(project_path or ".")
        try:
            max_loops = max(1, min(_MAX_MANAGER_LOOPS, int(max_loops)))
        except (TypeError, ValueError):
            max_loops = 3

        if not goal or not os.path.isdir(project_dir):
            reason = (
                "No project goal was provided."
                if not goal
                else f"Project path does not exist or is not a directory: {project_dir}"
            )
            return {
                "ok": False,
                "goal": goal,
                "cycles_run": 0,
                "max_cycles": max_loops,
                "spec": {},
                "final_evaluation": {},
                "history": [],
                "summary": reason,
            }

        try:
            spec = self.synthesize_spec(goal, project_path=project_dir)
        except Exception as exc:
            logger.exception("ProjectManager: Vision synthesis failed")
            return {
                "ok": False,
                "goal": goal,
                "cycles_run": 0,
                "max_cycles": max_loops,
                "spec": {},
                "final_evaluation": {},
                "history": [],
                "summary": f"Could not synthesize project specification: {exc}",
            }

        history: List[Dict[str, Any]] = []
        last_eval: Optional[EvaluationResult] = None
        remarks = ""

        for cycle in range(1, max_loops + 1):
            if _stop_requested():
                remarks = "Project stopped by VAVE emergency stop."
                last_eval = EvaluationResult(
                    approved=False,
                    test_passed=False,
                    remarks=remarks,
                    missing_criteria=["project was cancelled"],
                    cycle=cycle,
                )
                history.append({
                    "cycle": cycle,
                    "tasks": [],
                    "evaluation": last_eval.to_dict(),
                })
                break
            logger.info("ProjectManager: Starting Cycle %d/%d for '%s'", cycle, max_loops, goal[:60])
            cycle_tasks = []

            for task in spec.tasks:
                task.status = "pending"
                task.result = ""
                if _stop_requested():
                    task_output = self._finish_delegation(
                        task,
                        False,
                        error="Task cancelled by VAVE emergency stop.",
                    )
                    cycle_tasks.append({
                        "id": task.id,
                        "engine": task.engine,
                        "status": task.status,
                        "output": task_output,
                    })
                    break
                invalid_targets = self._invalid_target_files(task, project_dir)
                if invalid_targets:
                    task_output = self._finish_delegation(
                        task,
                        False,
                        error=(
                            "Task rejected because target path escapes project root: "
                            + ", ".join(invalid_targets)
                        ),
                    )
                else:
                    try:
                        task_output = self.delegate_task(
                            task,
                            project_path=project_dir,
                            extra_context=remarks,
                        )
                        # Custom delegates used by integrations/tests may only
                        # return text. No exception plus no explicit failure is
                        # still a completed dispatch.
                        if task.status in ("pending", "running"):
                            task.status = "completed"
                            task.result = str(task_output)
                    except Exception as exc:
                        logger.exception(
                            "ProjectManager: Delegation failed for task %s", task.id
                        )
                        task_output = self._finish_delegation(
                            task,
                            False,
                            error=f"Delegation raised an error: {exc}",
                        )
                cycle_tasks.append({
                    "id": task.id,
                    "engine": task.engine,
                    "status": task.status,
                    "output": task_output[:_MAX_HISTORY_OUTPUT],
                })

            if _stop_requested():
                eval_res = EvaluationResult(
                    approved=False,
                    test_passed=False,
                    remarks="Project stopped by VAVE emergency stop.",
                    missing_criteria=["project was cancelled"],
                    cycle=cycle,
                )
            else:
                eval_res = self.evaluate(spec, project_path=project_dir, cycle=cycle)
            last_eval = eval_res

            history.append({
                "cycle": cycle,
                "tasks": cycle_tasks,
                "evaluation": eval_res.to_dict(),
            })

            if eval_res.approved:
                logger.info("ProjectManager: Cycle %d APPROVED. Goal achieved.", cycle)
                break

            logger.warning("ProjectManager: Cycle %d REJECTED. Remarks: %s", cycle, eval_res.remarks[:120])
            remarks = eval_res.remarks

        approved = last_eval.approved if last_eval else False
        return {
            "ok": approved,
            "goal": goal,
            "cycles_run": len(history),
            "max_cycles": max_loops,
            "spec": spec.to_dict(),
            "final_evaluation": last_eval.to_dict() if last_eval else {},
            "history": history,
            "summary": (
                f"Project '{goal}' succeeded in {len(history)} cycles."
                if approved
                else f"Project '{goal}' ended after {len(history)} cycles without full approval. Last issue: {remarks}"
            ),
        }

    @staticmethod
    def _extract_json(text: str) -> Optional[Any]:
        """Extract the first valid JSON object or array from model response."""
        if not text:
            return None
        text = text.strip()
        # Direct parse
        try:
            return json.loads(text)
        except Exception:
            pass

        # Match ```json ... ``` blocks
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except Exception:
                pass

        # Match { ... }
        match = re.search(r"(\{[\s\S]*\})", text)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except Exception:
                pass

        return None


def manage_coding_project(goal: str, project_path: str = ".", max_loops: int = 3) -> str:
    """Convenience tool wrapper to execute autonomous project management loop."""
    manager = ProjectManager()
    res = manager.run_managed_project(goal=goal, project_path=project_path, max_loops=max_loops)
    return json.dumps(res, indent=2)
