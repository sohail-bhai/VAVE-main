import logging
logger = logging.getLogger(__name__)

import copy
import json
import os
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = PROJECT_ROOT / "config.json"

DEFAULT_CONFIG = {
    "user_name": "Sohail",
    "assistant_name": "Vave",
    "voice_rate": 170,
    "voice_volume": 1.0,
    "speech_enabled": True,
    "wake_word_enabled": True,
    # When enabled, safe reads still run but every sensitive, destructive, or
    # outward action returns a redacted simulation instead of executing.
    "dry_run_enabled": False,
    "llm_model": "qwen2.5:7b",
    # Featherless is an optional hosted brain. It is only used when the
    # switch in Settings is on and a key is present, so the local Ollama
    # path stays the default.
    "featherless_enabled": False,
    "featherless_api_key": "",
    "featherless_model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
    # Two brains, picked per task. The small one is quick and handles the
    # atomic desktop work well; the large one is for genuine reasoning -
    # planning, research, code, anything with several moving parts. Escalation
    # is automatic, and falls back to the fast model if the big one won't load.
    "llm_model_fast": "qwen2.5:7b",
    "llm_model_smart": "qwen2.5:14b",
    "llm_model_deep": "deepseek-r1:14b",
    "model_escalation_enabled": True,
    # Choosing a tool is not a creative act. Ollama's own default of 0.8 made
    # the same request pick a different action on each run.
    "llm_tool_temperature": 0.1,
    "llm_chat_temperature": 0.7,
    # Where Ollama is listening. Point this elsewhere for a second instance
    # (a CPU-only one, say) or for Ollama on another machine.
    "ollama_url": "http://localhost:11434",
    # Machine-local model storage. Settings writes this override to
    # config.local.json so a laptop and desktop can share the repository.
    "ollama_models_dir": r"F:\ollama\models",
    # A model running on the CPU needs longer to read a long prompt than one
    # on a GPU. A timeout is indistinguishable from "no model", so be generous.
    "llm_timeout_seconds": 300,
    "ollama_num_gpu": None,
    
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    
    "email_address": "",
    "email_app_password": "",
    
    # Control plane API. Authentication is on by default; callers on this
    # machine are trusted so the desktop app keeps working without a token.
    "api_require_auth": True,
    "api_trust_localhost": True,
    "api_rate_limit_per_minute": 120,
    "api_host": "127.0.0.1",
    "api_port": 8765,
    # Extra origins allowed to READ cross-origin API responses (same-origin
    # callers like the served PWA need nothing). Empty = same-origin only.
    # Pairing endpoints additionally reject any cross-origin browser request.
    "api_cors_origins": [],
    "device_token_ttl_seconds": 2592000,
    # Send approvals, failures and security events to Telegram as well as the
    # phone's own connection.
    "notify_telegram": False,

    # Web work is many small tool calls, so one task step gets a longer loop.
    "agent_max_steps": 15,
    # Seeing the browser matters: some sites serve an empty page to a headless
    # one, and a login has to be done by a person in a window they can see.
    "browser_headless": False,
    # Attach to an existing browser via Chrome DevTools Protocol (CDP) if available.
    "browser_cdp_url": "http://127.0.0.1:9222",
    # Automatically pick up interrupted tasks on startup.
    "auto_resume_tasks": True,

    # Disk preservation policy: the GUI flags low free space on the system
    # drive below this many gigabytes.
    "disk_warning_gb": 15.0,

    # Coding workflow: how many coder/test-writer agents may run at once,
    # how long one test command may take, and an optional override for
    # projects whose test command cannot be detected.
    "antigravity_cli_path": "",
    "opencode_cli_path": "",
    "openclaw_cli_path": "",
    "opencode_only_for_complex": True,
    "coding_max_parallel_agents": 3,
    "coding_test_timeout_seconds": 300,
    "coding_test_command": "",

    # Background health sweep cadence in seconds. 0 disables it.
    "health_sweep_seconds": 60,

    # Reliability supervisor. It runs lightweight checks in long-lived modes;
    # `vave doctor` adds network and hardware probes on demand.
    "reliability_supervisor_interval_seconds": 60,
    "reliability_failure_threshold": 3,
    "reliability_recovery_seconds": 300,
    "doctor_probe_timeout_seconds": 3,

    # Smart home bridge. Empty URL disables the Home Assistant tools; the
    # long-lived access token lives in the secret store as "homeassistant".
    "home_assistant_url": "",

    # Folders a paired phone can reach. Nothing is shared until you list one.
    # Example: ["~/Documents", "~/Pictures"]
    "file_shares": [],
    "files_allow_write": True,
    "files_allow_delete": False,

    "default_volume_step": 5,
    "listen_timeout_seconds": 8,
    "listen_phrase_time_limit_seconds": 25,
    "listen_pause_threshold_seconds": 2.0,
    "normal_pause_seconds": 2.0,
    "notes_pause_seconds": 3.0,
    "normal_max_phrase_time": 25,
    "notes_max_phrase_time": 60,

    # Hands-free wake word. openwakeword ships a handful of pretrained models;
    # "hey_jarvis" is the closest fit and is downloaded on first use. A custom
    # model can be pointed at by giving a path to a .onnx/.tflite file instead.
    "wakeword_model": "hey_jarvis",
    "wakeword_threshold": 0.6,

    "websites": {
        "youtube": "https://www.youtube.com",
        "google": "https://www.google.com",
        "gmail": "https://mail.google.com",
        "github": "https://github.com",
        "chatgpt": "https://chatgpt.com",
        "whatsapp": "https://web.whatsapp.com",
        "whatsapp web": "https://web.whatsapp.com",
        "instagram": "https://www.instagram.com"
    },
    
    "routines": {
        "good morning": [
            "what time is it",
            "what is the battery percentage",
            "read my emails"
        ],
        "focus mode": [
            "set volume to 20",
            "open chatgpt"
        ],
        "dev mode": [
            "split screen code and cmd",
            "set volume to 30"
        ],
        "split screen": [
            "split screen notepad and calculator"
        ],
        "movie mode": [
            "open netflix",
            "maximize netflix",
            "set volume to 60"
        ]
    },
    "safety": {
        "voice": {
            "mode": "allow",
            "allow_safe": True,
            "allow_sensitive": True,
            "allow_destructive": False,
            "confirm_sensitive": False,
            "confirm_destructive": True
        },
        "telegram": {
            "mode": "allow",
            "allow_safe": True,
            "allow_sensitive": False,
            "allow_destructive": False,
            "confirm_sensitive": True,
            "confirm_destructive": True
        },
        "routine": {
            "mode": "allow",
            "allow_safe": True,
            "allow_sensitive": True,
            "allow_destructive": True,
            "confirm_sensitive": False,
            "confirm_destructive": False
        }
    },
    "overwatch": {
        "max_z_order": 3,
        "scan_interval": 1.0,
        "rules": [
            {
                "target_text": "submit",
                "auto_click": False,
                "require_focus": False,
                "pattern": "exact"
            },
            {
                "target_text": "proceed",
                "auto_click": False,
                "require_focus": False,
                "pattern": "exact"
            },
            {
                "target_text": "yes",
                "auto_click": False,
                "require_focus": False,
                "pattern": "exact"
            }
        ]
    }
}

