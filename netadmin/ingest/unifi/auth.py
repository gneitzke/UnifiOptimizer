"""UniFi controller authentication: three strategies behind one interface.

Auto-detected in this order (ARCHITECTURE.md 5.1):

1. :class:`ApiKeyAuth` -- ``X-API-KEY`` against ``/proxy/network/api/...``
   (UniFi OS consoles, Network 9.x). Stateless, revocable, no CSRF dance.
2. :class:`UnifiOsCookieAuth` -- cookie + CSRF against ``/proxy/network/api/...``
   via ``POST /api/auth/login`` (UniFi OS password login, e.g. CloudKey Gen2).
3. :class:`LegacyCookieAuth` -- cookie against ``:8443/api/...`` via
   ``POST /api/login`` (legacy self-hosted controllers).

The quirks reverse-engineered in ``api/cloudkey_gen2_client.py``,
``api/csrf_token_manager.py`` and ``api/cloudkey_jwt_helper.py`` are salvaged
here rather than imported (the old package stays untouched):

* CSRF token captured from the ``X-CSRF-Token`` response header, and -- when the
  header is absent -- decoded out of the ``TOKEN`` JWT cookie payload.
* ``X-CSRF-Token`` echoed on every mutating request (UniFi OS rejects any
  POST/PUT/DELETE without it, including read-shaped POSTs like ``stat/report``).
* HTTP 499 on login means 2FA is required -> :class:`TwoFactorRequired`.
* UniFi OS vs legacy path detection via a login-free probe.
* ``verify_ssl=False`` default for self-signed certs, warning suppressed once
  (see :mod:`netadmin.ingest.unifi.client`), never globally.
"""

from __future__ import annotations

import abc
import base64
import binascii
import json
from typing import Any, Optional

import httpx

from netadmin.logging import get_logger

logger = get_logger("ingest.unifi.auth")

# HTTP methods that mutate server state and therefore must carry X-CSRF-Token on
# UniFi OS. stat/report, stat/event, stat/session are POSTs, so they are here.
_MUTATING = frozenset({"POST", "PUT", "DELETE", "PATCH"})

# UniFi OS returns this (non-standard) status on a login that needs 2FA.
HTTP_2FA_REQUIRED = 499

# JWT cookie names that may carry a CSRF claim.
_JWT_COOKIE_NAMES = ("TOKEN", "AUTH_TOKEN")
# Cookie names that directly hold a CSRF token.
_CSRF_COOKIE_NAMES = ("csrf_token", "csrfToken", "csrf", "X-CSRF-Token", "_csrf")


class UnifiError(Exception):
    """Base class for all UniFi client errors."""


class UnifiConnectionError(UnifiError):
    """Controller unreachable / transport failure."""


class UnifiAuthError(UnifiError):
    """Authentication was refused or could not be established."""


class TwoFactorRequired(UnifiAuthError):
    """Login needs a 2FA token (HTTP 499). Cannot proceed non-interactively."""


