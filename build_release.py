"""Build a shareable VAVE source ZIP without copying local runtime state.

Only application source, tests, documentation, and public assets are included.
The build never imports the assistant or reads the user's runtime config.
"""

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import tempfile
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parent
ROOT_FILES = (
    "main.py", "vave_gui.py", "requirements.txt", "pyproject.toml",
    "README.md", "LICENSE", "build_release.py",
)
SOURCE_DIRS = ("assistant", "gui", "docs", "tests", "assets", "mobile/pwa")
SKIP_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "node_modules", ".pytest_cache",
    "screenshots", "logs", "data", "browser_profile", "build", "dist",
}
SKIP_SUFFIXES = {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".key", ".pem", ".p12", ".pfx", ".bak"}
SAFE_CONFIG = {
    "user_name": "User",
    "assistant_name": "VAVE",
    "speech_enabled": False,
    "wake_word_enabled": False,
    "auto_resume_tasks": False,
    "llm_model": "qwen2.5:7b",
    "llm_model_fast": "qwen2.5:7b",
    "llm_model_smart": "qwen2.5:7b",
    "llm_model_deep": "qwen2.5:7b",
    "ollama_models_dir": "~/.ollama/models",
}

SETUP_BAT = r'''@echo off
setlocal
cd /d "%~dp0"
echo Setting up VAVE. Python 3.11 64-bit is recommended.
python --version >nul 2>&1
if errorlevel 1 (
    echo Python was not found. Install Python and enable Add Python to PATH.
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo ready>".venv\.vave-setup-ok"
echo.
echo Setup complete. Install Ollama and a model before using AI workflows.
echo Then double-click Start_VAVE.bat.
pause
exit /b 0
:failed
echo.
echo Setup failed. Read the error above before trying again.
pause
exit /b 1
'''

START_BAT = r'''@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\.vave-setup-ok" (
    call Setup_VAVE.bat
    if errorlevel 1 exit /b 1
)
echo Starting VAVE. Use typed input; microphone listening is optional.
".venv\Scripts\python.exe" vave_gui.py
if errorlevel 1 (
    echo VAVE could not start. Read the error above.
    pause
    exit /b 1
)
'''

QUICKSTART = """VAVE source distribution

1. Install 64-bit Python 3.11 and enable Add Python to PATH.
2. Extract this ZIP and open its VAVE folder.
3. Run Setup_VAVE.bat; installation errors remain visible.
4. Install Ollama from https://ollama.com/download/windows and start it.
5. Run: ollama pull qwen2.5:7b
   Then: ollama run qwen2.5:7b "Reply with ready."
   Choose a smaller model and update VAVE's settings if your hardware needs it.
6. Run Start_VAVE.bat, or .venv\\Scripts\\python.exe vave_gui.py.
7. Start with typed commands. Microphone and speech are disabled in this package.

Safe verification:
  .venv\\Scripts\\python.exe main.py --smoke-test
  .venv\\Scripts\\python.exe main.py --text "time" --no-speech

Optional browser setup:
  .venv\\Scripts\\python.exe -m playwright install chromium

This is Python source, not a standalone installer. Models, credentials, personal
notes, and local settings are not included. Connected services need separate
configuration. See README.md and docs/ for details.
"""


def read_version(root):
    """Read package metadata without importing runtime modules."""
    metadata = (root / "pyproject.toml").read_text(encoding="utf-8")
    project = re.search(r"(?ms)^\[project\]\s*\n(.*?)(?=^\[|\Z)", metadata)
    match = re.search(r'(?m)^version\s*=\s*"([^"\n]+)"\s*$', project.group(1)) if project else None
    if match is None:
        raise ValueError("Missing project version in pyproject.toml")
    version = match.group(1)
    if not version or any(char not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-" for char in version):
        raise ValueError("Invalid release version")
    return version


def public_source_file(path, root):
    """Reject symlinks, private names, generated state, and paths outside root."""
    relative = path.relative_to(root)
    if any(part.lower() in SKIP_DIRS for part in relative.parts):
        return False
    if any((root.joinpath(*relative.parts[:index])).is_symlink()
           for index in range(1, len(relative.parts) + 1)):
        return False
    if not path.resolve().is_relative_to(root):
        return False
    name = path.name.lower()
    if name.startswith(".") or name in {"config.json", "config.local.json"}:
        return False
    if name.startswith(("credentials", "token", "secret")) and path.suffix.lower() != ".py":
        return False
    if path.suffix.lower() in SKIP_SUFFIXES or name.endswith(("-wal", "-shm")):
        return False
    return path.is_file()


def source_files(root):
    """Walk only explicitly allowed source trees; never follow directory links."""
    for name in ROOT_FILES:
        path = root / name
        if public_source_file(path, root):
            yield path
    for name in SOURCE_DIRS:
        directory = root / name
        if not directory.is_dir() or directory.is_symlink():
            continue
        for current, dirs, files in os.walk(directory, followlinks=False):
            dirs[:] = sorted(d for d in dirs
                             if d.lower() not in SKIP_DIRS
                             and not d.startswith(".")
                             and not (Path(current) / d).is_symlink())
            for filename in sorted(files):
                path = Path(current) / filename
                if public_source_file(path, root):
                    yield path


def build_release(root=PROJECT_ROOT, output_dir=None):
    """Write a versioned archive and its hash manifest, leaving source untouched."""
    root = Path(root).resolve()
    output_dir = Path(output_dir or root / "dist" / "releases").resolve()
    version = read_version(root)
    filename = f"VAVE-{version}-source.zip"
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / filename
    handle, temporary_name = tempfile.mkstemp(prefix=".vave-release-", suffix=".zip", dir=output_dir)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in source_files(root):
                if path.resolve().is_relative_to(output_dir):
                    continue
                bundle.write(path, "VAVE/" + path.relative_to(root).as_posix())
            bundle.writestr("VAVE/config.json", json.dumps(SAFE_CONFIG, indent=2) + "\n")
            bundle.writestr("VAVE/data/notes.txt", "")
            bundle.writestr("VAVE/Setup_VAVE.bat", SETUP_BAT.replace("\n", "\r\n"))
            bundle.writestr("VAVE/Start_VAVE.bat", START_BAT.replace("\n", "\r\n"))
            bundle.writestr("VAVE/QUICKSTART.txt", QUICKSTART)
        os.replace(temporary, archive)
    finally:
        temporary.unlink(missing_ok=True)
    manifest = {
        "version": version,
        "file": filename,
        "distribution": "source",
        "size_bytes": archive.stat().st_size,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "python_recommended": "3.11 (64-bit)",
        "models_included": False,
        "repository": "https://github.com/sohail-bhai/VAVE-main",
    }
    (output_dir / "release.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, help="Output folder (default: dist/releases)")
    args = parser.parse_args()
    archive = build_release(output_dir=args.output_dir)
    print(f"Created {archive}")


if __name__ == "__main__":
    main()