# Credentials do not belong in a file that is committed. Anything in
# config.local.json wins over config.json, and that file is git-ignored, so a
# token lives on the machine that uses it instead of in the repository.
LOCAL_CONFIG_FILE = CONFIG_FILE.parent / "config.local.json"

# Keys that must never be written back to the shared config.json.
SECRET_KEYS = frozenset({
    "telegram_bot_token", "telegram_chat_id", "email_app_password", "featherless_api_key",
    "gitlab_token", "openai_api_key", "anthropic_api_key",
})

# Machine-specific values also stay out of shared config writes. They are not
# secrets, but committing one computer's drive layout breaks another computer.
LOCAL_SETTING_KEYS = SECRET_KEYS | frozenset({"ollama_models_dir"})

_config_cache = None
_config_mtime = 0
_local_mtime = 0
_backup_mtime = 0
_config_source = "primary"

# Reads and writes race between the voice loop, task workers and the API.
_config_lock = threading.RLock()

# Last validation result is exposed to `vave doctor` without leaking values.
_config_validation_issues = []

_STRING_LIMITS = {
    "user_name": 80,
    "assistant_name": 80,
    "llm_model": 200,
    "llm_model_fast": 200,
    "llm_model_smart": 200,
    "llm_model_deep": 200,
    "featherless_model": 300,
    "ollama_url": 2048,
    "ollama_models_dir": 2048,
    "browser_cdp_url": 2048,
    "home_assistant_url": 2048,
    "telegram_chat_id": 128,
    "api_host": 255,
    "wakeword_model": 2048,
    "antigravity_cli_path": 2048,
    "opencode_cli_path": 2048,
    "openclaw_cli_path": 2048,
    "coding_test_command": 4096,
}

