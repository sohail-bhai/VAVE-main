"""Authentication and rate limiting for the control plane API.

The API drives a real computer, so reaching the port must not be the same as
being allowed to use it. Every client is a paired device holding its own
token, and a token can be revoked without touching the others.

Pairing is deliberately small: the desktop app (or anyone on the machine
itself) asks for a short-lived code, the phone posts that code once, and gets
a token back. Only the hash of that token is ever stored, so the database is
not a list of working credentials.
"""

import hashlib
import logging
import secrets
import threading
import time
from collections import OrderedDict

from assistant.config import get_setting
from assistant.control.models import Device, DeviceStatus, now

logger = logging.getLogger(__name__)

# A pairing code is typed by a person, so it is short and dies quickly.
PAIRING_CODE_TTL = 10 * 60
PAIRING_CODE_DIGITS = 6

# Requests allowed per device per minute before the API pushes back.
DEFAULT_RATE_LIMIT = 120

# Callers on the machine itself are the desktop app; they may pair devices.
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


class PairingError(Exception):
    """The pairing code was wrong, used, or expired."""


class RateLimited(Exception):
    """Too many requests. Carries how long the caller should wait."""

    def __init__(self, retry_after):
        super().__init__("Too many requests.")
        self.retry_after = retry_after


def hash_token(token):
    """Tokens are stored only as hashes, so a stolen database is not a key."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TokenBucket:
    """Per-caller rate limit. Refills steadily, caps at burst size, backed by store when provided."""

    # In-memory identities are bounded: without this, randomized caller ids
    # grow the dict forever on long-running servers.
    MAX_IDENTITIES = 1000

    def __init__(self, per_minute, store=None):
        self.capacity = max(1, per_minute)
        self.rate = self.capacity / 60.0
        self.store = store
        self._tokens = OrderedDict()
        self._lock = threading.Lock()

    def take(self, identity):
        """Consume one request. Returns 0 when allowed, else seconds to wait."""
        with self._lock:
            current = time.time()
            if self.store is not None and hasattr(self.store, "get_rate_limit"):
                record = self.store.get_rate_limit(identity)
                if record is not None:
                    tokens = float(record["tokens"])
                    last = float(record["last_updated"])
                else:
                    tokens = float(self.capacity)
                    last = current
                # Wall-clock time can step backwards (NTP). A negative elapsed
                # would drain the bucket and freeze the caller until the clock
                # catches back up, so it is clamped to zero.
                elapsed = max(0.0, current - last)
                tokens = min(float(self.capacity), tokens + elapsed * self.rate)

                if tokens < 1.0:
                    self.store.update_rate_limit(identity, tokens, current)
                    return max(1, int((1.0 - tokens) / self.rate) + 1)

                tokens -= 1.0
                self.store.update_rate_limit(identity, tokens, current)
                return 0
            else:
                try:
                    tokens, last = self._tokens.pop(identity)
                except KeyError:
                    tokens, last = self.capacity, current
                elapsed = max(0.0, current - last)
                tokens = min(self.capacity, tokens + elapsed * self.rate)

                if tokens < 1:
                    self._tokens[identity] = (tokens, current)
                    self._prune_identities()
                    return max(1, int((1 - tokens) / self.rate) + 1)

                self._tokens[identity] = (tokens - 1, current)
                self._prune_identities()
                return 0

    def _prune_identities(self):
        """Drop the stalest in-memory identities past the cap (LRU order:
        every access re-inserts at the end, so the front is the stalest)."""
        while len(self._tokens) > self.MAX_IDENTITIES:
            self._tokens.popitem(last=False)


class ApiSecurity:
    """Who may call the API, and how often.

    `require_auth=False` is for tests and for a deliberately open local setup;
    the default comes from config so the shipped behaviour is closed.
    """

    def __init__(self, store, require_auth=None, trust_local=None,
                 rate_limit_per_minute=None, trusted_hosts=LOCAL_HOSTS):
        self.store = store
        self.require_auth = (get_setting("api_require_auth", True)
                             if require_auth is None else require_auth)
        self.trust_local = (get_setting("api_trust_localhost", True)
                            if trust_local is None else trust_local)
        self.trusted_hosts = tuple(trusted_hosts)
        self.limiter = TokenBucket(
            rate_limit_per_minute
            if rate_limit_per_minute is not None
            else get_setting("api_rate_limit_per_minute", DEFAULT_RATE_LIMIT),
            store=self.store)

        self._codes = {}
        self._lock = threading.Lock()

    # -- pairing ------------------------------------------------------------

    def is_local(self, client_host):
        return bool(client_host) and client_host in self.trusted_hosts

    def issue_pairing_code(self):
        """Mint a one-time code for a phone to claim."""
        code = "".join(secrets.choice("0123456789") for _ in range(PAIRING_CODE_DIGITS))
        expires_at = time.time() + PAIRING_CODE_TTL

        with self._lock:
            self._expire_codes()
            self._codes[code] = expires_at

        logger.info("Pairing code issued. It expires in %d minutes.",
                    PAIRING_CODE_TTL // 60)
        return {"code": code, "expires_at": expires_at,
                "expires_in": PAIRING_CODE_TTL}

    def _expire_codes(self):
        current = time.time()
        for code, expires_at in list(self._codes.items()):
            if expires_at <= current:
                del self._codes[code]

    def pair(self, code, name, kind="phone", platform="", ttl_seconds=None):
        """Trade a valid code for a device and its token.

        The token is returned exactly once. Only its hash is kept.
        """
        with self._lock:
            self._expire_codes()
            if code not in self._codes:
                raise PairingError("That pairing code is wrong or has expired.")
            del self._codes[code]

        if ttl_seconds is None:
            ttl_seconds = get_setting("device_token_ttl_seconds", 30 * 86400)
        token_expires_at = (now() + ttl_seconds) if (ttl_seconds and ttl_seconds > 0) else 0.0

        token = secrets.token_urlsafe(32)
        device = Device(name=name or "Paired device", kind=kind, platform=platform,
                        status=DeviceStatus.ONLINE, token_hash=hash_token(token),
                        paired_at=now(), token_expires_at=token_expires_at)
        self.store.save_device(device)
        return device, token

    def rotate(self, token, ttl_seconds=None, reason="", ip_address=""):
        """Rotate the token for an active (non-expired) device.

        Returns (device, new_token).
        """
        if not token:
            raise PermissionError("Token is required for rotation.")
        device = self.device_for_token(token)
        if device is None:
            raise PermissionError("Cannot rotate: invalid token.")
        if device.is_expired:
            device.status = DeviceStatus.OFFLINE
            self.store.save_device(device)
            raise PermissionError("Cannot rotate: token has expired. Re-pairing required.")

        if ttl_seconds is None:
            ttl_seconds = get_setting("device_token_ttl_seconds", 30 * 86400)
        new_expires_at = (now() + ttl_seconds) if (ttl_seconds and ttl_seconds > 0) else 0.0

        new_token = secrets.token_urlsafe(32)
        device.token_hash = hash_token(new_token)
        device.token_expires_at = new_expires_at
        device.status = DeviceStatus.ONLINE
        device.last_seen = now()
        self.store.save_device(device)
        if hasattr(self.store, "record_token_rotation"):
            self.store.record_token_rotation(device.id, reason=reason, ip_address=ip_address)
        return device, new_token

    def unpair(self, device_id):
        """Revoke one device's token without disturbing the others."""
        device = self.store.get_device(device_id)
        if device is None:
            return None
        device.token_hash = ""
        device.paired_at = 0.0
        device.token_expires_at = 0.0
        device.status = DeviceStatus.OFFLINE
        self.store.save_device(device)
        return device

    # -- authentication -----------------------------------------------------

    def device_for_token(self, token):
        if not token:
            return None
        return self.store.get_device_by_token(hash_token(token))

    def authenticate(self, token, client_host):
        """Return the calling device, or None when the call is unauthenticated.

        Raises PermissionError when the call must be rejected. A local caller
        is allowed through without a token only while `trust_local` holds,
        which is what keeps the desktop app on this machine working.
        """
        device = self.device_for_token(token)
        if device is not None:
            if device.is_expired:
                device.status = DeviceStatus.OFFLINE
                self.store.save_device(device)
                raise PermissionError("Device token has expired. Re-pairing or rotation required.")
            device.status = DeviceStatus.ONLINE
            device.last_seen = now()
            self.store.save_device(device)
            return device

        if token:
            raise PermissionError("That token is not valid.")
        if not self.require_auth:
            return None
        if self.trust_local and self.is_local(client_host):
            return None

        raise PermissionError("Authentication required.")

    def may_pair(self, device, client_host):
        """Only this machine, or an already paired device, may add devices."""
        if device is not None:
            return True
        return self.trust_local and self.is_local(client_host)

    def check_same_origin(self, request):
        """Refuse browser requests arriving from another origin.

        Pairing endpoints mint trust, so a web page must never drive them: an
        evil.com tab in the user's own browser connects from loopback and
        passes may_pair, then reads the code back through permissive CORS.
        Browsers always attach Origin to cross-origin POSTs, while same-origin
        callers (the served PWA, the docs UI) carry a matching Origin and
        non-browsers (CLI, native apps, tests) carry none — so only a real
        cross-origin page is refused. Unparseable origins fail open to avoid
        breaking legitimate clients behind odd proxies.
        """
        headers = getattr(request, "headers", {}) or {}
        get = getattr(headers, "get", None)
        origin = get("origin", "") if callable(get) else ""
        if not origin:
            return
        try:
            from urllib.parse import urlparse
            origin_host = (urlparse(origin).hostname or "").lower()
        except Exception:
            return
        if not origin_host:
            return
        try:
            server_host = (request.url.hostname or "").lower()
        except Exception:
            server_host = ""
        if server_host and origin_host != server_host:
            logger.warning("Refused cross-origin pairing request from %r.",
                           origin)
            raise PermissionError(
                "Cross-origin pairing is not allowed. "
                "Open the VAVE page itself to pair a device.")

    def check_websocket_origin(self, websocket, token):
        """Refuse cross-origin browser sockets that carry no credential.

        A page on evil.com can open ws://127.0.0.1:8765/ws/events from the
        user's own browser; loopback trust would wave it through and stream
        task output and notifications to the attacker (CSWSH). Browsers always
        send Origin on the handshake: absent (native apps, curl, tests) or
        matching passes; anything else needs a real device token. Unparseable
        origins fail closed — unlike plain HTTP, a socket stays open.
        """
        headers = getattr(websocket, "headers", {}) or {}
        get = getattr(headers, "get", None)
        origin = get("origin", "") if callable(get) else ""
        if not origin:
            return
        try:
            from urllib.parse import urlparse
            origin_host = (urlparse(origin).hostname or "").lower()
            server_host = (websocket.url.hostname or "").lower()
        except Exception:
            raise PermissionError(
                "Cross-origin WebSocket connections need a device token.")
        if not origin_host or not server_host or origin_host == server_host:
            return
        credential = token or bearer_token(
            get("authorization", "") if callable(get) else "")
        if not credential:
            logger.warning("Refused cross-origin WebSocket from %r.", origin)
            raise PermissionError(
                "Cross-origin WebSocket connections need a device token.")

    # -- rate limiting ------------------------------------------------------

    def check_rate(self, identity):
        retry_after = self.limiter.take(identity)
        if retry_after:
            raise RateLimited(retry_after)


def bearer_token(header_value):
    """Pull the token out of an Authorization header, or return ''."""
    if not header_value:
        return ""
    parts = header_value.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return ""
