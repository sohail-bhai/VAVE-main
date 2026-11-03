"""Side-effect-free health probes and safe doctor repairs."""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from assistant import config
from assistant.reliability import lifecycle
from assistant.reliability.models import CheckStatus, HealthCheck, HealthReport


def _data_dir():
    override = os.environ.get("VAVE_DATA_DIR", "").strip()
    return Path(override) if override else config.PROJECT_ROOT / "data"


def _timed(name, critical, probe):
    started = time.perf_counter()
    try:
        check = probe()
        if not isinstance(check, HealthCheck):
            raise TypeError("probe did not return HealthCheck")
    except Exception as error:
        check = HealthCheck(
            name=name,
            status=CheckStatus.FAILED,
            summary=f"probe failed: {type(error).__name__}",
            critical=critical,
            details={"error": str(error)[:500]},
        )
    check.name = name
    check.critical = bool(critical)
    check.latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return check


def check_config():
    info = config.inspect_config()
    issues = info["issues"]
    errors = [item for item in issues if item.get("severity") == "error"]
    if not info["valid_json"]:
        summary = (
            "primary config invalid; backup can recover it"
            if info["backup_valid"]
            else "primary config invalid; defaults required"
        )
        return HealthCheck(
            "config",
            CheckStatus.FAILED,
            summary,
            critical=True,
            repairable=True,
            details={
                "path": info["path"],
                "source": info["source"],
                "issue_count": len(issues),
            },
        )
    if issues:
        return HealthCheck(
            "config",
            CheckStatus.WARNING,
            f"{len(issues)} invalid or unsafe setting(s); defaults used in memory",
            critical=True,
            repairable=True,
            details={
                "path": info["path"],
                "issues": issues[:20],
                "error_count": len(errors),
            },
        )
    return HealthCheck(
        "config",
        CheckStatus.OK,
        "valid and within safety limits",
        critical=True,
        details={"path": info["path"], "source": info["source"]},
    )


def check_data_directory():
    path = _data_dir()
    if not path.exists():
        return HealthCheck(
            "data_directory",
            CheckStatus.WARNING,
            "runtime data directory is missing",
            repairable=True,
            details={"path": str(path)},
        )
    if not path.is_dir():
        return HealthCheck(
            "data_directory",
            CheckStatus.FAILED,
            "runtime data path is not a directory",
            critical=True,
            details={"path": str(path)},
        )
    if not os.access(path, os.R_OK | os.W_OK):
        return HealthCheck(
            "data_directory",
            CheckStatus.FAILED,
            "runtime data directory is not readable and writable",
            critical=True,
            details={"path": str(path)},
        )
    return HealthCheck(
        "data_directory",
        CheckStatus.OK,
        "runtime storage is accessible",
        critical=True,
        details={"path": str(path)},
    )


def check_disk_space():
    target = config.PROJECT_ROOT
    usage = shutil.disk_usage(target)
    free_gb = usage.free / (1024 ** 3)
    warning_gb = float(config.get_setting("disk_warning_gb", 15.0))
    if free_gb < 1.0:
        status = CheckStatus.FAILED
        summary = f"critically low disk space: {free_gb:.1f} GB free"
    elif free_gb < warning_gb:
        status = CheckStatus.WARNING
        summary = f"low disk space: {free_gb:.1f} GB free"
    else:
        status = CheckStatus.OK
        summary = f"{free_gb:.1f} GB free"
    return HealthCheck(
        "disk_space",
        status,
        summary,
        critical=True,
        details={"path": str(target), "free_gb": round(free_gb, 2)},
    )


def check_model_storage():
    if platform.system().lower() != "windows":
        return HealthCheck(
            "model_storage",
            CheckStatus.SKIPPED,
            "F: model-storage policy applies on Windows",
        )
    expected = Path(r"F:\ollama\models")
    configured = os.environ.get("OLLAMA_MODELS", "").strip()
    if not configured:
        return HealthCheck(
            "model_storage",
            CheckStatus.FAILED,
            "OLLAMA_MODELS is not set to F:\\ollama\\models",
            critical=True,
            details={"expected": str(expected)},
        )
    actual = Path(configured)
    try:
        matches = actual.resolve() == expected.resolve()
    except OSError:
        matches = str(actual).casefold() == str(expected).casefold()
    if not matches:
        return HealthCheck(
            "model_storage",
            CheckStatus.FAILED,
            "Ollama models are configured outside required F: location",
            critical=True,
            details={"configured": str(actual), "expected": str(expected)},
        )
    if not actual.is_dir():
        return HealthCheck(
            "model_storage",
            CheckStatus.WARNING,
            "configured model directory does not exist yet",
            critical=True,
            repairable=True,
            details={"path": str(actual)},
        )
    return HealthCheck(
        "model_storage",
        CheckStatus.OK,
        "Ollama models remain on F: drive",
        critical=True,
        details={"path": str(actual)},
    )


