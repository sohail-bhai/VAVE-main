"""Distribute coding work across registered agents, verify it, and improve.

The point of this module is not "one model writes code". It is a small
engineering team with named roles: a Planner turns the goal into file-level
tasks, Coder/TestWriter agents execute independent tasks in parallel waves,
the test runner judges the result honestly, a Reviewer checks the work against
the goal, and a Fixer iterates on real failure output.

Everything the workflow learns is written to a small lessons journal so the
next planner sees what failed before. No secrets go in the journal; lessons
are redacted.

All model calls go through `assistant.swarm.run_sub_agent`, so the guard,
audit ledger and event bus apply exactly as they do everywhere else.
"""

import json
import logging
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

from assistant import audit, events, guard
from assistant.config import get_setting
from assistant.swarm import (
    _verdict_approved,
    run_sub_agent,
    topological_levels,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The registered agents
# ---------------------------------------------------------------------------

PLANNER_PROMPT = (
    "You are a Senior Software Architect. Break the coding goal into a "
    "concrete, ordered plan of file-level tasks. Each task must be specific "
    "enough for another engineer to execute without asking questions. Output "
    "ONLY a JSON array of task objects with these fields:\n"
    '  "id": "unique snake_case id"\n'
    '  "file": "project-relative path to create or modify"\n'
    '  "action": "create" | "modify" | "delete" | "test"\n'
    '  "description": "exactly what to do in this file"\n'
    '  "needs": ["ids of tasks that must finish first"]\n'
    '  "test_command": "optional shell command that proves this task works"\n'
    "Use 'test' for tasks that write or change test files. Prefer editing "
    "existing files over rewriting them. Declare dependencies for anything "
    "that imports or changes another task's file. No markdown, no commentary "
    "- only the JSON array."
)

CODER_PROMPT = (
    "You are an expert software engineer working as one member of a small "
    "team. Implement your assigned file task exactly. You have read_file, "
    "write_file and list_directory. Work only inside "
    "the project root you were given, and only on the file assigned to you "
    "unless a dependency output says otherwise. Read the existing code before "
    "editing it. The orchestrator runs tests after your change; do not invent "
    "test results. End with a short summary of files changed and outcome."
)

TEST_WRITER_PROMPT = (
    "You are a test engineer. Write or update the tests described by the "
    "task. Put tests in the project's existing test layout and match its "
    "framework. Cover the behaviour named in the goal, including the obvious "
    "failure case. Run the tests and report the real output. Do not weaken or "
    "delete a failing test to make it pass."
)

FIXER_PROMPT = (
    "You are a debugging specialist. The tests failed; the failure output is "
    "real. Diagnose the cause, fix the code (not the test assertions, unless "
    "the test itself is wrong). You have read_file, write_file and "
    "list_directory. Work only inside the project root. The orchestrator will "
    "re-run tests after your change. Report the root cause and files changed; "
    "never invent a passing result."
)

REVIEWER_PROMPT = (
    "You are a strict code reviewer. Judge the work against the original "
    "goal, the plan, and the real test output. Check: the goal is actually "
    "met, tests pass, no test was weakened or skipped, no secrets, no "
    "destructive commands, and no claim is made without tool output. Reply "
    "with exactly one line first: APPROVED if everything passes, otherwise "
    "REJECTED followed by the concrete fixes needed."
)

# name -> what it is, when it runs, and which model tier it deserves.
AGENT_REGISTRY = {
    "planner": {
        "prompt": PLANNER_PROMPT,
        "purpose": "Turn a goal into file-level tasks with dependencies.",
        "tier": "smart",
        "phase": "plan",
    },
    "coder": {
        "prompt": CODER_PROMPT,
        "purpose": "Create or modify implementation files.",
        "tier": "smart",
        "phase": "code",
    },
    "test_writer": {
        "prompt": TEST_WRITER_PROMPT,
        "purpose": "Write or update tests for the goal.",
        "tier": "smart",
        "phase": "code",
    },
    "fixer": {
        "prompt": FIXER_PROMPT,
        "purpose": "Fix real test failures until the suite passes.",
        "tier": "smart",
        "phase": "fix",
    },
    "reviewer": {
        "prompt": REVIEWER_PROMPT,
        "purpose": "Verify the result against the goal and the evidence.",
        "tier": "smart",
        "phase": "verify",
    },
}

# Local tools/apps the workflow can call by name. They are ordinary tools in
# AVAILABLE_FUNCTIONS; this registry exists so a caller can see what is
# available without importing ai_brain.
CODING_APPS = {
    "scaffold_code": "Generate a single file from a prompt.",
    "deep_test_project": "Boot a web project and audit the UI with vision.",
    "git_auto_commit_and_push": "Commit after a successful run (commit=True).",
    "github_propose_fix": "Manual follow-up: open a pull request.",
    "gitlab_propose_fix": "Manual follow-up: open a merge request.",
}


def registered_agents():
    """Every coding agent the workflow can distribute work to."""
    return {
        name: {"purpose": info["purpose"], "phase": info["phase"]}
        for name, info in AGENT_REGISTRY.items()
    }


def registered_apps():
    return dict(CODING_APPS)


# Tools a coding agent may call. Keeping this list small is a real speed win:
# fewer tool schemas means faster local-model prefill and fewer wrong picks.
CODING_TOOL_NAMES = (
    "read_file", "write_file", "list_directory",
)


def coding_tools():
    """The tool schemas a Coder/TestWriter/Fixer may use."""
    try:
        from assistant.ai_brain import LLM_TOOLS
        allowed = set(CODING_TOOL_NAMES)
        return [tool for tool in LLM_TOOLS
                if tool.get("function", {}).get("name") in allowed]
    except Exception:
        return None


def list_coding_agents():
    """Plain-text roster of the registered agents and coding apps."""
    lines = ["Registered coding agents:"]
    for name, info in AGENT_REGISTRY.items():
        lines.append(f"  {name}: {info['purpose']}")
    lines.append("")
    lines.append("Registered coding apps:")
    for name, purpose in CODING_APPS.items():
        lines.append(f"  {name}: {purpose}")
    lines.append("")
    lines.append("Use run_coding_workflow(goal, project_path) to distribute a "
                 "coding task across them.")
    return "\n".join(lines)


def coding_workflow_stats_text():
    stats = workflow_stats()
    lines = [f"Coding workflows: {stats['runs']} run, {stats['ok']} succeeded, "
             f"{stats['failed']} incomplete."]
    if stats["top_failures"]:
        lines.append("Most common failures:")
        for item in stats["top_failures"]:
            lines.append(f"  {item['count']}x {item['reason']}")
    else:
        lines.append("No failure patterns recorded yet.")
    return "\n".join(lines)


def _model_for(tier):
    """The configured model for a tier, or None to use the default."""
    if tier == "smart":
        return str(get_setting("llm_model_smart", "") or "") or None
    return None


# ---------------------------------------------------------------------------
# Lessons journal: how the workflow improves instead of repeating mistakes
# ---------------------------------------------------------------------------

def lessons_path():
    from assistant.memory import get_data_dir
    return Path(get_data_dir()) / "coding_lessons.jsonl"


def record_lesson(lesson, path=None):
    """Append one redacted lesson. Never raises; the workflow must not die."""
    target = Path(path) if path else lessons_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            safe = audit._redact_value(lesson)
        except Exception:
            safe = lesson
        line = json.dumps(safe, ensure_ascii=False, default=str)
        # Keep the journal small: last 200 entries.
        lines = []
        if target.exists():
            with open(target, "r", encoding="utf-8") as handle:
                lines = handle.readlines()[-199:]
        lines.append(line + "\n")
        with open(target, "w", encoding="utf-8") as handle:
            handle.writelines(lines)
        return True
    except Exception as exc:
        logger.debug("Could not record coding lesson: %s", exc)
        return False


def load_lessons(limit=5, path=None):
    """The most recent lessons, newest last. Tolerates a missing/bad file."""
    target = Path(path) if path else lessons_path()
    try:
        if not target.exists():
            return []
        with open(target, "r", encoding="utf-8") as handle:
            lines = handle.readlines()[-max(1, int(limit)):]
        lessons = []
        for line in lines:
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    lessons.append(item)
            except Exception:
                continue
        return lessons
    except Exception as exc:
        logger.debug("Could not read coding lessons: %s", exc)
        return []


def workflow_stats(path=None):
    """Counts for the GUI/voice: runs, successes, and top failure reasons."""
    lessons = load_lessons(limit=200, path=path)
    runs = len(lessons)
    wins = sum(1 for item in lessons if item.get("ok"))
    reasons = {}
    for item in lessons:
        for reason in item.get("failures", []) or []:
            text = str(reason)[:120]
            reasons[text] = reasons.get(text, 0) + 1
    top = sorted(reasons.items(), key=lambda pair: pair[1], reverse=True)[:5]
    return {"runs": runs, "ok": wins,
            "failed": runs - wins,
            "top_failures": [{"reason": r, "count": c} for r, c in top]}


def _lessons_for_prompt(limit=5):
    lessons = load_lessons(limit=limit)
    if not lessons:
        return ""
    lines = ["PAST WORKFLOW LESSONS (do not repeat these mistakes):"]
    for item in lessons:
        failures = item.get("failures") or []
        detail = "; ".join(str(f)[:160] for f in failures[:3]) or "see verdict"
        lines.append(f"- Goal: {str(item.get('goal', ''))[:120]} -> {detail}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

_ALLOWED_ACTIONS = ("create", "modify", "delete", "test")


def _extract_json_array(raw):
    """Pull a JSON array out of model text, tolerating fences and prose."""
    if isinstance(raw, list):
        return raw
    text = str(raw or "").strip()
    if not text:
        return []
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    candidates = [text]
    first = text.find("[")
    last = text.rfind("]")
    if first != -1 and last > first:
        candidates.append(text[first:last + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except Exception:
            continue
        if isinstance(value, dict) and isinstance(value.get("tasks"), list):
            value = value["tasks"]
        if isinstance(value, list):
            return value
    return []


def _safe_project_path(project_path, relative):
    """A project-relative path that cannot escape the project root."""
    root = Path(project_path).resolve()
    cleaned = str(relative or "").strip().replace("\\", "/")
    if not cleaned or cleaned.startswith("/") or re.match(r"^[A-Za-z]:", cleaned):
        return None
    candidate = (root / cleaned).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return str(candidate)


_PROJECT_PATH_ARGUMENTS = {
    "read_file": "path",
    "write_file": "path",
    "list_directory": "path",
}


def _prepare_project_call(project_path):
    """Anchor coding-agent file tools to project root and reject escapes."""
    root = Path(project_path).resolve()

    def prepare(tool_name, args):
        cleaned = dict(args or {})
        key = _PROJECT_PATH_ARGUMENTS.get(tool_name)
        if key is None:
            return cleaned
        raw = cleaned.get(key, "." if tool_name == "list_directory" else "")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{tool_name} requires a project-relative path")
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"{tool_name} path escapes the project root"
            ) from exc
        cleaned[key] = str(candidate)
        return cleaned

    return prepare


def normalize_plan(raw, project_path):
    """Validate and de-duplicate planner output. Returns (plan, warnings)."""
    tasks = _extract_json_array(raw)
    plan, warnings, seen_ids = [], [], set()
    for index, item in enumerate(tasks):
        if not isinstance(item, dict):
            warnings.append(f"Task {index} is not an object; skipped.")
            continue
        task_id = str(item.get("id", "")).strip() or f"task_{index + 1}"
        task_id = re.sub(r"[^A-Za-z0-9_-]+", "_", task_id)[:60]
        while task_id in seen_ids:
            task_id = f"{task_id}_x"
        seen_ids.add(task_id)
        action = str(item.get("action", "modify")).strip().lower()
        if action not in _ALLOWED_ACTIONS:
            warnings.append(f"Task {task_id}: action {action!r} is not allowed; "
                            "treated as modify.")
            action = "modify"
        file_value = str(item.get("file", "")).strip()
        if not file_value:
            warnings.append(f"Task {task_id}: no file; skipped.")
            continue
        if _safe_project_path(project_path, file_value) is None:
            warnings.append(f"Task {task_id}: {file_value!r} escapes the "
                            "project root; skipped.")
            continue
        needs = item.get("needs", []) or []
        if isinstance(needs, str):
            needs = [needs]
        plan.append({
            "id": task_id,
            "file": file_value.replace("\\", "/"),
            "action": action,
            "description": str(item.get("description", "")).strip(),
            "needs": [str(need).strip() for need in needs if str(need).strip()],
            "test_command": str(item.get("test_command", "") or "").strip(),
        })
    # Drop dependencies that point at tasks we had to skip, so the DAG is valid.
    valid_ids = {task["id"] for task in plan}
    for task in plan:
        missing = [need for need in task["needs"] if need not in valid_ids]
        if missing:
            warnings.append(f"Task {task['id']}: dropped unknown dependencies "
                            f"{', '.join(missing)}.")
            task["needs"] = [need for need in task["needs"] if need in valid_ids]
    # Two agents must never edit the same file in the same wave, or the last
    # writer wins by luck. Chain same-file tasks in the order the planner
    # listed them.
    last_by_file = {}
    for task in plan:
        previous = last_by_file.get(task["file"])
        if previous and previous not in task["needs"]:
            task["needs"].append(previous)
        last_by_file[task["file"]] = task["id"]
    # Break cycles rather than failing the whole run: remove one edge at a
    # time, which is guaranteed to terminate (the edge set is finite).
    while True:
        _levels, error = topological_levels(plan)
        if not error:
            break
        warnings.append(f"Plan dependency error fixed: {error}")
        if "Circular" not in error:
            break
        removed = False
        for task in plan:
            if task["needs"]:
                task["needs"].pop()
                removed = True
                break
        if not removed:
            break
    return plan, warnings


def plan_work(goal, project_path=".", event_bus=None, max_steps=10,
              use_lessons=True):
    """Ask the Planner agent for a file-level plan."""
    lessons = _lessons_for_prompt() if use_lessons else ""
    task = f"Project root: {os.path.abspath(project_path)}\n\nGoal: {goal}"
    if lessons:
        task += f"\n\n{lessons}"
    output = run_sub_agent(AGENT_REGISTRY["planner"]["prompt"], task,
                           max_steps=max_steps, event_bus=event_bus,
                           agent_id="planner",
                           model=_model_for(AGENT_REGISTRY["planner"]["tier"]),
                           tools=False)
    plan, warnings = normalize_plan(output, project_path)
    return plan, warnings, output


# ---------------------------------------------------------------------------
# Test detection and execution
# ---------------------------------------------------------------------------

def detect_test_command(project_path="."):
    """A sensible test command for the project, or '' when unknown."""
    override = str(get_setting("coding_test_command", "") or "").strip()
    if override:
        return override
    root = Path(project_path)
    if (root / "package.json").exists():
        return "npm test"
    if (root / "Cargo.toml").exists():
        return "cargo test"
    if (root / "go.mod").exists():
        return "go test ./..."
    pyproject = root / "pyproject.toml"
    pytest_configured = (root / "pytest.ini").exists()
    if pyproject.exists():
        try:
            pytest_configured = pytest_configured or "[tool.pytest" in (
                pyproject.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            pass
    if pytest_configured:
        return f"{_quote_exe()} -m pytest -q"
    tests_dir = root / "tests"
    if tests_dir.is_dir() and any(tests_dir.glob("test_*.py")):
        return f"{_quote_exe()} -m unittest discover -s tests"
    if any(root.glob("test_*.py")):
        return f"{_quote_exe()} -m unittest discover"
    return ""


def _quote_exe():
    """This interpreter, quoted when its path contains spaces.

    The project can live under a path like `F drive Backup`, so an unquoted
    `C:\\...\\python.exe` is split by cmd.exe and every test command fails
    before Python starts.
    """
    return subprocess.list2cmdline([sys.executable])


def _normalize_command(command):
    """Make the command use this interpreter and stay non-interactive."""
    text = str(command or "").strip()
    if not text:
        return ""
    quoted = _quote_exe()
    if text.startswith(sys.executable):
        text = quoted + text[len(sys.executable):]
    text = re.sub(r"^(?:python3|python|py)(?=\s)", lambda _match: quoted, text)
    text = re.sub(r"^pytest(?=\s|$)", lambda _match: f"{quoted} -m pytest", text)
    return text


_SHELL_CONTROL_RE = re.compile(r"[;&|<>`\r\n]")


def _split_test_command(command):
    """Parse one command into argv without invoking a command shell."""
    if _SHELL_CONTROL_RE.search(command):
        return [], "Shell chaining and redirection are not allowed in test commands."
    try:
        argv = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        return [], f"Invalid test command quoting: {exc}"
    if os.name == "nt":
        argv = [
            value[1:-1]
            if len(value) >= 2
            and value[0] == value[-1]
            and value[0] in "\"'"
            else value
            for value in argv
        ]
    return argv, ""


def _test_runner_reason(argv):
    """Return refusal text unless argv invokes a supported test runner."""
    if not argv:
        return "Test command is empty."
    executable = Path(argv[0]).name.casefold()
    current_python = Path(sys.executable).resolve()
    try:
        is_current_python = Path(argv[0]).resolve() == current_python
    except OSError:
        is_current_python = False

    if is_current_python:
        if len(argv) >= 3 and argv[1] == "-m" and argv[2] in {
            "pytest", "unittest"
        }:
            return ""
        return "Python test commands must use '-m pytest' or '-m unittest'."
    if executable in {"npm", "npm.cmd", "npm.exe"}:
        if len(argv) >= 2 and argv[1].casefold() == "test":
            return ""
        return "Only 'npm test' is allowed as an npm test command."
    if executable in {"cargo", "cargo.exe"}:
        if len(argv) >= 2 and argv[1].casefold() == "test":
            return ""
        return "Only 'cargo test' is allowed as a Cargo test command."
    if executable in {"go", "go.exe"}:
        if len(argv) >= 2 and argv[1].casefold() == "test":
            return ""
        return "Only 'go test' is allowed as a Go test command."
    return "Test command must use pytest, unittest, npm test, cargo test, or go test."


def run_test_command(command, cwd=".", timeout=None):
    """Run one test command honestly. Returns a result dict, never raises."""
    command = _normalize_command(command)
    result = {"command": command, "passed": False, "exit_code": None,
              "output": "", "timed_out": False, "blocked": ""}
    if not command:
        result["output"] = "No test command."
        return result
    hard_denial = guard.hard_denial_reason(
        "run_terminal_command",
        {"command": command},
    )
    if hard_denial:
        result["blocked"] = hard_denial
        result["output"] = hard_denial
        return result
    dangerous, reason = guard.is_destructive_request(command)
    if dangerous:
        result["blocked"] = reason or "Blocked by the safety guard."
        result["output"] = result["blocked"]
        return result
    argv, parse_error = _split_test_command(command)
    runner_error = parse_error or _test_runner_reason(argv)
    if runner_error:
        result["blocked"] = runner_error
        result["output"] = runner_error
        return result
    if timeout is None:
        try:
            timeout = int(get_setting("coding_test_timeout_seconds", 300))
        except Exception:
            timeout = 300
    try:
        timeout = max(1, min(7200, int(timeout)))
    except (TypeError, ValueError):
        timeout = 300
    try:
        env = dict(os.environ)
        env.setdefault("CI", "1")  # stop npm/jest entering watch mode
        completed = subprocess.run(
            argv, shell=False, cwd=os.path.abspath(cwd),
            capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
            env=env)
        output = ((completed.stdout or "") + (completed.stderr or "")).strip()
        result["exit_code"] = completed.returncode
        result["passed"] = completed.returncode == 0
        result["output"] = output[-6000:]
    except subprocess.TimeoutExpired:
        result["timed_out"] = True
        result["output"] = f"Test command timed out after {timeout} seconds."
    except Exception as exc:
        result["output"] = f"Could not run test command: {exc}"
    return result


def collect_test_commands(plan, project_path, explicit=""):
    """Plan test commands first, then the project default. De-duplicated."""
    commands, seen = [], set()

    def add(value):
        cleaned = _normalize_command(value)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            commands.append(cleaned)

    add(explicit)
    for task in plan:
        add(task.get("test_command", ""))
    add(detect_test_command(project_path))
    return commands


# ---------------------------------------------------------------------------
# Work distribution
# ---------------------------------------------------------------------------

def _agent_for_task(task):
    return "test_writer" if task.get("action") == "test" else "coder"


def _task_prompt(goal, project_path, task, dependency_outputs):
    lines = [
        f"Project root: {os.path.abspath(project_path)}",
        f"Overall goal: {goal}",
        "",
        f"Assigned task: {task['id']}",
        f"File: {task['file']}",
        f"Action: {task['action']}",
        f"What to do: {task['description'] or '(use the goal)'}",
    ]
    if task.get("test_command"):
        lines.append(f"Test command: {task['test_command']}")
    if dependency_outputs:
        lines.append("")
        lines.append("Completed dependencies:")
        for dep_id, output in dependency_outputs:
            lines.append(f"- {dep_id}: {str(output)[:1500]}")
    lines.append("")
    lines.append("Work only inside the project root and on the assigned file. "
                 "Report what you changed and the real test result.")
    return "\n".join(lines)


def run_task_waves(plan, goal, project_path, event_bus=None, max_steps=15,
                   prior_results=None, retry_ids=None):
    """Run plan tasks in dependency waves, parallel inside a wave.

    `prior_results` plus `retry_ids` lets a later cycle re-run only the tasks
    that failed and the tasks that depend on them, instead of making every
    agent redo work that already landed on disk.

    Returns {"ok", "results"} where each result has ok/output/agent.
    """
    from assistant import call_context

    levels, error = topological_levels(plan)
    if error:
        return {"ok": False, "results": {}, "error": error}
    by_id = {task["id"]: task for task in plan}
    retry = set(by_id) if retry_ids is None else {
        task_id for task_id in retry_ids if task_id in by_id}
    results = {
        task_id: dict(item)
        for task_id, item in (prior_results or {}).items()
        if task_id in by_id and task_id not in retry
    }
    lock = threading.Lock()
    try:
        max_parallel = max(1, int(get_setting("coding_max_parallel_agents", 3)))
    except Exception:
        max_parallel = 3
    slots = threading.Semaphore(max_parallel)

    def worker(task_id):
        with slots:
            task = by_id[task_id]
            with lock:
                deps = [dep for dep in task["needs"] if dep in results]
                failed = [dep for dep in deps if not results[dep]["ok"]]
                dependency_outputs = [(dep, results[dep]["output"]) for dep in deps]
            if failed:
                outcome = {"ok": False, "agent": _agent_for_task(task),
                           "output": (f"Skipped: dependency '{failed[0]}' did "
                                      "not succeed.")}
            else:
                agent_name = _agent_for_task(task)
                agent = AGENT_REGISTRY[agent_name]
                output = run_sub_agent(
                    agent["prompt"],
                    _task_prompt(goal, project_path, task, dependency_outputs),
                    max_steps=max_steps, event_bus=event_bus,
                    agent_id=f"{agent_name}:{task_id}",
                    model=_model_for(agent["tier"]),
                    tools=coding_tools(),
                    prepare_call=_prepare_project_call(project_path))
                outcome = {"ok": not _is_failure(output),
                           "agent": agent_name, "output": output}
            with lock:
                results[task_id] = outcome

    for wave in levels:
        threads = []
        for task_id in wave:
            if task_id not in retry:
                continue
            thread = call_context.spawn_thread(
                target=worker, args=(task_id,),
                name=f"code-{task_id[:16]}")
            threads.append(thread)
        for thread in threads:
            thread.join()

    return {"ok": all(item["ok"] for item in results.values()),
            "results": results, "error": ""}


def retry_ids_for(plan, prior_results):
    """Tasks to run again: the failures and everything downstream of them."""
    if not prior_results:
        return {task["id"] for task in plan}
    failed = {task_id for task_id, item in prior_results.items()
              if not item.get("ok")}
    if not failed:
        return set()
    dependents = {}
    for task in plan:
        for need in task["needs"]:
            dependents.setdefault(need, set()).add(task["id"])
    retry, frontier = set(failed), list(failed)
    while frontier:
        current = frontier.pop()
        for child in dependents.get(current, ()):
            if child not in retry:
                retry.add(child)
                frontier.append(child)
    return retry


def _is_failure(output):
    text = str(output or "").strip()
    if not text:
        return True
    return (text in ("Agent failed.", "Agent reached maximum steps.")
            or text.startswith("Error:"))


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def review_work(goal, plan, task_results, test_results, event_bus=None,
                max_steps=8, cycle=1):
    evidence = {
        "goal": goal,
        "plan": plan,
        "agent_results": {
            task_id: {"agent": item.get("agent"), "ok": item.get("ok"),
                      "output": str(item.get("output", ""))[:2000]}
            for task_id, item in task_results.items()
        },
        "tests": [
            {"command": item.get("command"), "passed": item.get("passed"),
             "exit_code": item.get("exit_code"),
             "output": str(item.get("output", ""))[-2500:]}
            for item in test_results
        ],
    }
    verdict = run_sub_agent(
        AGENT_REGISTRY["reviewer"]["prompt"],
        json.dumps(evidence, indent=2, ensure_ascii=False),
        max_steps=max_steps, event_bus=event_bus,
        agent_id=f"reviewer:cycle{cycle}",
        model=_model_for(AGENT_REGISTRY["reviewer"]["tier"]),
        tools=False)
    return verdict, _verdict_approved(verdict)


def fix_failures(goal, project_path, failed_tests, task_results, event_bus=None,
                 max_steps=15, cycle=1, attempt=1):
    """One Fixer pass over the real failure output."""
    evidence = {
        "goal": goal,
        "failed_tests": [
            {"command": item.get("command"), "exit_code": item.get("exit_code"),
             "output": str(item.get("output", ""))[-4000:]}
            for item in failed_tests
        ],
        "agent_outputs": {
            task_id: str(item.get("output", ""))[:1500]
            for task_id, item in task_results.items()
        },
        "project_root": os.path.abspath(project_path),
    }
    return run_sub_agent(
        AGENT_REGISTRY["fixer"]["prompt"],
        json.dumps(evidence, indent=2, ensure_ascii=False),
        max_steps=max_steps, event_bus=event_bus,
        agent_id=f"fixer:cycle{cycle}:{attempt}",
        model=_model_for(AGENT_REGISTRY["fixer"]["tier"]),
        tools=coding_tools(),
        prepare_call=_prepare_project_call(project_path))


# ---------------------------------------------------------------------------
# The workflow
# ---------------------------------------------------------------------------

def _emit(event_bus, message, **kwargs):
    if event_bus is None:
        return
    try:
        event_bus.emit(events.EVENT_STATUS, message, scope="coding", **kwargs)
    except Exception:
        pass


def _build_report(goal, project_path, plan, cycles, ok, artifacts):
    agents = sorted({
        item.get("agent")
        for cycle in artifacts.get("cycles", [])
        for item in cycle.get("results", {}).values()
        if item.get("agent")
    })
    lines = ["=== CODING WORKFLOW REPORT ===",
             f"Goal: {goal}",
             f"Project: {os.path.abspath(project_path)}",
             f"Agents used: {', '.join(agents) or 'none'}",
             f"Cycles: {cycles}",
             f"Final: {'SUCCESS' if ok else 'INCOMPLETE'}",
             "",
             "--- Plan ---"]
    for task in plan:
        lines.append(f"  {task['id']}: {task['action']} {task['file']}")
    for cycle in artifacts.get("cycles", []):
        lines.append("")
        lines.append(f"--- Cycle {cycle['cycle']} ---")
        for task_id, item in cycle.get("results", {}).items():
            state = "OK" if item.get("ok") else "FAILED"
            lines.append(f"  {task_id} [{item.get('agent')}]: {state}")
        for test in cycle.get("tests", []):
            state = "PASS" if test.get("passed") else "FAIL"
            lines.append(f"  test {state}: {test.get('command')}")
        verdict = str(cycle.get("verdict", "")).strip().splitlines()
        if verdict:
            lines.append(f"  review: {verdict[0][:200]}")
    return "\n".join(lines)


def run_workflow(goal, project_path=".", max_cycles=3, max_fixes=2,
                 event_bus=None, test_command="", max_steps=15,
                 record=True, lessons=True):
    """Run the full Plan -> Code -> Test -> Review -> Fix loop.

    Returns {"ok", "report", "cycles", "plan", "artifacts", "warnings"}.
    """
    goal = str(goal or "").strip()
    project_path = os.path.abspath(project_path or ".")
    artifacts = {"cycles": []}
    warnings = []
    if not goal:
        return {"ok": False, "report": "No coding goal given.", "cycles": 0,
                "plan": [], "artifacts": artifacts, "warnings": warnings}
    if not os.path.isdir(project_path):
        return {"ok": False,
                "report": f"Project path does not exist: {project_path}",
                "cycles": 0, "plan": [], "artifacts": artifacts,
                "warnings": warnings}

    _emit(event_bus, f"Coding workflow started: {goal[:120]}", phase="plan")

    # 1. Plan (lessons from previous runs are injected by plan_work).
    plan, plan_warnings, raw_plan = plan_work(
        goal, project_path, event_bus=event_bus, use_lessons=lessons)
    warnings.extend(plan_warnings)
    artifacts["planner_raw"] = str(raw_plan)[:4000]
    if not plan:
        message = ("Planner did not produce a usable plan. "
                   + ("; ".join(plan_warnings) if plan_warnings else ""))
        _emit(event_bus, "Coding workflow failed: no plan", phase="done",
              ok=False)
        if record:
            record_lesson({"ts": time.time(), "ok": False, "goal": goal,
                           "project": project_path, "cycles": 0,
                           "failures": [message]})
        return {"ok": False, "report": message, "cycles": 0, "plan": plan,
                "artifacts": artifacts, "warnings": warnings}

    _emit(event_bus, f"Plan ready: {len(plan)} tasks", phase="plan",
          tasks=len(plan))

    all_commands = collect_test_commands(plan, project_path, test_command)
    artifacts["test_commands"] = all_commands
    failures_for_lesson = []
    ok = False
    cycle_number = 0
    prior_results = None
    retry_ids = None

    # 2. Execute cycles.
    for cycle_number in range(1, max(1, int(max_cycles)) + 1):
        _emit(event_bus, f"Coding cycle {cycle_number}", phase="code",
              cycle=cycle_number)

        task_run = run_task_waves(plan, goal, project_path,
                                  event_bus=event_bus, max_steps=max_steps,
                                  prior_results=prior_results,
                                  retry_ids=retry_ids)
        if task_run.get("error"):
            warnings.append(task_run["error"])
            failures_for_lesson.append(f"Plan error: {task_run['error']}")
            break
        prior_results = task_run["results"]
        # Next cycle: only the failures and their dependents need an agent.
        retry_ids = retry_ids_for(plan, prior_results)

        # Run every test command, not just the ones from failed tasks.
        test_results = [run_test_command(command, project_path)
                        for command in all_commands]
        artifacts["cycles"].append({
            "cycle": cycle_number,
            "results": task_run["results"],
            "tests": test_results,
        })

        failed_tests = [item for item in test_results if not item["passed"]]
        verdict, approved = review_work(
            goal, plan, task_run["results"], test_results,
            event_bus=event_bus, cycle=cycle_number)
        artifacts["cycles"][-1]["verdict"] = verdict

        if not failed_tests and approved:
            ok = True
            break

        # 3. Fix loop. A review-only rejection becomes failure evidence too,
        # otherwise the next cycle would repeat identical code and fail the
        # same way. Every retest re-runs the full command set: a fix can break
        # a test that passed before.
        evidence = failed_tests or [{
            "command": "reviewer verdict", "exit_code": None,
            "passed": False, "output": str(verdict),
            "timed_out": False, "blocked": "",
        }]
        for attempt in range(1, max(1, int(max_fixes)) + 1):
            _emit(event_bus,
                  f"Fixing {len(evidence)} failure(s), attempt {attempt}",
                  phase="fix", cycle=cycle_number, attempt=attempt)
            fix_output = fix_failures(
                goal, project_path, evidence, task_run["results"],
                event_bus=event_bus, cycle=cycle_number,
                attempt=attempt, max_steps=max_steps)
            artifacts["cycles"][-1].setdefault("fixes", []).append(
                {"attempt": attempt, "output": str(fix_output)[:3000]})
            test_results = [run_test_command(command, project_path)
                            for command in all_commands]
            artifacts["cycles"][-1]["tests"] = test_results
            artifacts["cycles"][-1].setdefault("retests", []).extend(
                test_results)
            failed_tests = [item for item in test_results
                            if not item["passed"]]
            verdict, approved = review_work(
                goal, plan, task_run["results"], test_results,
                event_bus=event_bus, cycle=cycle_number)
            artifacts["cycles"][-1]["verdict"] = verdict
            if not failed_tests and approved:
                break
            evidence = failed_tests or [{
                "command": "reviewer verdict", "exit_code": None,
                "passed": False, "output": str(verdict),
                "timed_out": False, "blocked": "",
            }]

        if not failed_tests and approved:
            ok = True
            break

        failures_for_lesson.extend(
            f"{item['command']}: {str(item['output'])[:200]}"
            for item in failed_tests if not item.get("passed"))
        failures_for_lesson.append(f"Reviewer: {str(verdict)[:300]}")

    # 4. Record what happened so the next plan starts smarter.
    if record:
        record_lesson({
            "ts": time.time(),
            "ok": ok,
            "goal": goal,
            "project": project_path,
            "cycles": cycle_number,
            "tasks": [task["id"] for task in plan],
            "agents": sorted({item.get("agent") for cycle in artifacts["cycles"]
                              for item in cycle["results"].values()
                              if item.get("agent")}),
            "failures": failures_for_lesson[-8:],
        })

    report = _build_report(goal, project_path, plan, cycle_number, ok,
                           artifacts)
    _emit(event_bus,
          f"Coding workflow finished: {'success' if ok else 'incomplete'}",
          phase="done", ok=ok, cycles=cycle_number)
    return {"ok": ok, "report": report, "cycles": cycle_number, "plan": plan,
            "artifacts": artifacts, "warnings": warnings}


def run_coding_workflow(goal, project_path=".", max_cycles=3,
                        test_command="", commit=False):
    """Tool entry point. Returns a plain-text report with the outcome."""
    result = run_workflow(goal, project_path=project_path,
                          max_cycles=int(max_cycles),
                          test_command=test_command)
    if commit and result.get("ok"):
        try:
            from assistant.dev_tools import git_auto_commit_and_push
            commit_note = str(git_auto_commit_and_push(push=False))
        except Exception as exc:
            commit_note = f"Commit skipped: {exc}"
        result["report"] += f"\n\nCommit: {commit_note}"
    return result["report"]
