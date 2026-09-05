"""Async UniFi controller client (ARCHITECTURE.md 5.1).

Thin wrapper over one :class:`httpx.AsyncClient` that owns:

* auth-strategy detection and the single shared session (cookies/API key),
* retry-with-backoff on 5xx and connect/read errors,
* a single re-login on 401, then fail,
* gentle request pacing (heavy ``stat/report`` queries are Mongo aggregations
  on a CloudKey; do not hammer it),
* ``verify_ssl=False`` by default for self-signed certs, with the insecure-TLS
  warning emitted once, never disabled globally at import.

Read-endpoint wrappers live in :mod:`netadmin.ingest.unifi.endpoints`, which
takes a connected :class:`UnifiClient`. This module never constructs models and
never touches SQL.
"""

from __future__ import annotations

import asyncio
import time
import warnings
from types import TracebackType
from typing import Any, Optional

import httpx

from netadmin.logging import get_logger

from .auth import (
    DEFAULT_AUTH_COOLDOWN_SECONDS,
    AuthStrategy,
    UnifiAmbiguousOutcomeError,
    UnifiAuthCooldownError,
    UnifiAuthError,
    UnifiConnectionError,
    UnifiError,
    resolve_strategy,
)

logger = get_logger("ingest.unifi.client")

_RETRYABLE_STATUS = frozenset({500, 502, 503, 504})
_RETRYABLE_EXC = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)


def envelope_error(payload: Any) -> Optional[str]:
    """Return the UniFi error message when the classic envelope reports failure.

    The classic UniFi envelope is ``{"meta": {"rc": "ok"|"error", "msg": ...},
    "data": [...]}``. An explicit ``meta.rc == "error"`` is a failure regardless
    of the HTTP status: the controller answers ``200`` with an error envelope
    (e.g. ``api.err.InvalidObject``) and the transport looks healthy. Shared by
    the read path (:meth:`UnifiClient._parse`) and the write path
    (:meth:`netadmin.fixes.writer.RealControllerWriter._send`) so a read never
    returns rows and a write never reports success on an envelope that says the
    operation failed (the R1 finding). Returns ``None`` when there is no explicit
    error to report (``rc`` absent or ``ok``, or a non-dict body).
    """
    if not isinstance(payload, dict):
        return None
    meta = payload.get("meta")
    if isinstance(meta, dict) and str(meta.get("rc", "")).strip().lower() == "error":
        return str(meta.get("msg") or "api.err (unspecified)")
    return None


