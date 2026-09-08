"""Where credentials live, so agents never hold them.

An agent that needs to send mail is given `secret://email_app_password`, not
the password. The reference is resolved at the moment the tool is called, by
this module, inside the control plane. What crosses the model's context, the
timeline, the logs and the API is the reference.

Values are encrypted at rest with a key that lives outside the database, so a
copied `control.db` is not a copied set of credentials.
"""

import base64
import hashlib
import logging
import os
import re
import stat
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from assistant.control.models import now

logger = logging.getLogger(__name__)

# How an agent refers to a secret it must never see.
REFERENCE = re.compile(r"secret://([A-Za-z0-9_.-]+)")

# The key is kept beside the database but never inside it.
DEFAULT_KEY_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "secret.key"

# Environment wins, so a deployment can hand the key in without a file.
KEY_ENVIRONMENT_VARIABLE = "VAVE_SECRET_KEY"

# Credentials that currently sit in config.json, and the names they take here.
CONFIG_SECRETS = {
    "telegram_bot_token": "telegram_bot_token",
    "email_app_password": "email_app_password",
}


class SecretNotFound(Exception):
    """A reference pointed at a secret that does not exist."""


def load_key(path=None):
    """Find or create the encryption key.

    A key file is created with owner-only permissions. It is deliberately not
    stored in the database: copying `control.db` must not copy the ability to
    read what is in it.
    """
    from_environment = os.environ.get(KEY_ENVIRONMENT_VARIABLE, "").strip()
    if from_environment:
        return _coerce_key(from_environment)

    key_path = Path(path or DEFAULT_KEY_PATH)
    if key_path.exists():
        return _coerce_key(key_path.read_text().strip())

    key = Fernet.generate_key()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key)
    try:
        key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)      # 0600
    except OSError:
        logger.warning("Could not restrict permissions on %s", key_path)
    return key


def _coerce_key(value):
    """Accept a real Fernet key, or derive one from any passphrase."""
    raw = value.encode("utf-8") if isinstance(value, str) else value
    try:
        Fernet(raw)
        return raw
    except (ValueError, TypeError):
        digest = hashlib.sha256(raw).digest()
        return base64.urlsafe_b64encode(digest)


class SecretStore:
    """Stores credentials encrypted, and resolves references to them."""

    def __init__(self, store, key=None):
        self.store = store
        self._fernet = Fernet(key or load_key())
        self._lock = threading.RLock()

    # -- keeping ------------------------------------------------------------

    def put(self, name, value, description=""):
        """Store or replace a secret. The value never comes back out of here."""
        if not name:
            raise ValueError("A secret needs a name.")

        with self._lock:
            self.store.save_secret(
                name=name,
                ciphertext=self._fernet.encrypt(str(value).encode("utf-8")).decode(),
                description=description, updated_at=now())
        return self.describe(name)

    def delete(self, name):
        with self._lock:
            return self.store.delete_secret(name)

    def names(self):
        return [row["name"] for row in self.store.list_secrets()]

    def has(self, name):
        return self.store.get_secret(name) is not None

    def describe(self, name):
        """What a client may know about a secret: everything except its value."""
        row = self.store.get_secret(name)
        if row is None:
            return None
        return {"name": row["name"], "description": row["description"] or "",
                "reference": f"secret://{row['name']}",
                "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def list(self):
        return [self.describe(name) for name in self.names()]

    # -- using --------------------------------------------------------------

    def reveal(self, name):
        """The real value. Only the control plane calls this, at the last moment."""
        row = self.store.get_secret(name)
        if row is None:
            raise SecretNotFound(f"There is no secret called '{name}'.")
        try:
            return self._fernet.decrypt(row["ciphertext"].encode()).decode("utf-8")
        except InvalidToken as error:
            raise SecretNotFound(
                f"'{name}' cannot be read with this key. Was the key replaced?"
            ) from error

    def resolve(self, value):
        """Replace `secret://name` references anywhere in a value.

        Strings, lists and dicts are walked, so a whole set of tool arguments
        can be resolved in one call just before the tool runs.
        """
        if isinstance(value, str):
            return REFERENCE.sub(lambda match: self.reveal(match.group(1)), value)
        if isinstance(value, dict):
            return {key: self.resolve(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(self.resolve(item) for item in value)
        return value

    def references(self, value):
        """Which secrets a value asks for, without resolving any of them."""
        return sorted(set(REFERENCE.findall(value if isinstance(value, str)
                                            else str(value))))

    # -- keeping values out of everything else ------------------------------

    def redact(self, text):
        """Replace any stored secret that appears in text with its reference.

        Belt and braces: nothing should ever put a value here, so anything
        this catches is a bug that would otherwise reach a log or a phone.
        """
        if not text:
            return text

        result = str(text)
        for name in self.names():
            try:
                value = self.reveal(name)
            except SecretNotFound:
                continue
            if value and value in result:
                result = result.replace(value, f"secret://{name}")
        return result

    # -- migration ----------------------------------------------------------

    def resolve_setting(self, value, default=""):
        """Resolve ``secret://`` references in a value, or return ``default``.

        Unlike :meth:`resolve`, a failure here never propagates and never
        leaks the reference: an outbound integration must not build a request
        from ``secret://telegram_bot_token`` (that yields a permanent HTTP 404
        on every poll). If nothing can be resolved, ``default`` is returned.
        """
        if not isinstance(value, str) or "secret://" not in value:
            return value
        try:
            return self.resolve(value)
        except Exception as error:                       # missing key/secret/db
            logger.warning("Could not resolve secret reference: %s", error)
            return default

    def import_from_config(self, config_get=None, config_set=None):
        """Move credentials out of config.json and leave references behind."""
        if config_get is None or config_set is None:
            from assistant.config import get_setting, update_setting
            config_get = config_get or get_setting
            config_set = config_set or update_setting

        moved = []
        for setting, name in CONFIG_SECRETS.items():
            value = config_get(setting, "")
            if not value or str(value).startswith("secret://"):
                continue
            self.put(name, value, description=f"Moved out of config.json ({setting}).")
            config_set(setting, f"secret://{name}")
            moved.append(name)
        return moved


def resolve_setting(value, default=""):
    """Resolve ``secret://`` references in a config value at the last moment.

    The outbound integrations (Telegram, e-mail) read a token or password
    straight from config, where the stored value is normally a ``secret://name``
    reference. A request built from an unresolved reference fails confusingly -
    a Telegram token of ``secret://telegram_bot_token`` gives a permanent HTTP
    404 on every poll. On any failure this returns ``default`` (empty string)
    rather than the reference, so a caller never builds a request from one.
    """
    if not isinstance(value, str) or "secret://" not in value:
        return value
    try:
        from assistant.control.store import ControlStore
        store = SecretStore(ControlStore(), key=load_key())
        return store.resolve_setting(value, default=default)
    except Exception as error:                            # store/key unavailable
        logger.warning("Could not open secret store to resolve reference: %s",
                       error)
        return default