_NUMBER_RANGES = {
    "voice_rate": (80, 400),
    "voice_volume": (0.0, 1.0),
    "llm_tool_temperature": (0.0, 2.0),
    "llm_chat_temperature": (0.0, 2.0),
    "llm_timeout_seconds": (1, 3600),
    "api_rate_limit_per_minute": (1, 100000),
    "api_port": (1, 65535),
    "device_token_ttl_seconds": (60, 31536000),
    "agent_max_steps": (1, 100),
    "disk_warning_gb": (0.1, 10000.0),
    "coding_max_parallel_agents": (1, 32),
    "coding_test_timeout_seconds": (1, 7200),
    "health_sweep_seconds": (0, 86400),
    "reliability_supervisor_interval_seconds": (0, 86400),
    "reliability_failure_threshold": (1, 100),
    "reliability_recovery_seconds": (1, 86400),
    "doctor_probe_timeout_seconds": (1, 60),
    "default_volume_step": (1, 100),
    "listen_timeout_seconds": (1, 300),
    "listen_phrase_time_limit_seconds": (1, 600),
    "listen_pause_threshold_seconds": (0.1, 30.0),
    "normal_pause_seconds": (0.1, 30.0),
    "notes_pause_seconds": (0.1, 60.0),
    "normal_max_phrase_time": (1, 600),
    "notes_max_phrase_time": (1, 1200),
    "wakeword_threshold": (0.0, 1.0),
}

_URL_KEYS = frozenset({"ollama_url", "browser_cdp_url", "home_assistant_url"})


def _config_backup_path():
    return CONFIG_FILE.with_name(f"{CONFIG_FILE.name}.bak")


def _issue(path, message, severity="warning"):
    return {"path": path, "severity": severity, "message": message}


def _bounded_unknown(value, path, issues):
    """Bound extension settings without rejecting future config keys."""
    if isinstance(value, str) and len(value) > 16384:
        issues.append(_issue(path, "value exceeds 16384 characters; truncated"))
        return value[:16384]
    if isinstance(value, list):
        if len(value) > 500:
            issues.append(_issue(path, "list exceeds 500 items; truncated"))
        return [
            _bounded_unknown(item, f"{path}[{index}]", issues)
            for index, item in enumerate(value[:500])
        ]
    if isinstance(value, dict):
        items = list(value.items())
        if len(items) > 500:
            issues.append(_issue(path, "object exceeds 500 keys; truncated"))
        return {
            str(key)[:200]: _bounded_unknown(item, f"{path}.{key}", issues)
            for key, item in items[:500]
        }
    return value


