"""OpenCode CLI integration for VAVE.

Allows VAVE's Project Manager to delegate coding tasks, refactoring, and
scaffolding to the locally installed OpenCode desktop engine.
"""

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from assistant.config import get_setting
from assistant.integrations._process import safe_cli_command

logger = logging.getLogger(__name__)

# Known install locations on Windows. Never turn a missing environment variable
# into a relative path inside an untrusted project directory.
_local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
_KNOWN_PATHS = [Path("C:/Program Files/OpenCode/resources/opencode-cli.exe")]
if _local_app_data:
    _KNOWN_PATHS.insert(
        0,
        Path(_local_app_data)
        / "Programs"
        / "ai.opencode.desktop"
        / "resources"
        / "opencode-cli.exe",
    )
def find_opencode_cli() -> Optional[str]:
    """Locate the opencode-cli binary on the system."""
    override = get_setting("opencode_cli_path", "")
    if override:
        override_path = Path(str(override)).expanduser().resolve()
        if override_path.is_file():
            return str(override_path)

    for path in _KNOWN_PATHS:
        if path.is_file():
            return str(path)

    # Check system PATH
    found = shutil.which("opencode-cli") or shutil.which("opencode")
    if found:
        return found

    return None


def is_opencode_available() -> bool:
    """Return True if OpenCode CLI is installed and executable."""
    return find_opencode_cli() is not None


def list_opencode_models(timeout: int = 15) -> list[str]:
    """Return list of models supported by the local OpenCode installation."""
    cli = find_opencode_cli()
    if not cli:
        return []

    try:
        proc = subprocess.run(
            safe_cli_command(cli, "models"),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode == 0:
            return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        return []
    except Exception as exc:
        logger.warning("Failed to query OpenCode models: %s", exc)
        return []


def run_opencode(
    prompt: str,
    directory: Optional[str] = None,
    model: Optional[str] = None,
    session_id: Optional[str] = None,
    auto_approve: bool = True,
    timeout: int = 300,
) -> Dict[str, Any]:
    """Execute a coding task via OpenCode CLI.

    Args:
        prompt: Task instruction or coding message.
        directory: Working directory for the task (default: current workspace).
        model: Model identifier (e.g. 'opencode/flint-40b-free' or 'openai/gpt-4o').
        session_id: Optional existing or new session ID.
        auto_approve: Automatically approve permissions in OpenCode.
        timeout: Execution timeout in seconds (default 300).

    Returns:
        Dict with keys: ok (bool), output (str), exit_code (int), error (str).
    """
    prompt = str(prompt or "").strip()
    if not prompt:
        return {
            "ok": False,
            "output": "",
            "exit_code": -1,
            "error": "OpenCode task prompt is empty.",
        }

    from assistant import guard

    if guard.dry_run_active():
        work_dir = os.path.abspath(directory or os.getcwd())
        return {
            "ok": True,
            "dry_run": True,
            "output": (
                f"DRY RUN: would launch OpenCode in {work_dir} for a "
                f"{len(prompt)}-character task. No process was started."
            ),
            "exit_code": 0,
            "error": "",
        }

    cli = find_opencode_cli()
    if not cli:
        return {
            "ok": False,
            "output": "",
            "exit_code": -1,
            "error": "OpenCode CLI is not installed or could not be found.",
        }

    args = ["run"]
    if auto_approve:
        args.append("--auto")
    if model:
        args.extend(["--model", str(model)])
    if session_id:
        args.extend(["--session", str(session_id)])
    args.extend(["--format", "default", prompt])
    try:
        cmd = safe_cli_command(cli, *args)
    except ValueError as exc:
        return {
            "ok": False,
            "output": "",
            "exit_code": -1,
            "error": f"OpenCode task rejected: {exc}",
        }

    work_dir = os.path.abspath(directory or os.getcwd())
    if not os.path.isdir(work_dir):
        return {
            "ok": False,
            "output": "",
            "exit_code": -1,
            "error": f"OpenCode working directory does not exist: {work_dir}",
        }
    try:
        timeout = max(1, min(3600, int(timeout)))
    except (TypeError, ValueError):
        timeout = 300

    logger.info("Launching OpenCode task in %s (prompt length=%d)", work_dir, len(prompt))

    try:
        proc = subprocess.run(
            cmd,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

        output = proc.stdout.strip()
        err = proc.stderr.strip()
        ok = proc.returncode == 0

        return {
            "ok": ok,
            "output": output,
            "exit_code": proc.returncode,
            "error": err if not ok else "",
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "output": "",
            "exit_code": -1,
            "error": f"OpenCode task timed out after {timeout} seconds.",
        }
    except Exception as exc:
        logger.exception("OpenCode execution failed")
        return {
            "ok": False,
            "output": "",
            "exit_code": -1,
            "error": str(exc),
        }


def run_opencode_task(prompt: str, directory: str = "", model: str = "") -> str:
    """Delegates a coding or refactoring task to the local OpenCode desktop engine."""
    res = run_opencode(prompt, directory=directory or None, model=model or None)
    if res["ok"]:
        return f"OpenCode completed successfully:\n{res['output']}"
    return f"OpenCode failed (exit {res['exit_code']}): {res['error']}\n{res['output']}"


def list_opencode_models_tool() -> str:
    """Lists models available in local OpenCode."""
    models = list_opencode_models()
    if not models:
        return "No OpenCode models found or OpenCode CLI not available."
    return "Available OpenCode models:\n" + "\n".join(models)
