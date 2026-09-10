import logging
logger = logging.getLogger(__name__)

import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = PROJECT_ROOT / "config.json"

DEFAULT_CONFIG = {
    "user_name": "Sohail",
    "assistant_name": "Vave",
    "voice_rate": 170,
    "voice_volume": 1.0,
    "llm_model": "qwen2.5:3b",
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
    "llm_model_fast": "qwen2.5:3b",
    "llm_model_smart": "qwen3.5:9b",
    "model_escalation_enabled": True,
    # Choosing a tool is not a creative act. Ollama's own default of 0.8 made
    # the same request pick a different action on each run.
    "llm_tool_temperature": 0.1,
    "llm_chat_temperature": 0.7,
    # Where Ollama is listening. Point this elsewhere for a second instance
    # (a CPU-only one, say) or for Ollama on another machine.
    "ollama_url": "http://localhost:11434",
    # A model running on the CPU needs longer to read a long prompt than one
    # on a GPU. A timeout is indistinguishable from "no model", so be generous.
    "llm_timeout_seconds": 300,
    
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    
    "email_address": "",
    "email_app_password": "",
    
    # Control plane API. Authentication is on by default; callers on this
    # machine are trusted so the desktop app keeps working without a token.
    "api_require_auth": True,
    "api_trust_localhost": True,
    "api_rate_limit_per_minute": 120,
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
    "telegram_bot_token", "email_app_password", "featherless_api_key",
    "gitlab_token", "openai_api_key", "anthropic_api_key",
})

_config_cache = None
_config_mtime = 0
_local_mtime = 0

def create_default_config():
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


def load_config():
    global _config_cache, _config_mtime, _local_mtime
    if not CONFIG_FILE.exists():
        create_default_config()

    try:
        current_mtime = os.path.getmtime(CONFIG_FILE)
        local_config, local_mtime = _read_local_config()

        if (_config_cache is not None and current_mtime == _config_mtime
                and local_mtime == _local_mtime):
            return _config_cache

        with open(CONFIG_FILE, "r", encoding="utf-8") as file:
            user_config = json.load(file)

        merged = deep_merge(DEFAULT_CONFIG, user_config)
        _config_cache = deep_merge(merged, local_config)
        _config_mtime = current_mtime
        _local_mtime = local_mtime

        return _config_cache

    except Exception as error:
        logger.info("Config error:", error)
        logger.info("Using default config.")
        return DEFAULT_CONFIG

def save_config(config):
    CONFIG_FILE.write_text(
        json.dumps(config, indent=4),
        encoding="utf-8"
    )
    global _config_mtime
    _config_mtime = 0

def get_setting(key, default=None):
    config = load_config()
    return config.get(key, default)

def update_setting(key, value):
    """Change one setting. Credentials go to the untracked local file."""
    if key in SECRET_KEYS:
        return _update_local_setting(key, value)

    config = load_config()
    config[key] = value
    # The cache holds values merged from config.local.json; writing it back
    # wholesale would copy a credential into the shared file.
    for secret in SECRET_KEYS:
        config.pop(secret, None)
    save_config(config)


def _update_local_setting(key, value):
    global _local_mtime
    local_config, _ = _read_local_config()
    local_config[key] = value
    LOCAL_CONFIG_FILE.write_text(json.dumps(local_config, indent=4),
                                 encoding="utf-8")
    _local_mtime = 0
