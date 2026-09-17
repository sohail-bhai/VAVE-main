"""Calling any service that has an API, without writing a module for each one.

The browser is how VAVE uses a site a person would click through. This is
the other half: most services worth automating - GitHub, Jira, Linear, Notion,
Slack, a home server, your own backend - have a REST API, and an API call
either worked or it did not, which a click never quite tells you.

Credentials are named, never passed in. `auth_secret="github_token"` looks the
token up in the control plane's secret store at the moment of the call, so the
model asks for a service by name and never sees the key.
"""

import ipaddress
import json
import logging
import socket
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

TIMEOUT = 30

# Enough of a response to reason about, not so much that it floods the model.
MAX_RESPONSE_CHARS = 4000

# How different services expect their credential to be presented.
AUTH_STYLES = {
    "bearer": ("Authorization", "Bearer {token}"),
    "token": ("Authorization", "token {token}"),
    "private-token": ("PRIVATE-TOKEN", "{token}"),
    "x-api-key": ("X-API-Key", "{token}"),
    "basic": ("Authorization", "Basic {token}"),
}

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

class WebApiError(Exception):
    """The service refused, or could not be reached."""


# The model chooses these addresses, and a web page it just read can suggest
# one. Without this, "call this API" reaches VAVE's own server on localhost,
# the router, a NAS on the LAN, or a cloud metadata endpoint - none of which
# the user meant by "an API on the internet". Names are resolved first, because
# a hostname can point anywhere.
BLOCKED_HOSTS = {"metadata.google.internal", "metadata.goog"}


class BlockedAddress(WebApiError):
    """The address points somewhere on this machine or this network."""


