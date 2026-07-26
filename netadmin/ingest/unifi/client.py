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

from .auth import AuthStrategy, UnifiAuthError, UnifiConnectionError, UnifiError, resolve_strategy

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

        self._http = httpx.AsyncClient(
            verify=verify_ssl,
            timeout=timeout,
            follow_redirects=True,
        )
        self._strategy: Optional[AuthStrategy] = None
        self._auth_lock = asyncio.Lock()
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
            self._strategy = await resolve_strategy(
                self._http,
                host=self._host,
                site=self._site,
                username=self._username,
                password=self._password,
                api_key=self._api_key,
            )
            self._login_epoch += 1
            return self._strategy

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
                await self.connect()
                return
            self._strategy.authenticated = False
            await self._strategy.authenticate(self._http)
            self._login_epoch += 1

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
    ) -> httpx.Response:
        """Issue an authenticated request to a site endpoint with retries.

        ``endpoint`` is a site-relative path such as ``stat/device`` or
        ``stat/report/hourly.ap``; the strategy resolves the full URL.
        """
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
                if attempt >= self._max_retries:
                    raise UnifiConnectionError(
                        f"{method} {endpoint} failed after {attempt + 1} attempts: {exc}"
                    ) from exc
                delay = self._backoff(attempt)
                logger.warning(
                    "%s %s transport error (%s); retry %d in %.1fs",
                    method,
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
                logger.info("%s %s -> 401; re-logging in once.", method, endpoint)
                relogged = True
                await self._relogin(login_epoch)
                login_epoch = self._login_epoch  # adopt whichever login now stands
                continue

            if resp.status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                delay = self._backoff(attempt)
                logger.warning(
                    "%s %s -> %d; retry %d in %.1fs",
                    method,
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
    async def get_json(
        self, endpoint: str, params: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        resp = await self.request("GET", endpoint, params=params)
        return self._parse(resp, endpoint)

    async def post_json(self, endpoint: str, body: Optional[Any] = None) -> dict[str, Any]:
        resp = await self.request("POST", endpoint, json_body=body if body is not None else {})
        return self._parse(resp, endpoint)

    async def get_data(
        self, endpoint: str, params: Optional[dict[str, Any]] = None
    ) -> list[dict[str, Any]]:
        return self._data(await self.get_json(endpoint, params))

    async def post_data(self, endpoint: str, body: Optional[Any] = None) -> list[dict[str, Any]]:
        return self._data(await self.post_json(endpoint, body))

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
        return data

    @staticmethod
    def _data(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data", [])
        if isinstance(data, list):
            return data
        return [data] if data else []


__all__ = ["UnifiClient"]
