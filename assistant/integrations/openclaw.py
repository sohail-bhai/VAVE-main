"""OpenClaw (Clawdbot) integration for VAVE.

Provides gateway and tool bridging for OpenClaw / Clawdbot messaging,
browser automation, and multi-channel workflows.
"""

import logging
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from assistant.config import get_setting
from assistant.integrations._process import safe_cli_command

logger = logging.getLogger(__name__)

_SUBCOMMAND_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# Standard locations for global npm binaries on Windows. Empty environment
# variables are omitted so lookup never falls back to project-relative paths.
_NPM_GLOBAL_PATHS = [Path("C:/Program Files/nodejs")]
for _env_name, _parts in (
    ("LOCALAPPDATA", ("Programs", "nodejs")),
    ("APPDATA", ("npm",)),
):
    _base = os.environ.get(_env_name, "").strip()
    if _base:
        _NPM_GLOBAL_PATHS.insert(0, Path(_base).joinpath(*_parts))
def find_openclaw_cli() -> Optional[str]:
    """Locate the openclaw or clawdbot CLI executable."""
    override = get_setting("openclaw_cli_path", "")
    if override:
        override_path = Path(str(override)).expanduser().resolve()
        if override_path.is_file():
            return str(override_path)

    for name in ["moltbotden", "openclaw", "clawdbot", "mbd"]:
        found = shutil.which(name)
        if found:
            return found

    for base in _NPM_GLOBAL_PATHS:
        for ext in [".cmd", ".ps1", ".exe", ""]:
            for name in ["moltbotden", "openclaw", "clawdbot", "mbd"]:
                candidate = base / f"{name}{ext}"
                if candidate.is_file():
                    return str(candidate)

    return None


def is_openclaw_available() -> bool:
    """Return True if OpenClaw / Clawdbot CLI is available."""
    return find_openclaw_cli() is not None


def run_openclaw(
    subcommand: str,
    args: Optional[List[str]] = None,
    timeout: int = 180,
    directory: Optional[str] = None,
) -> Dict[str, Any]:
    """Run an OpenClaw / Clawdbot CLI command.

    Args:
        subcommand: The OpenClaw command to run (e.g. 'gateway', 'agent', 'status').
        args: Optional list of additional CLI flags and arguments.
        timeout: Execution timeout in seconds (default 180).
        directory: Working directory.

    Returns:
        Dict with keys: ok (bool), output (str), exit_code (int), error (str).
    """
    subcommand = str(subcommand or "").strip()
    if not _SUBCOMMAND_RE.fullmatch(subcommand):
        return {
            "ok": False,
            "output": "",
            "exit_code": -1,
            "error": "OpenClaw subcommand is empty or invalid.",
        }

    from assistant import guard

    if guard.dry_run_active():
        work_dir = os.path.abspath(directory or os.getcwd())
        return {
            "ok": True,
            "dry_run": True,
            "output": (
                f"DRY RUN: would run OpenClaw subcommand '{subcommand}' with "
                f"{len(args or [])} argument(s) in {work_dir}. No process was started."
            ),
            "exit_code": 0,
            "error": "",
        }

    cli = find_openclaw_cli()
    if not cli:
        return {
            "ok": False,
            "output": "",
            "exit_code": -1,
            "error": "OpenClaw / Clawdbot CLI is not installed or could not be found.",
        }

    clean_args = [str(arg) for arg in (args or [])]
    try:
        cmd = safe_cli_command(cli, subcommand, *clean_args)
    except ValueError as exc:
        return {
            "ok": False,
            "output": "",
            "exit_code": -1,
            "error": f"OpenClaw command rejected: {exc}",
        }

    work_dir = os.path.abspath(directory or os.getcwd())
    if not os.path.isdir(work_dir):
        return {
            "ok": False,
            "output": "",
            "exit_code": -1,
            "error": f"OpenClaw working directory does not exist: {work_dir}",
        }
    try:
        timeout = max(1, min(3600, int(timeout)))
    except (TypeError, ValueError):
        timeout = 180

    logger.info(
        "Running OpenClaw subcommand %s with %d argument(s) in %s",
        subcommand,
        len(clean_args),
        work_dir,
    )

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

        ok = proc.returncode == 0
        return {
            "ok": ok,
            "output": proc.stdout.strip(),
            "exit_code": proc.returncode,
            "error": proc.stderr.strip() if not ok else "",
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "output": "",
            "exit_code": -1,
            "error": f"OpenClaw command timed out after {timeout} seconds.",
        }
    except Exception as exc:
        logger.exception("OpenClaw command execution failed")
        return {
            "ok": False,
            "output": "",
            "exit_code": -1,
            "error": str(exc),
        }


def run_openclaw_command(subcommand: str, args: str = "") -> str:
    """Runs a command via the local OpenClaw / MoltbotDen engine."""
    try:
        if isinstance(args, (list, tuple)):
            arg_list = [str(value) for value in args]
        else:
            arg_list = (
                shlex.split(str(args), posix=os.name != "nt")
                if args
                else None
            )
        if os.name == "nt" and arg_list:
            arg_list = [
                value[1:-1]
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'"
                else value
                for value in arg_list
            ]
    except (TypeError, ValueError) as exc:
        return f"OpenClaw error: invalid arguments ({exc})."
    res = run_openclaw(subcommand, args=arg_list)
    if res["ok"]:
        return f"OpenClaw output:\n{res['output']}"
    return f"OpenClaw error (exit {res['exit_code']}): {res['error']}\n{res['output']}"