def check_ollama():
    base = str(config.get_setting("ollama_url", "http://localhost:11434")).rstrip("/")
    timeout = float(config.get_setting("doctor_probe_timeout_seconds", 3))
    request = urllib.request.Request(
        f"{base}/api/tags",
        headers={"User-Agent": "VAVE-Doctor/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (OSError, ValueError, urllib.error.URLError) as error:
        return HealthCheck(
            "ollama",
            CheckStatus.FAILED,
            "local AI service is unreachable",
            critical=True,
            details={"url": base, "error": str(error)[:300]},
        )
    installed = {
        str(item.get("name", ""))
        for item in payload.get("models", [])
        if isinstance(item, dict)
    }
    required = {
        str(config.get_setting(key, "") or "")
        for key in ("llm_model_fast", "llm_model_smart")
    }
    required.discard("")
    missing = sorted(model for model in required if model not in installed)
    if missing:
        return HealthCheck(
            "ollama",
            CheckStatus.WARNING,
            f"Ollama online; {len(missing)} configured model(s) missing",
            critical=True,
            details={"missing_models": missing, "installed_count": len(installed)},
        )
    return HealthCheck(
        "ollama",
        CheckStatus.OK,
        f"online with {len(installed)} installed model(s)",
        critical=True,
    )


def check_microphone():
    try:
        import pyaudio
    except Exception as error:
        return HealthCheck(
            "microphone",
            CheckStatus.FAILED,
            "PyAudio is unavailable",
            critical=True,
            details={"error": str(error)[:300]},
        )
    audio = pyaudio.PyAudio()
    try:
        info = audio.get_default_input_device_info()
        name = str(info.get("name", "default input"))
        channels = int(info.get("maxInputChannels", 0) or 0)
        if channels < 1:
            raise RuntimeError("default input has no channels")
        return HealthCheck(
            "microphone",
            CheckStatus.OK,
            f"default input available: {name}",
            critical=True,
            details={"channels": channels},
        )
    except Exception as error:
        return HealthCheck(
            "microphone",
            CheckStatus.FAILED,
            "no usable default microphone input",
            critical=True,
            details={"error": str(error)[:300]},
        )
    finally:
        audio.terminate()


def check_speech_output():
    modules = {
        "pyttsx3": importlib.util.find_spec("pyttsx3") is not None,
        "edge_tts": importlib.util.find_spec("edge_tts") is not None,
    }
    if not any(modules.values()):
        return HealthCheck(
            "speech_output",
            CheckStatus.FAILED,
            "no supported text-to-speech backend is installed",
            critical=True,
            details=modules,
        )
    return HealthCheck(
        "speech_output",
        CheckStatus.OK,
        "text-to-speech backend installed",
        critical=True,
        details=modules,
    )


def check_browser():
    installed = importlib.util.find_spec("playwright") is not None
    if not installed:
        return HealthCheck(
            "browser",
            CheckStatus.WARNING,
            "Playwright is not installed; browser agent unavailable",
        )
    return HealthCheck(
        "browser",
        CheckStatus.OK,
        "Playwright package installed (browser launch not performed)",
    )


def check_control_database():
    path = _data_dir() / "control.db"
    if not path.exists():
        return HealthCheck(
            "control_database",
            CheckStatus.SKIPPED,
            "control database has not been created yet",
            details={"path": str(path)},
        )
    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=2)
        try:
            result = connection.execute("PRAGMA quick_check").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as error:
        return HealthCheck(
            "control_database",
            CheckStatus.FAILED,
            "SQLite integrity check failed",
            critical=True,
            details={"path": str(path), "error": str(error)[:300]},
        )
    if not result or result[0] != "ok":
        return HealthCheck(
            "control_database",
            CheckStatus.FAILED,
            "SQLite reported corruption",
            critical=True,
            details={"result": str(result)[:300]},
        )
    return HealthCheck(
        "control_database",
        CheckStatus.OK,
        "SQLite quick_check passed",
        critical=True,
    )


def check_remote_channels():
    telegram_token = str(config.get_setting("telegram_bot_token", "") or "")
    telegram_chat = str(config.get_setting("telegram_chat_id", "") or "")
    if not telegram_token and not telegram_chat:
        return HealthCheck(
            "remote_channels",
            CheckStatus.SKIPPED,
            "Telegram remote control is not configured",
        )
    if not telegram_token or not telegram_chat:
        return HealthCheck(
            "remote_channels",
            CheckStatus.WARNING,
            "Telegram configuration is incomplete",
        )
    return HealthCheck(
        "remote_channels",
        CheckStatus.OK,
        "Telegram credentials and paired chat are configured",
    )


def check_lifecycle():
    resources = lifecycle.snapshot()
    dead = [item["name"] for item in resources if not item["alive"]]
    if dead:
        return HealthCheck(
            "lifecycle",
            CheckStatus.WARNING,
            f"{len(dead)} managed background worker(s) are not alive",
            details={"dead": dead, "registered": len(resources)},
        )
    return HealthCheck(
        "lifecycle",
        CheckStatus.OK,
        f"{len(resources)} managed background worker(s) healthy",
        details={"resources": resources},
    )


RUNTIME_PROBES = (
    ("config", True, check_config),
    ("data_directory", True, check_data_directory),
    ("disk_space", True, check_disk_space),
    ("model_storage", True, check_model_storage),
    ("control_database", True, check_control_database),
    ("lifecycle", False, check_lifecycle),
)

DOCTOR_PROBES = RUNTIME_PROBES[:-1] + (
    ("ollama", True, check_ollama),
    ("microphone", True, check_microphone),
    ("speech_output", True, check_speech_output),
    ("browser", False, check_browser),
    ("remote_channels", False, check_remote_channels),
    RUNTIME_PROBES[-1],
)


def run_runtime_diagnostics():
    return HealthReport([
        _timed(name, critical, probe)
        for name, critical, probe in RUNTIME_PROBES
    ])


def run_diagnostics():
    return HealthReport([
        _timed(name, critical, probe)
        for name, critical, probe in DOCTOR_PROBES
    ])


def _repair_directories():
    created = []
    for path in (
        _data_dir(),
        config.PROJECT_ROOT / "logs",
        config.PROJECT_ROOT / "assets" / "screenshots",
    ):
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created.append(str(path))
    return {
        "ok": True,
        "action": "runtime directories",
        "message": f"created {len(created)} missing directorie(s)",
        "created": created,
    }


def _repair_stale_telegram_lock():
    lock_path = config.PROJECT_ROOT / "data" / ".telegram.pid"
    if not lock_path.exists():
        return {
            "ok": True,
            "action": "Telegram singleton lock",
            "message": "no stale lock found",
        }
    try:
        pid = int(lock_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pid = -1
    alive = False
    if pid > 0:
        try:
            import psutil

            alive = psutil.pid_exists(pid)
        except Exception:
            alive = False
    if alive:
        return {
            "ok": True,
            "action": "Telegram singleton lock",
            "message": f"active owner PID {pid}; unchanged",
        }
    lock_path.unlink(missing_ok=True)
    return {
        "ok": True,
        "action": "Telegram singleton lock",
        "message": "removed stale lock",
    }


def run_safe_repairs():
    repairs = []
    try:
        result = config.repair_config()
        repairs.append({
            "ok": bool(result.get("ok")),
            "action": "configuration",
            "message": (
                f"rewrote validated config from {result.get('source')}; "
                f"{result.get('issues_repaired')} issue(s) repaired"
            ),
            "backup": result.get("backup"),
        })
    except Exception as error:
        repairs.append({
            "ok": False,
            "action": "configuration",
            "message": str(error)[:500],
        })
    for repair in (_repair_directories, _repair_stale_telegram_lock):
        try:
            repairs.append(repair())
        except Exception as error:
            repairs.append({
                "ok": False,
                "action": repair.__name__,
                "message": str(error)[:500],
            })
    return repairs


def run_doctor(repair_safe=False):
    repairs = run_safe_repairs() if repair_safe else []
    report = run_diagnostics()
    report.repairs = repairs
    return report


def doctor_cli(json_output=False, repair_safe=False, stream=None):
    stream = stream or sys.stdout
    report = run_doctor(repair_safe=repair_safe)
    if json_output:
        stream.write(json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n")
    else:
        stream.write(report.render_text() + "\n")
    return report.exit_code