class UnifiClient:
    """A connected, authenticated async client for one controller + site."""

    _ssl_warning_emitted = False

    def __init__(
        self,
        *,
        host: str,
        site: str = "default",
        username: Optional[str] = None,
        password: Optional[str] = None,
        api_key: Optional[str] = None,
        verify_ssl: bool = False,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        backoff_max: float = 8.0,
        min_request_interval: float = 0.1,
    ) -> None:
        if not host:
            raise ValueError("host is required")
        self._host = host.rstrip("/")
        self._site = site
        self._username = username
        self._password = password
        self._api_key = api_key
        self._verify_ssl = verify_ssl
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._min_interval = min_request_interval

        if not verify_ssl:
            self._suppress_insecure_warning()

        # follow_redirects=False is a security rail (S4): the REST client carries
        # the controller credential (X-API-KEY header, or the session cookie). httpx
        # replays request headers across a redirect, so a redirect to another origin
        # -- easy to induce when TLS verification is disabled for self-signed certs --
        # would forward that credential off-controller. We never chase redirects on
        # an authenticated controller request; a 3xx surfaces as a response the
        # caller can inspect, and no secret leaves the controller origin.
        self._http = httpx.AsyncClient(
            verify=verify_ssl,
            timeout=timeout,
            follow_redirects=False,
        )
        self._strategy: Optional[AuthStrategy] = None
        # A separate cookie strategy + its own http client for the events
        # WebSocket, which rejects API-key auth (see :meth:`ws_strategy`). Kept
        # off the REST client so the cookie session cannot taint REST. None until
        # first needed, and only ever created when REST is API-key based.
        self._ws_strategy: Optional[AuthStrategy] = None
        self._ws_http: Optional[httpx.AsyncClient] = None
        self._auth_lock = asyncio.Lock()
        # Authentication failures are session-wide state, just like successful
        # authentication. The deadline is guarded by _auth_lock so callers queued
        # behind one rejected login observe the same failure instead of each
        # spending another controller login attempt.
        self._auth_cooldown_until = 0.0
        self._auth_failure_message: Optional[str] = None
        self._pace_lock = asyncio.Lock()
        self._last_request_ts = 0.0
        # Monotonic counter bumped on every successful (re)login. A request that
        # hits a 401 remembers the epoch it authenticated under; the re-login
        # guard uses it to collapse a burst of concurrent 401s (all authed under
        # the same epoch) into a single re-login instead of one per request.
        self._login_epoch = 0

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    @classmethod
    def _suppress_insecure_warning(cls) -> None:
        """Silence the self-signed-cert warning exactly once (not globally)."""
        if cls._ssl_warning_emitted:
            return
        cls._ssl_warning_emitted = True
        logger.warning(
            "TLS verification disabled (verify_ssl=False) for self-signed "
            "controller certificates. This is expected for CloudKey/UDM."
        )
        try:  # scope the urllib3 filter to this category, do not disable all warnings
            from urllib3.exceptions import InsecureRequestWarning

            warnings.filterwarnings("ignore", category=InsecureRequestWarning)
        except Exception:  # pragma: no cover - urllib3 optional
            pass

    async def __aenter__(self) -> "UnifiClient":
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()
        if self._ws_http is not None:
            await self._ws_http.aclose()

    @property
    def site(self) -> str:
        return self._site

    @property
    def host(self) -> str:
        return self._host

    @property
    def strategy(self) -> Optional[AuthStrategy]:
        return self._strategy

    @property
    def http(self) -> httpx.AsyncClient:
        """The underlying client (used by the WS listener to reuse cookies)."""
        return self._http

    async def connect(self) -> AuthStrategy:
        """Detect auth strategy and log in (idempotent, concurrency-safe)."""
        async with self._auth_lock:
            if self._strategy is not None and self._strategy.authenticated:
                return self._strategy
            return await self._connect_locked()

    def _active_auth_cooldown(self) -> Optional[UnifiAuthCooldownError]:
        """Return the shared cooldown error, if its monotonic deadline is active.

        Must be called while holding :attr:`_auth_lock`.
        """
        remaining = self._auth_cooldown_until - time.monotonic()
        if remaining <= 0:
            self._auth_cooldown_until = 0.0
            self._auth_failure_message = None
            return None
        return UnifiAuthCooldownError(remaining, self._auth_failure_message)

    def _remember_auth_failure(self, exc: UnifiAuthError) -> None:
        """Establish one shared quiet period after a refused auth attempt."""
        delay = (
            exc.retry_after
            if isinstance(exc, UnifiAuthCooldownError)
            else DEFAULT_AUTH_COOLDOWN_SECONDS
        )
        self._auth_cooldown_until = max(
            self._auth_cooldown_until, time.monotonic() + max(0.0, delay)
        )
        self._auth_failure_message = (
            exc.reason if isinstance(exc, UnifiAuthCooldownError) else str(exc)
        )

    def _clear_auth_failure(self) -> None:
        self._auth_cooldown_until = 0.0
        self._auth_failure_message = None

    async def _connect_locked(self) -> AuthStrategy:
        """Connect with ``_auth_lock`` held, sharing success and failure state."""
        cooldown = self._active_auth_cooldown()
        if cooldown is not None:
            raise cooldown
        try:
            strategy = await resolve_strategy(
                self._http,
                host=self._host,
                site=self._site,
                username=self._username,
                password=self._password,
                api_key=self._api_key,
            )
        except UnifiAuthError as exc:
            self._remember_auth_failure(exc)
            raise
        self._strategy = strategy
        self._clear_auth_failure()
        self._login_epoch += 1
        return strategy

    async def _authenticate_locked(self, strategy: AuthStrategy, http: httpx.AsyncClient) -> None:
        """Authenticate an existing strategy with shared cooldown handling."""
        cooldown = self._active_auth_cooldown()
        if cooldown is not None:
            raise cooldown
        try:
            await strategy.authenticate(http)
        except UnifiAuthError as exc:
            self._remember_auth_failure(exc)
            raise
        self._clear_auth_failure()

    async def _relogin(self, observed_epoch: Optional[int] = None) -> None:
        """Force a fresh login on the current strategy (401 recovery).

        ``observed_epoch`` is the login epoch the caller was authenticated under
        when it hit the 401. If, by the time this acquires the auth lock, the
        epoch has already advanced, another concurrent caller re-logged in for
        this same session generation -- so this one returns without a second
        login. That collapses N simultaneous 401s (the whole poll fan-out failing
        at once on an expired session) into exactly one re-login rather than N
        hammering a rate-limited CloudKey. Passing ``None`` always re-logs in
        (the WS listener's explicit :meth:`relogin`).
        """
        async with self._auth_lock:
            if observed_epoch is not None and observed_epoch != self._login_epoch:
                return  # someone already re-logged in for this epoch; reuse it
            if self._strategy is None:
                await self._connect_locked()
                return
            self._strategy.authenticated = False
            await self._authenticate_locked(self._strategy, self._http)
            self._login_epoch += 1

    async def ws_strategy(self, *, force_reauth: bool = False) -> AuthStrategy:
        """An auth strategy for the EVENTS WebSocket, which needs a cookie session.

        UniFi's events socket rejects ``X-API-KEY``: it accepts the handshake
        then closes 1000 with no frames. The API key stays correct for REST, so
        when REST authenticates by key but a username and password are also
        configured, the WS uses a cookie login instead. A cookie strategy already
        in use for REST is reused as-is. With only an API key, events cannot be
        subscribed on this controller, and this raises :class:`UnifiAuthError`
        with guidance so the listener degrades cleanly rather than looping --
        history and detection keep working, only live events are unavailable.
        """
        # Deferred import: these live in the same package; a module-level import
        # would be circular through resolve_strategy's own imports.
        from .auth import LegacyCookieAuth, UnifiOsCookieAuth, _CookieAuthBase, _is_unifi_os

        await self.connect()
        async with self._auth_lock:
            if isinstance(self._strategy, _CookieAuthBase):
                # REST already cookie-based; one session serves both. Still honour
                # a forced re-auth by re-running the login on that shared strategy.
                if force_reauth:
                    self._strategy.authenticated = False
                    await self._authenticate_locked(self._strategy, self._http)
                return self._strategy
            if not (self._username and self._password):
                raise UnifiAuthError(
                    "The events WebSocket needs a username and password: UniFi's "
                    "events socket rejects API-key auth. History and detection "
                    "keep working; live events stay unavailable until a password "
                    "login is configured."
                )
            if (
                not force_reauth
                and self._ws_strategy is not None
                and self._ws_strategy.authenticated
            ):
                return self._ws_strategy
            # The cookie login goes on a SEPARATE http client, never the REST one.
            # Logging in for a cookie session sets a TOKEN cookie and a CSRF token;
            # left on the shared REST client alongside the API key, that mixed auth
            # state makes the controller 403 the older cookie-checked endpoints
            # (list/alarm, stat/event, stat/rogueap) with "session lost" while the
            # API-key endpoints keep working. Isolating the WS session keeps REST
            # pure API-key. Reads its cookies via :attr:`ws_cookies`.
            if self._ws_http is None:
                self._ws_http = httpx.AsyncClient(
                    verify=self._verify_ssl, timeout=self._http.timeout, follow_redirects=False
                )
            cookie_cls = (
                UnifiOsCookieAuth
                if await _is_unifi_os(self._ws_http, self._host)
                else LegacyCookieAuth
            )
            cookie = cookie_cls(self._host, self._site, self._username, self._password)
            await self._authenticate_locked(cookie, self._ws_http)
            self._ws_strategy = cookie
            return cookie

    @property
    def ws_cookies(self) -> httpx.Cookies:
        """Cookie jar the events WebSocket handshake should use.

        The isolated WS session's jar when REST uses an API key, else the shared
        one (REST already cookie-based -- one session serves both).
        """
        return self._ws_http.cookies if self._ws_http is not None else self._http.cookies

    async def relogin(self) -> AuthStrategy:
        """Force a fresh login and return the re-authenticated strategy.

        Unlike :meth:`connect` -- which is idempotent and early-returns the
        cached strategy while ``authenticated`` is still True -- this always
        re-runs authentication, so a caller recovering from a 401/403 (e.g. the
        WebSocket listener after an expired session token) gets fresh session
        material instead of the stale strategy. Raises
        :class:`~netadmin.ingest.unifi.auth.UnifiAuthError` if re-auth fails.
        """
        await self._relogin()
        assert self._strategy is not None  # _relogin establishes it or raises
        return self._strategy

    # ------------------------------------------------------------------ #
    # core request path
    # ------------------------------------------------------------------ #
    async def _pace(self) -> None:
        """Enforce a minimum gap between outbound requests."""
        if self._min_interval <= 0:
            return
        async with self._pace_lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_request_ts)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_ts = time.monotonic()

    def _backoff(self, attempt: int) -> float:
        return min(self._backoff_base * (2**attempt), self._backoff_max)

    async def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[Any] = None,
        allow_mutation: bool = False,
    ) -> httpx.Response:
        """Issue an authenticated request to a site endpoint.

        ``endpoint`` is a site-relative path such as ``stat/device`` or
        ``stat/report/hourly.ap``; the strategy resolves the full URL.

        This is the collector transport boundary and it enforces the GET-only
        contract (S1): routine collection may issue **only** idempotent GETs.
        A non-GET verb is refused unless the caller sets ``allow_mutation=True``,
        which exactly one production object does -- the approved fix writer
        (:class:`netadmin.fixes.writer.RealControllerWriter`), the single seam
        allowed to change the controller. Any other non-GET is a contract
        violation and raises :class:`UnifiError` before a socket opens.

        Retry policy is method-aware (C2). GETs are idempotent, so a transport
        error or a retryable 5xx is retried with backoff. A mutation is **never**
        auto-retried on an ambiguous transport failure: a lost response could
        otherwise dispatch the same write several times. Such a mutation raises
        :class:`UnifiAmbiguousOutcomeError` after a single attempt so the caller
        keeps the before-state and reconciles via a GET before trying again.
        """
        method_u = method.upper()
        idempotent = method_u == "GET"
        if not idempotent and not allow_mutation:
            raise UnifiError(
                f"GET-only contract: refusing {method_u} {endpoint}. The collector is "
                "read-only; only the approved fix writer may issue a controller mutation."
            )

        strategy = await self.connect()
        url = strategy.api_url(self._host, self._site, endpoint)
        # The epoch we authenticated under; the 401 guard uses it so a burst of
        # concurrent 401s on the same session produces one re-login, not one each.
        login_epoch = self._login_epoch
        relogged = False
        attempt = 0

        while True:
            headers = strategy.request_headers(method)
            await self._pace()
            try:
                resp = await self._http.request(
                    method, url, params=params, json=json_body, headers=headers
                )
            except _RETRYABLE_EXC as exc:
                if not idempotent:
                    # Ambiguous outcome on a mutation: do NOT retry (C2). The write
                    # may already have landed; retrying could apply it again.
                    raise UnifiAmbiguousOutcomeError(
                        f"{method_u} {endpoint} outcome unknown "
                        f"({type(exc).__name__}: {exc}); not retried. Reconcile "
                        "controller state via GET before any further attempt."
                    ) from exc
                if attempt >= self._max_retries:
                    raise UnifiConnectionError(
                        f"{method_u} {endpoint} failed after {attempt + 1} attempts: {exc}"
                    ) from exc
                delay = self._backoff(attempt)
                logger.warning(
                    "%s %s transport error (%s); retry %d in %.1fs",
                    method_u,
                    endpoint,
                    type(exc).__name__,
                    attempt + 1,
                    delay,
                )
                attempt += 1
                await asyncio.sleep(delay)
                continue

            strategy.capture(resp, self._http.cookies)

            if resp.status_code == 401 and not relogged:
                # A 401 is an unambiguous rejection: the request was not applied,
                # so a single re-login and retry is safe even for a mutation.
                logger.info("%s %s -> 401; re-logging in once.", method_u, endpoint)
                relogged = True
                await self._relogin(login_epoch)
                login_epoch = self._login_epoch  # adopt whichever login now stands
                continue

            # Retryable 5xx: only GETs are retried. A mutation that draws a 5xx is
            # a definite server-side failure (a received response, not a lost one),
            # so it is surfaced to the caller as a non-2xx outcome, never retried.
            if (
                idempotent
                and resp.status_code in _RETRYABLE_STATUS
                and attempt < self._max_retries
            ):
                delay = self._backoff(attempt)
                logger.warning(
                    "%s %s -> %d; retry %d in %.1fs",
                    method_u,
                    endpoint,
                    resp.status_code,
                    attempt + 1,
                    delay,
                )
                attempt += 1
                await asyncio.sleep(delay)
                continue

            return resp

    # ------------------------------------------------------------------ #
    # JSON helpers (classic UniFi envelope: {"meta": {...}, "data": [...]})
    # ------------------------------------------------------------------ #
    # There is deliberately no ``post_json`` / ``post_data`` helper: the collector
    # is GET-only (S1), so a read helper that POSTs would be a contract violation
    # waiting to be called. The one legitimate controller mutation path is the fix
    # writer, which calls :meth:`request` directly with ``allow_mutation=True``.
    async def get_json(
        self, endpoint: str, params: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        resp = await self.request("GET", endpoint, params=params)
        return self._parse(resp, endpoint)

    async def get_data(
        self, endpoint: str, params: Optional[dict[str, Any]] = None
    ) -> list[dict[str, Any]]:
        return self._data(await self.get_json(endpoint, params))

    def _parse(self, resp: httpx.Response, endpoint: str) -> dict[str, Any]:
        if resp.status_code in (401, 403):
            raise UnifiAuthError(f"{endpoint} -> {resp.status_code} (auth). Session lost.")
        if resp.status_code >= 400:
            raise UnifiError(f"{endpoint} -> {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise UnifiError(f"{endpoint} returned non-JSON response") from exc
        if not isinstance(data, dict):
            raise UnifiError(f"{endpoint} returned unexpected JSON shape")
        # An explicit error envelope is a failure even on HTTP 200 (R1). Without
        # this, a ``{"meta":{"rc":"error"},"data":[]}`` body would quietly parse to
        # zero rows and read as an empty-but-healthy result.
        err = envelope_error(data)
        if err is not None:
            raise UnifiError(f"{endpoint} -> {err}")
        return data

    @staticmethod
    def _data(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data", [])
        if isinstance(data, list):
            return data
        return [data] if data else []


__all__ = ["UnifiClient", "envelope_error"]