def _validate_value(key, value, default, path, issues):
    if key == "ollama_num_gpu":
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            issues.append(_issue(path, "expected null or non-negative integer; default restored"))
            return None
        return value
    if isinstance(default, bool):
        if not isinstance(value, bool):
            issues.append(_issue(path, "expected boolean; default restored"))
            return copy.deepcopy(default)
        return value

    if isinstance(default, int) and not isinstance(default, bool):
        if not isinstance(value, int) or isinstance(value, bool):
            issues.append(_issue(path, "expected integer; default restored"))
            return default
    elif isinstance(default, float):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            issues.append(_issue(path, "expected number; default restored"))
            return default
        value = float(value)
    elif isinstance(default, str):
        if not isinstance(value, str):
            issues.append(_issue(path, "expected text; default restored"))
            return default
        limit = _STRING_LIMITS.get(key, 16384)
        if len(value) > limit:
            issues.append(_issue(path, f"exceeds {limit} characters; default restored"))
            return default
        if key in ("user_name", "assistant_name") and not value.strip():
            issues.append(_issue(path, "must not be empty; default restored"))
            return default
        if key in _URL_KEYS and value:
            parsed = urlparse(value)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                issues.append(_issue(path, "must be an http(s) URL; default restored"))
                return default
        return value
    elif isinstance(default, list):
        if not isinstance(value, list):
            issues.append(_issue(path, "expected list; default restored"))
            return copy.deepcopy(default)
        if len(value) > 200:
            issues.append(_issue(path, "list exceeds 200 items; truncated"))
        if default and isinstance(default[0], dict):
            cleaned = []
            template = default[0]
            for index, item in enumerate(value[:200]):
                if not isinstance(item, dict):
                    issues.append(
                        _issue(f"{path}[{index}]", "expected object; item removed")
                    )
                    continue
                cleaned.append(
                    _validate_value(
                        key,
                        item,
                        template,
                        f"{path}[{index}]",
                        issues,
                    )
                )
            return cleaned
        cleaned = []
        for index, item in enumerate(value[:200]):
            if not isinstance(item, str):
                issues.append(_issue(f"{path}[{index}]", "expected text; item removed"))
                continue
            if len(item) > 2048:
                issues.append(_issue(f"{path}[{index}]", "item too long; removed"))
                continue
            cleaned.append(item)
        return cleaned
    elif isinstance(default, dict):
        if not isinstance(value, dict):
            issues.append(_issue(path, "expected object; default restored"))
            return copy.deepcopy(default)
        cleaned = {}
        for child_key, child_value in value.items():
            child_path = f"{path}.{child_key}" if path else str(child_key)
            if child_key in default:
                cleaned[child_key] = _validate_value(
                    child_key,
                    child_value,
                    default[child_key],
                    child_path,
                    issues,
                )
            else:
                cleaned[child_key] = _bounded_unknown(child_value, child_path, issues)
        return cleaned

    bounds = _NUMBER_RANGES.get(key)
    if bounds is not None:
        minimum, maximum = bounds
        if value < minimum or value > maximum:
            issues.append(
                _issue(path, f"must be between {minimum} and {maximum}; default restored")
            )
            return default
    return value


def validate_config(config_data):
    """Return ``(sanitized_overrides, issues)`` without mutating input."""
    if not isinstance(config_data, dict):
        return {}, [_issue("config", "root must be a JSON object", "error")]
    issues = []
    sanitized = {}
    for key, value in config_data.items():
        if key in DEFAULT_CONFIG:
            sanitized[key] = _validate_value(
                key,
                value,
                DEFAULT_CONFIG[key],
                key,
                issues,
            )
        else:
            sanitized[key] = _bounded_unknown(value, str(key), issues)
    return sanitized, issues

def create_default_config():
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(DEFAULT_CONFIG, indent=4),
        encoding="utf-8"
    )

def deep_merge(base, override):
    merged = base.copy()
    for k, v in override.items():
        if isinstance(v, dict) and k in merged and isinstance(merged[k], dict):
            merged[k] = deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged

def _read_local_config():
    """The machine's own settings and credentials, if it has any."""
    if not LOCAL_CONFIG_FILE.exists():
        return {}, 0
    try:
        with open(LOCAL_CONFIG_FILE, "r", encoding="utf-8") as file:
            return json.load(file), os.path.getmtime(LOCAL_CONFIG_FILE)
    except Exception as error:
        logger.info("Could not read config.local.json: %s", error)
        return {}, 0


