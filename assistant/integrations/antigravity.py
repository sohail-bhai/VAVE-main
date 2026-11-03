"""Antigravity Agent API integration harness for VAVE.

Connects VAVE Project Manager to local Antigravity Language Server / Agent API CLI
(C:\\Users\\ADMIN\\.gemini\\antigravity\\bin\\agentapi.bat).
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

DEFAULT_AGENTAPI_PATH = str(
    Path.home() / ".gemini" / "antigravity" / "bin" / "agentapi.bat"
)
def get_agentapi_path() -> Optional[str]:
    """Return verified path to agentapi.bat CLI."""
    override = str(get_setting("antigravity_cli_path", "") or "").strip()
    if override:
        override_path = Path(override).expanduser().resolve()
        if override_path.is_file():
            return str(override_path)
    if os.path.isfile(DEFAULT_AGENTAPI_PATH):
        return DEFAULT_AGENTAPI_PATH
    found = shutil.which("agentapi.bat") or shutil.which("agentapi")
    if found and os.path.isfile(found):
        return found
    return None


def is_antigravity_available() -> bool:
    """Return True if Antigravity Agent API CLI is accessible."""
    cli = get_agentapi_path()
    if not cli:
        return False
    try:
        res = subprocess.run(
            safe_cli_command(cli, "--help"),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return res.returncode == 0
    except Exception as exc:
        logger.debug("Antigravity availability check failed: %s", exc)
        return False


def run_antigravity_task(
    prompt: str,
    model: str = "flash",
    directory: str = ".",
    timeout: int = 180,
) -> Dict[str, Any]:
    """Dispatch an autonomous coding task to Antigravity."""
    prompt = str(prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "Antigravity task prompt is empty."}

    from assistant import guard

    if guard.dry_run_active():
        return {
            "ok": True,
            "dry_run": True,
            "output": (
                f"DRY RUN: would delegate prompt ({len(prompt)} chars) to "
                f"Antigravity model {model}. No process was started."
            ),
            "exit_code": 0,
            "engine": "antigravity",
            "model": model,
        }

    cli = get_agentapi_path()
    if not cli:
        return {
            "ok": False,
            "error": "Antigravity Agent API CLI (agentapi.bat) not found.",
        }

    valid_models = ("flash_lite", "flash", "pro")
    chosen_model = model if model in valid_models else "flash"

    try:
        cmd = safe_cli_command(
            cli,
            "new-conversation",
            f"--model={chosen_model}",
            prompt,
        )
    except ValueError as exc:
        return {"ok": False, "error": f"Antigravity task rejected: {exc}"}
    target_dir = os.path.abspath(directory)
    if not os.path.isdir(target_dir):
        return {
            "ok": False,
            "error": f"Antigravity working directory does not exist: {target_dir}",
        }

    try:
        timeout = max(1, min(3600, int(timeout)))
    except (TypeError, ValueError):
        timeout = 180

    logger.info("Antigravity: Launching task with model=%s in %s", chosen_model, target_dir)

    try:
        proc = subprocess.run(
            cmd,
            cwd=target_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        output = (proc.stdout + "\n" + proc.stderr).strip()
        ok = proc.returncode == 0
        return {
            "ok": ok,
            "output": output,
            "exit_code": proc.returncode,
            "engine": "antigravity",
            "model": chosen_model,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"Antigravity task timed out after {timeout} seconds.",
            "exit_code": -1,
            "engine": "antigravity",
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Antigravity execution failed: {exc}",
            "exit_code": -1,
            "engine": "antigravity",
        }
