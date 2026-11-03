"""Safe subprocess command construction for Windows CLI launchers."""

from __future__ import annotations

import os
import re
import shlex
import shutil
from pathlib import Path
from typing import List


def _split_fixed_args(text: str) -> List[str]:
    values = shlex.split(text.strip(), posix=False)
    return [
        value[1:-1]
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'"
        else value
        for value in values
    ]


def _unwrap_windows_script(cli: str) -> List[str]:
    """Resolve a trusted .cmd/.bat shim to its executable and fixed args.

    Passing arbitrary model text through ``cmd.exe /c`` makes shell
    metacharacters executable. Common Antigravity and npm shims only forward
    to an executable, so invoke that target directly instead.
    """
    path = Path(cli).resolve()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ValueError(f"cannot read command launcher '{path}'") from exc

    # Antigravity-style wrapper:
    #   "C:\...\language_server.exe" agentapi %*
    direct = re.search(
        r'^\s*"((?:[A-Za-z]:[\\/]|\\\\)[^"\r\n]+\.exe)"'
        r'\s+([^\r\n]*?)\s+%\*\s*$',
        text,
        re.IGNORECASE | re.MULTILINE,
    )
    if direct:
        executable = Path(direct.group(1))
        if not executable.is_file():
            raise ValueError(f"launcher target does not exist: {executable}")
        return [str(executable), *_split_fixed_args(direct.group(2))]

    # npm .cmd wrapper. Resolve %dp0% ourselves and call node directly.
    npm_script = re.search(
        r'"%dp0%[\\/]([^"\r\n]+\.js)"\s+%\*',
        text,
        re.IGNORECASE,
    )
    if npm_script:
        script = (path.parent / npm_script.group(1)).resolve()
        node = path.parent / "node.exe"
        node_path = str(node) if node.is_file() else shutil.which("node")
        if not node_path:
            raise ValueError("node executable for command launcher was not found")
        if not script.is_file():
            raise ValueError(f"npm launcher target does not exist: {script}")
        return [node_path, str(script)]

    raise ValueError(
        f"unsupported command launcher '{path}'; refusing unsafe cmd.exe fallback"
    )


def safe_cli_command(cli: str, *args: str) -> List[str]:
    """Build shell-free argv, unwrapping Windows scripts when necessary."""
    if os.name == "nt" and Path(cli).suffix.lower() in (".bat", ".cmd"):
        prefix = _unwrap_windows_script(cli)
    else:
        prefix = [cli]
    return [*prefix, *(str(arg) for arg in args)]
