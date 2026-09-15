"""Home Assistant: reading device states and switching things over its REST API.

Mirrors the GitHub agent architecture. The long-lived access token lives in
the control plane's secret store as `homeassistant` (scoped `home.*`), never
in arguments or logs. The base URL comes from the `home_assistant_url`
setting. Everything returns a readable string; nothing here raises into the
model except HomeAssistantError, which the caller turns into a sentence.

Reads are safe; acting on a device is sensitive and goes through the normal
guard and approval paths like any other consequential tool.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from assistant.config import get_setting

logger = logging.getLogger(__name__)

TIMEOUT = 15
MAX_DEVICES = 60

# Domains worth listing. Everything else (automations, scripts, zones) is
# reachable by entity id but kept out of the overview.
DEVICE_DOMAINS = ("light", "switch", "climate", "media_player", "fan",
                  "cover", "sensor", "binary_sensor")

ON_OFF_DOMAINS = ("light", "switch", "fan", "media_player", "cover")


class HomeAssistantError(Exception):
    """Home Assistant refused, is not configured, or could not be reached."""


def _base_url():
    return str(get_setting("home_assistant_url", "") or "").rstrip("/")


def _token(capability):
    """The token, from the secret store (capability-checked) or settings."""
    try:
        from assistant.control.service import get_control_plane
        plane = get_control_plane()
        if plane.secrets.has("homeassistant"):
            try:
                return plane.secrets.reveal("homeassistant", capability=capability)
            except PermissionError:
                raise HomeAssistantError(
                    f"The stored Home Assistant token is not allowed for "
                    f"'{capability}'. Widen its allowed_capabilities to 'home.*'.")
    except HomeAssistantError:
        raise
    except Exception:
        pass

    configured = get_setting("homeassistant_token", "")
    if configured and not str(configured).startswith("secret://"):
        return configured

    raise HomeAssistantError(
        "No Home Assistant token. Store one with "
        "plane.secrets.put('homeassistant', <long-lived-token>, "
        "allowed_capabilities='home.*'), and set home_assistant_url in config.json."
    )


def _request(method, path, base_url, token, payload=None, transport=None):
    """One place that knows how to call Home Assistant."""
    url = f"{base_url}/{path.lstrip('/')}"
    if transport is not None:
        return transport(method, url, payload)

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if payload is not None:
        req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail)
            detail = parsed.get("message", detail)
        except Exception:
            pass
        raise HomeAssistantError(f"Home Assistant {error.code}: {detail}")
    except urllib.error.URLError as error:
        raise HomeAssistantError(f"Cannot reach Home Assistant: {error.reason}")


class HomeAssistantClient:
    """Carries requests to Home Assistant, or to a test double."""

    def __init__(self, base_url="", token="", transport=None):
        self.base_url = base_url
        self.token = token
        self.transport = transport

    def _call(self, method, path, payload=None):
        return _request(method, path, self.base_url, self.token,
                        payload=payload, transport=self.transport)

    def get_states(self):
        return self._call("GET", "api/states")

    def get_state(self, entity_id):
        return self._call("GET", f"api/states/{entity_id}")

    def call_service(self, domain, service, data):
        return self._call("POST", f"api/services/{domain}/{service}", data)


def _client(capability, transport=None):
    url = _base_url()
    if not url:
        raise HomeAssistantError(
            "Home Assistant is not configured yet. Set home_assistant_url "
            "in config.json, e.g. \"http://homeassistant.local:8123\".")
    return HomeAssistantClient(base_url=url, token=_token(capability),
                               transport=transport)


def _friendly(state):
    attrs = state.get("attributes") or {}
    return attrs.get("friendly_name") or state.get("entity_id", "?"), state.get("state", "?")


def list_home_devices(domain="", _client_override=None):
    """List connected devices with their current states, optionally one domain."""
    try:
        client = _client_override or _client("home.read")
        states = client.get_states() or []
    except HomeAssistantError as error:
        return str(error)

    wanted = str(domain or "").strip().lower()
    rows = []
    for state in states:
        entity_id = str(state.get("entity_id", ""))
        if "." not in entity_id:
            continue
        if entity_id.split(".", 1)[0] not in DEVICE_DOMAINS:
            continue
        if wanted and entity_id.split(".", 1)[0] != wanted:
            continue
        name, value = _friendly(state)
        rows.append(f"{name} ({entity_id}): {value}")
        if len(rows) >= MAX_DEVICES:
            break

    if not rows:
        return "No matching devices found."
    return "\n".join(rows)


def get_device_state(entity_id, _client_override=None):
    """The current state of one device, e.g. 'light.bedroom'."""
    entity_id = str(entity_id or "").strip().lower()
    if not entity_id or "." not in entity_id:
        return "Give me a device like 'light.bedroom'."
    try:
        client = _client_override or _client("home.read")
        state = client.get_state(entity_id)
    except HomeAssistantError as error:
        return str(error)
    if not state or not state.get("entity_id"):
        return f"No device called {entity_id}."
    name, value = _friendly(state)
    return f"{name} is {value}."


def control_device(entity_id, action, value=None, _client_override=None):
    """Act on a device: on, off, toggle, brightness (0-100), temperature.

    The state is re-read afterwards, so the answer describes what actually
    happened rather than what was asked for.
    """
    entity_id = str(entity_id or "").strip().lower()
    action = str(action or "").strip().lower()
    if not entity_id or "." not in entity_id:
        return "Give me a device like 'light.bedroom' and an action like 'off'."
    domain = entity_id.split(".", 1)[0]

    try:
        client = _client_override or _client("home.control")
        before = client.get_state(entity_id) or {}
    except HomeAssistantError as error:
        return str(error)
    if not before.get("entity_id"):
        return f"No device called {entity_id}."

    try:
        if action in ("on", "turn_on"):
            if domain not in ON_OFF_DOMAINS:
                return f"{entity_id} cannot simply be switched on."
            client.call_service(domain, "turn_on", {"entity_id": entity_id})
        elif action in ("off", "turn_off"):
            if domain not in ON_OFF_DOMAINS:
                return f"{entity_id} cannot simply be switched off."
            client.call_service(domain, "turn_off", {"entity_id": entity_id})
        elif action == "toggle":
            if domain not in ON_OFF_DOMAINS:
                return f"{entity_id} cannot simply be toggled."
            client.call_service(domain, "toggle", {"entity_id": entity_id})
        elif action in ("brightness", "dim", "brighten"):
            if domain != "light":
                return "Brightness only applies to lights."
            try:
                pct = max(0, min(100, int(float(value))))
            except (TypeError, ValueError):
                return "Brightness needs a number between 0 and 100."
            client.call_service("light", "turn_on",
                                {"entity_id": entity_id, "brightness_pct": pct})
        elif action in ("temperature", "set_temperature"):
            if domain != "climate":
                return "Temperature only applies to climate devices."
            try:
                degrees = float(value)
            except (TypeError, ValueError):
                return "Temperature needs a number, e.g. 24."
            client.call_service("climate", "set_temperature",
                                {"entity_id": entity_id, "temperature": degrees})
        else:
            return (f"Unknown action '{action}'. Use on, off, toggle, "
                    "brightness, or temperature.")
        after = client.get_state(entity_id) or {}
    except HomeAssistantError as error:
        return str(error)

    if not after.get("entity_id"):
        return f"{entity_id} accepted the command but stopped reporting."
    name, value_now = _friendly(after)
    return f"{name} is now {value_now}."