class _CheckedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to a target that passes check_address, and
    never carry credential headers to a different host or scheme.

    The stdlib follows 301/302/307 without re-checking, so a public API that
    answers `Location: http://127.0.0.1:8765/...` would walk straight past
    the SSRF filter — with the caller's credential headers attached.
    """

    _CREDENTIAL_HEADERS = ("authorization", "private-token", "x-api-key")

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_address(newurl)
        new_request = super().redirect_request(req, fp, code, msg, headers,
                                               newurl)
        if new_request is None:
            return None
        try:
            old = urllib.parse.urlparse(req.get_full_url())
            new = urllib.parse.urlparse(new_request.get_full_url())
        except Exception:
            return new_request
        if ((old.hostname or "").lower() != (new.hostname or "").lower()
                or old.scheme.lower() != new.scheme.lower()):
            for name in list(new_request.headers):
                if name.lower() in self._CREDENTIAL_HEADERS:
                    del new_request.headers[name]
        return new_request


def _open(request):
    opener = urllib.request.build_opener(_CheckedRedirectHandler)
    return opener.open(request, timeout=TIMEOUT)


def _is_private(address):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def check_address(url, resolver=None):
    """Refuse an address that is not out on the internet.

    `resolver` exists for the tests; it stands in for DNS.
    """
    parsed = urllib.parse.urlparse(str(url))
    host = (parsed.hostname or "").lower()
    if not host:
        raise WebApiError("That address has no host in it.")

    if host in BLOCKED_HOSTS or host.endswith(".local") or host == "localhost":
        raise BlockedAddress(
            f"{host} is on this machine or this network, not the internet. "
            "VAVE will not call it from a tool.")

    if _is_private(host):
        raise BlockedAddress(
            f"{host} is a private address, not a public API.")

    resolve = resolver or (lambda name: [item[4][0] for item in
                                         socket.getaddrinfo(name, None)])
    try:
        addresses = resolve(host)
    except Exception:
        # A name that will not resolve fails later anyway, with a clearer
        # message than anything invented here.
        return

    for address in addresses:
        if _is_private(address):
            raise BlockedAddress(
                f"{host} resolves to {address}, which is on this machine or "
                "this network. VAVE will not call it from a tool.")


def _resolve_secret(name):
    """Look a credential up by name. The caller never holds the value."""
    if not name:
        return ""

    from assistant.control.service import get_control_plane

    plane = get_control_plane()
    clean = str(name).replace("secret://", "").strip()
    if not plane.secrets.has(clean):
        raise WebApiError(
            f"There is no stored credential called '{clean}'. "
            f"Store one first, then try again.")
    return plane.secrets.reveal(clean)


def _headers(auth_secret, auth_style, extra):
    headers = {"Accept": "application/json", "User-Agent": "VAVE"}
    headers.update(extra or {})

    if auth_secret:
        style = str(auth_style or "bearer").lower()
        if style not in AUTH_STYLES:
            raise WebApiError(
                f"'{style}' is not a way of sending a credential I know. "
                f"Use one of: {', '.join(sorted(AUTH_STYLES))}.")
        header, template = AUTH_STYLES[style]
        headers[header] = template.format(token=_resolve_secret(auth_secret))

    return headers


def call(method, url, body=None, params=None, auth_secret="", auth_style="bearer",
         headers=None, transport=None):
    """Make one API call and return (status, parsed body or text)."""
    method = str(method).upper()
    if not str(url).startswith(("http://", "https://")):
        raise WebApiError("The address must start with http:// or https://.")

    check_address(url)

    if params:
        url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(params)}"

    prepared = _headers(auth_secret, auth_style, headers)

    if transport is not None:
        return transport(method, url, body, prepared)

    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        prepared["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=payload, method=method)
    for name, value in prepared.items():
        request.add_header(name, value)

    try:
        with _open(request) as response:
            text = response.read().decode("utf-8", errors="replace")
            status = response.status
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise WebApiError(f"{error.code} from {url}: {detail}") from error
    except BlockedAddress:
        raise
    except Exception as error:
        raise WebApiError(f"Could not reach {url}: {error}") from error

    try:
        return status, json.loads(text) if text else {}
    except ValueError:
        return status, text


def _summarise(status, payload):
    """What comes back into the conversation: readable, and trimmed."""
    if isinstance(payload, (dict, list)):
        text = json.dumps(payload, indent=2, ensure_ascii=False)
    else:
        text = str(payload)

    if len(text) > MAX_RESPONSE_CHARS:
        text = text[:MAX_RESPONSE_CHARS] + "\n... [trimmed]"
    return f"HTTP {status}\n{text}"


def _run(method, url, body=None, params=None, auth_secret="", auth_style="bearer",
         transport=None):
    try:
        status, payload = call(method, url, body=body, params=params,
                               auth_secret=auth_secret, auth_style=auth_style,
                               transport=transport)
    except WebApiError as error:
        return str(error)
    except Exception as error:
        logger.exception("API call failed")
        return f"That call didn't work: {error}"

    return _summarise(status, payload)


# -- the two tools ----------------------------------------------------------
# Reading and changing are separate tools rather than one with a method
# argument, because they carry different risk and the check is per tool.

def web_api_get(url, params=None, auth_secret="", auth_style="bearer",
                _transport=None):
    """Read from any API: list issues, fetch a record, check a status."""
    return _run("GET", url, params=_as_dict(params), auth_secret=auth_secret,
                auth_style=auth_style, transport=_transport)


def web_api_call(method, url, body=None, params=None, auth_secret="",
                 auth_style="bearer", _transport=None):
    """Change something through an API: create, update, comment, delete."""
    if str(method).upper() in SAFE_METHODS:
        return web_api_get(url, params=params, auth_secret=auth_secret,
                           auth_style=auth_style, _transport=_transport)

    return _run(method, url, body=_as_dict(body), params=_as_dict(params),
                auth_secret=auth_secret, auth_style=auth_style,
                transport=_transport)


def _as_dict(value):
    """Models sometimes send JSON as a string. Accept both."""
    if value is None or isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else None
        except ValueError:
            return None
    return None