# --------------------------------------------------------------------------- #
# JWT / CSRF salvage helpers (ported from api/cloudkey_jwt_helper.py)
# --------------------------------------------------------------------------- #
def parse_jwt(token: str) -> Optional[dict[str, Any]]:
    """Decode a JWT payload (middle segment) without verifying the signature.

    UniFi OS signs its own tokens; we only need the ``csrfToken`` claim, so a
    signature check would need a key we do not have and buys nothing here.
    """
    if not token or token.count(".") != 2:
        return None
    payload_b64 = token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)  # restore base64 padding
    try:
        decoded = base64.urlsafe_b64decode(payload_b64)
        obj = json.loads(decoded)
    except (binascii.Error, ValueError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def csrf_from_jwt(token: str) -> Optional[str]:
    """Extract a CSRF token from a JWT payload, if present."""
    payload = parse_jwt(token)
    if not payload:
        return None
    for key in ("csrfToken", "csrf"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    for key, value in payload.items():
        if "csrf" in key.lower() and isinstance(value, str) and value:
            return value
    return None


def csrf_from_cookies(cookies: httpx.Cookies) -> Optional[str]:
    """Extract a CSRF token from a cookie jar: direct cookie, then JWT payload."""
    for name in _CSRF_COOKIE_NAMES:
        value = cookies.get(name)
        if value:
            return value
    for name in _JWT_COOKIE_NAMES:
        jwt = cookies.get(name)
        if jwt:
            found = csrf_from_jwt(jwt)
            if found:
                return found
    return None


# --------------------------------------------------------------------------- #
# Strategy interface
# --------------------------------------------------------------------------- #
class AuthStrategy(abc.ABC):
    """One way to authenticate to, and address, a UniFi controller."""

    name: str = "base"
    #: True once :meth:`authenticate` has succeeded.
    authenticated: bool = False

    @abc.abstractmethod
    async def authenticate(self, http: httpx.AsyncClient) -> None:
        """Establish auth on ``http`` (login and/or verify). Raise on failure."""

    @abc.abstractmethod
    def api_url(self, host: str, site: str, endpoint: str) -> str:
        """Full URL for a site endpoint, e.g. ``stat/device`` -> proxy path."""

    @abc.abstractmethod
    def ws_url(self, host: str, site: str) -> str:
        """WebSocket URL for the site event stream."""

    def request_headers(self, method: str) -> dict[str, str]:
        """Per-request auth headers (API key always, CSRF for mutating verbs)."""
        return {}

    def capture(self, response: httpx.Response, cookies: httpx.Cookies) -> None:
        """Refresh rotating auth material (CSRF) from a response. Optional."""

    def ws_headers(self, cookies: httpx.Cookies) -> dict[str, str]:
        """Headers the WebSocket handshake needs to reuse this session."""
        return {}


def _ws_scheme(host: str) -> str:
    return host.replace("https://", "wss://", 1).replace("http://", "ws://", 1)


# --------------------------------------------------------------------------- #
# 1. API key
# --------------------------------------------------------------------------- #
class ApiKeyAuth(AuthStrategy):
    """``X-API-KEY`` against UniFi OS ``/proxy/network/api`` paths (preferred)."""

    name = "api_key"

    def __init__(self, host: str, site: str, api_key: str) -> None:
        self._host = host.rstrip("/")
        self._site = site
        self._api_key = api_key

    async def authenticate(self, http: httpx.AsyncClient) -> None:
        # No login; verify the key against a cheap read endpoint.
        url = self.api_url(self._host, self._site, "stat/health")
        try:
            resp = await http.get(url, headers=self.request_headers("GET"))
        except httpx.HTTPError as exc:  # pragma: no cover - transport
            raise UnifiConnectionError(f"API-key verification failed: {exc}") from exc
        if resp.status_code in (401, 403):
            raise UnifiAuthError(f"API key rejected ({resp.status_code}).")
        if resp.status_code >= 400:
            raise UnifiAuthError(f"API key verification returned {resp.status_code}.")
        self.authenticated = True
        logger.info("Authenticated via X-API-KEY.")

    def api_url(self, host: str, site: str, endpoint: str) -> str:
        return f"{host.rstrip('/')}/proxy/network/api/s/{site}/{endpoint.lstrip('/')}"

    def ws_url(self, host: str, site: str) -> str:
        return f"{_ws_scheme(host.rstrip('/'))}/proxy/network/wss/s/{site}/events"

    def request_headers(self, method: str) -> dict[str, str]:
        return {"X-API-KEY": self._api_key, "Accept": "application/json"}

    def ws_headers(self, cookies: httpx.Cookies) -> dict[str, str]:
        return {"X-API-KEY": self._api_key}


# --------------------------------------------------------------------------- #
# 2. UniFi OS cookie + CSRF
# --------------------------------------------------------------------------- #
class _CookieAuthBase(AuthStrategy):
    """Shared password-login machinery for UniFi OS and legacy controllers."""

    login_path: str = "/api/auth/login"

    def __init__(self, host: str, site: str, username: str, password: str) -> None:
        self._host = host.rstrip("/")
        self._site = site
        self._username = username
        self._password = password
        self._csrf: Optional[str] = None

    async def authenticate(self, http: httpx.AsyncClient) -> None:
        url = f"{self._host}{self.login_path}"
        body = {"username": self._username, "password": self._password, "remember": True}
        try:
            resp = await http.post(url, json=body)
        except httpx.HTTPError as exc:  # pragma: no cover - transport
            raise UnifiConnectionError(f"Login request failed: {exc}") from exc

        if resp.status_code == HTTP_2FA_REQUIRED or self._is_2fa_body(resp):
            raise TwoFactorRequired(
                "Controller requires a 2FA token; non-interactive login cannot proceed. "
                "Use an API key or a local-only admin without 2FA."
            )
        if resp.status_code in (400, 401):
            raise UnifiAuthError(f"Login rejected ({resp.status_code}). Check credentials.")
        if resp.status_code >= 400:
            raise UnifiAuthError(f"Login failed ({resp.status_code}).")

        self.capture(resp, http.cookies)
        self.authenticated = True
        source = "header" if resp.headers.get("X-CSRF-Token") else "jwt/cookie"
        logger.info("Authenticated via %s cookie login (CSRF from %s).", self.name, source)

    @staticmethod
    def _is_2fa_body(resp: httpx.Response) -> bool:
        try:
            data = resp.json()
        except (ValueError, json.JSONDecodeError):
            return False
        marker = json.dumps(data).lower()
        return "2fa" in marker or "ubic2fa" in marker

    def capture(self, response: httpx.Response, cookies: httpx.Cookies) -> None:
        header = response.headers.get("X-CSRF-Token")
        if header:
            self._csrf = header
            return
        # Header absent (the CloudKey quirk): pull it out of the response first,
        # then the persistent jar (JWT TOKEN cookie carries the csrfToken claim).
        found = csrf_from_cookies(response.cookies) or csrf_from_cookies(cookies)
        if found:
            self._csrf = found

    def request_headers(self, method: str) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if method.upper() in _MUTATING and self._csrf:
            headers["X-CSRF-Token"] = self._csrf
        return headers

    def ws_headers(self, cookies: httpx.Cookies) -> dict[str, str]:
        # Reuse the session cookies for the WebSocket handshake.
        pairs = "; ".join(f"{name}={value}" for name, value in cookies.items())
        headers: dict[str, str] = {}
        if pairs:
            headers["Cookie"] = pairs
        if self._csrf:
            headers["X-CSRF-Token"] = self._csrf
        return headers


class UnifiOsCookieAuth(_CookieAuthBase):
    """UniFi OS password login: ``/api/auth/login`` + ``/proxy/network`` paths."""

    name = "unifi_os_cookie"
    login_path = "/api/auth/login"

    def api_url(self, host: str, site: str, endpoint: str) -> str:
        return f"{host.rstrip('/')}/proxy/network/api/s/{site}/{endpoint.lstrip('/')}"

    def ws_url(self, host: str, site: str) -> str:
        return f"{_ws_scheme(host.rstrip('/'))}/proxy/network/wss/s/{site}/events"


class LegacyCookieAuth(_CookieAuthBase):
    """Legacy self-hosted controller: ``/api/login`` on ``:8443``, no proxy."""

    name = "legacy_cookie"
    login_path = "/api/login"

    def api_url(self, host: str, site: str, endpoint: str) -> str:
        return f"{host.rstrip('/')}/api/s/{site}/{endpoint.lstrip('/')}"

    def ws_url(self, host: str, site: str) -> str:
        return f"{_ws_scheme(host.rstrip('/'))}/wss/s/{site}/events"


# --------------------------------------------------------------------------- #
# Auto-detection
# --------------------------------------------------------------------------- #
async def _is_unifi_os(http: httpx.AsyncClient, host: str) -> bool:
    """Login-free discriminator: does ``/proxy/network`` exist on this host?

    UniFi OS consoles answer the proxy path (200/302/401/403); a legacy
    controller has no such path and returns 404 or refuses the connection. This
    deliberately spends no login attempt, which CloudKey rate-limits hard.
    """
    try:
        resp = await http.get(f"{host.rstrip('/')}/proxy/network/", follow_redirects=False)
    except httpx.HTTPError:
        return False
    return resp.status_code in (200, 301, 302, 401, 403)


async def resolve_strategy(
    http: httpx.AsyncClient,
    *,
    host: str,
    site: str,
    username: Optional[str],
    password: Optional[str],
    api_key: Optional[str],
) -> AuthStrategy:
    """Detect and return an authenticated strategy, in the section 5.1 order.

    API key is tried first when present (a read verification, not a login).
    Cookie auth then picks UniFi OS vs legacy via a login-free probe, so at most
    one login POST is spent for the whole session.
    """
    host = host.rstrip("/")

    if api_key:
        strat: AuthStrategy = ApiKeyAuth(host, site, api_key)
        try:
            await strat.authenticate(http)
            return strat
        except UnifiAuthError as exc:
            logger.warning("API key present but rejected (%s); falling back to cookie.", exc)

    if not (username and password):
        raise UnifiAuthError("No usable credentials: need an API key or username+password.")

    cookie: AuthStrategy
    if await _is_unifi_os(http, host):
        cookie = UnifiOsCookieAuth(host, site, username, password)
    else:
        cookie = LegacyCookieAuth(host, site, username, password)
    await cookie.authenticate(http)
    return cookie


__all__ = [
    "UnifiError",
    "UnifiConnectionError",
    "UnifiAuthError",
    "TwoFactorRequired",
    "AuthStrategy",
    "ApiKeyAuth",
    "UnifiOsCookieAuth",
    "LegacyCookieAuth",
    "resolve_strategy",
    "parse_jwt",
    "csrf_from_jwt",
    "csrf_from_cookies",
    "HTTP_2FA_REQUIRED",
]