def _read_json_object(path):
    with open(path, "r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError("config root must be a JSON object")
    return value


def _atomic_write_text(path, text, prefix):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=prefix,
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def inspect_config():
    """Return safe config diagnostics without returning setting values."""
    path = Path(CONFIG_FILE)
    backup = _config_backup_path()
    result = {
        "path": str(path),
        "backup_path": str(backup),
        "exists": path.exists(),
        "valid_json": False,
        "backup_valid": False,
        "recoverable": False,
        "source": "default",
        "issues": [],
    }

    primary = None
    if path.exists():
        try:
            primary = _read_json_object(path)
            result["valid_json"] = True
            result["source"] = "primary"
        except Exception as error:
            result["issues"].append(
                _issue("config", f"cannot parse config.json: {error}", "error")
            )
    else:
        result["issues"].append(
            _issue("config", "config.json does not exist", "error")
        )

    backup_data = None
    if backup.exists():
        try:
            backup_data = _read_json_object(backup)
            result["backup_valid"] = True
        except Exception as error:
            result["issues"].append(
                _issue("config.backup", f"backup is invalid: {error}")
            )

    source_data = primary if primary is not None else backup_data
    if source_data is not None:
        _, validation_issues = validate_config(source_data)
        result["issues"].extend(validation_issues)
        if primary is None:
            result["source"] = "backup"
            result["recoverable"] = True
    else:
        result["recoverable"] = True
    return result


def load_config():
    global _config_cache, _config_mtime, _local_mtime
    global _backup_mtime, _config_source, _config_validation_issues
    with _config_lock:
        if not CONFIG_FILE.exists():
            create_default_config()

        current_mtime = os.path.getmtime(CONFIG_FILE)
        backup = _config_backup_path()
        backup_mtime = os.path.getmtime(backup) if backup.exists() else 0
        local_config, local_mtime = _read_local_config()

        if (_config_cache is not None and current_mtime == _config_mtime
                and local_mtime == _local_mtime
                and backup_mtime == _backup_mtime):
            return _config_cache

        issues = []
        try:
            user_config = _read_json_object(CONFIG_FILE)
            _config_source = "primary"
        except Exception as error:
            issues.append(_issue("config", f"primary config invalid: {error}", "error"))
            try:
                user_config = _read_json_object(backup)
                _config_source = "backup"
                logger.warning("config.json is invalid; using validated backup %s", backup)
            except Exception:
                user_config = {}
                _config_source = "default"
                logger.warning("config.json is invalid and no usable backup exists; using defaults")

        user_config, shared_issues = validate_config(user_config)
        local_config, local_issues = validate_config(local_config)
        issues.extend(shared_issues)
        issues.extend(
            {**item, "path": f"local.{item['path']}"}
            for item in local_issues
        )

        merged = deep_merge(DEFAULT_CONFIG, user_config)
        _config_cache = deep_merge(merged, local_config)
        _config_mtime = current_mtime
        _local_mtime = local_mtime
        _backup_mtime = backup_mtime
        _config_validation_issues = issues
        return _config_cache

def save_config(config, create_backup=True):
    data = json.dumps(config, indent=4)
    if create_backup and CONFIG_FILE.exists():
        previous = CONFIG_FILE.read_text(encoding="utf-8", errors="replace")
        _atomic_write_text(
            _config_backup_path(),
            previous,
            ".config-backup.",
        )
    _atomic_write_text(CONFIG_FILE, data, ".config.")
    global _config_mtime, _backup_mtime
    with _config_lock:
        _config_mtime = 0
        _backup_mtime = 0


def get_config_issues():
    """Current redacted validation issues, refreshing cache when needed."""
    load_config()
    return copy.deepcopy(_config_validation_issues)


def config_source():
    load_config()
    return _config_source


def reset_config_cache():
    """Clear cached config state. Intended for tests and safe repair flows."""
    global _config_cache, _config_mtime, _local_mtime, _backup_mtime
    global _config_validation_issues, _config_source
    with _config_lock:
        _config_cache = None
        _config_mtime = 0
        _local_mtime = 0
        _backup_mtime = 0
        _config_validation_issues = []
        _config_source = "primary"


def repair_config():
    """Safely rewrite config from primary, backup, or defaults.

    Existing config is retained as ``config.json.bak`` by ``save_config``.
    Returns a machine-readable result for `vave doctor --repair-safe`.
    """
    with _config_lock:
        source = "default"
        raw = {}
        try:
            raw = _read_json_object(CONFIG_FILE)
            source = "primary"
        except Exception:
            try:
                raw = _read_json_object(_config_backup_path())
                source = "backup"
            except Exception:
                raw = {}

        sanitized, issues = validate_config(raw)
        save_config(sanitized, create_backup=(source == "primary"))
        reset_config_cache()
        load_config()
        return {
            "ok": True,
            "source": source,
            "backup": str(_config_backup_path()),
            "issues_repaired": len(issues),
        }

def get_setting(key, default=None):
    config = load_config()
    return config.get(key, default)


def normalize_ollama_models_dir(value=None):
    """Return configured model directory as an absolute expanded path."""
    if value is None:
        value = get_setting(
            "ollama_models_dir",
            DEFAULT_CONFIG["ollama_models_dir"],
        )
    text = os.path.expandvars(os.path.expanduser(str(value or "").strip()))
    if not text:
        return ""
    return str(Path(text).resolve())


def apply_ollama_models_dir(value=None, persist_user=False):
    """Apply model path to this process and optionally Windows user env.

    Existing Ollama processes cannot inherit a changed environment. Callers
    surface ``restart_required`` rather than killing or restarting Ollama.
    """
    path = normalize_ollama_models_dir(value)
    if not path:
        raise ValueError("Ollama model directory cannot be empty.")

    previous = os.environ.get("OLLAMA_MODELS", "")
    os.environ["OLLAMA_MODELS"] = path
    persisted = False
    if persist_user and os.name == "nt":
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            "Environment",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(
                key,
                "OLLAMA_MODELS",
                0,
                winreg.REG_SZ,
                path,
            )
        persisted = True
        try:
            import ctypes

            result = ctypes.c_ulong()
            ctypes.windll.user32.SendMessageTimeoutW(
                0xFFFF,
                0x001A,
                0,
                "Environment",
                0x0002,
                5000,
                ctypes.byref(result),
            )
        except Exception:
            logger.debug("Could not broadcast Ollama environment change.", exc_info=True)

    return {
        "path": path,
        "exists": Path(path).is_dir(),
        "persisted": persisted,
        "restart_required": bool(
            persist_user
            or os.path.normcase(previous) != os.path.normcase(path)
        ),
    }

def update_setting(key, value):
    """Change one setting. Credentials go to the untracked local file."""
    if key in LOCAL_SETTING_KEYS:
        return _update_local_setting(key, value)

    with _config_lock:
        # Work on a copy: load_config() returns the shared cache, and the
        # secret-stripping below used to destroy cached credentials in memory.
        config = copy.deepcopy(load_config())
        config[key] = value
        # The cache holds values merged from config.local.json; writing it back
        # wholesale would copy a credential into the shared file.
        for local_key in LOCAL_SETTING_KEYS:
            config.pop(local_key, None)
        config, issues = validate_config(config)
        rejected = [item for item in issues if item.get("path") == key]
        if rejected and config.get(key) != value:
            raise ValueError(rejected[0]["message"])
        save_config(config)


def _update_local_setting(key, value):
    global _local_mtime
    with _config_lock:
        local_config, _ = _read_local_config()
        local_config[key] = value
        local_config, issues = validate_config(local_config)
        rejected = [item for item in issues if item.get("path") == key]
        if rejected and local_config.get(key) != value:
            raise ValueError(rejected[0]["message"])
        data = json.dumps(local_config, indent=4)
        _atomic_write_text(
            LOCAL_CONFIG_FILE,
            data,
            ".config-local.",
        )
        _local_mtime = 0
