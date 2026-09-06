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
# Transport exceptions whose OUTCOME is uncertain. On an idempotent GET each is
# retried with backoff; on a MUTATION each surfaces as an ambiguous outcome (never
# retried, never a definitive failure). ``ReadError``/``WriteError`` are raised when
# the socket faults AFTER the request bytes were sent -- so on a mutation the write
# may already have landed (D6): they belong here exactly like ``ReadTimeout``, not
# leaking out as an unhandled failure the caller records as a clean "failed".
_RETRYABLE_EXC = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
)
# Failures raised AFTER the full request was already sent and the controller
# answered, while READING / DECODING / FINISHING (closing) the response:
#   * ``DecodingError`` -- a corrupt/undecodable body (bad gzip, a truncated chunked
#     stream); a ``RequestError`` that is deliberately NOT a ``TransportError`` and so
#     is absent from ``_RETRYABLE_EXC`` above (D6/#w10a-1);
#   * ``CloseError`` -- the response yielded its success bytes, then the socket faulted
#     during cleanup/close (#w13a-5). A ``NetworkError`` that is deliberately kept OUT
#     of ``_RETRYABLE_EXC`` (unlike ``ReadError``/``WriteError``) so a GET does NOT
#     silently retry a response that already delivered its body; on a mutation it is
#     classified here as post-send ambiguous, exactly like a decode failure.
# In every one of these the request landed at the controller (it replied) -- only the
# tail of receiving/finishing the reply failed -- so on a MUTATION the outcome is
# UNKNOWN, never a definitive failure: the write may well have taken. It is classified
# exactly like a lost response / an ambiguous 5xx -- ambiguous, never retried, never a
# clean "failed" the applier could replay. This is the concrete arm of a general rule:
# ANY exception raised after the request bytes were sent, without a parsed definitive
# rejection, is ambiguous for a mutation. A GET keeps its existing behavior: the error
# propagates to the caller unchanged (a read that cannot be finished is a read failure,
# and a GET is safely re-issued by the caller, not silently ambiguous).
_POST_SEND_READ_EXC = (httpx.DecodingError, httpx.CloseError)


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

    def _finish_mutation(
        self,
        strategy: AuthStrategy,
        resp: httpx.Response,
        method_u: str,
        endpoint: str,
    ) -> httpx.Response:
        """Finish a MUTATION's response; any post-send failure is AMBIGUOUS (#w14a-1).

        Single-dispatch by construction -- a mutation is NEVER retried or
        re-dispatched here. Everything after the request bytes were sent runs under
        ONE guard: cookie ``capture``, the 401 envelope parse, response close. The
        rule is structural, not an enumerated exception list: any exception raised in
        that window that is NOT a parsed definitive rejection means the write may have
        landed, so it surfaces as :class:`UnifiAmbiguousOutcomeError` (never reaching
        the applier's generic 'failed' handler, never replayed). Only a PARSED
        controller rejection (``meta.rc=error`` -- including on a 401, #w12a-1) is a
        DEFINITIVE failure, returned unchanged; a received 2xx/5xx is returned for the
        writer's unified envelope classification (a mutation 5xx is a received
        server-side failure, not a lost response, and is never retried).
        """
        try:
            strategy.capture(resp, self._http.cookies)
            if resp.status_code == 401:
                # #w12a-1: honour the envelope. A 401 carrying a PARSED rejection
                # (``meta.rc=error``) is a DEFINITIVE failure the controller confirmed;
                # return it for the writer's classification, do NOT launder it into
                # ambiguous. Only a 401 with NO parseable rejection is ambiguous: the
                # session dropped and the write may or may not have landed.
                try:
                    body = resp.json()
                except ValueError:
                    body = None
                if envelope_error(body) is not None:
                    logger.warning(
                        "%s %s -> 401 with a parsed rejection envelope; definitive "
                        "failure (single dispatch, not re-dispatched, not ambiguous).",
                        method_u,
                        endpoint,
                    )
                    return resp
                logger.warning(
                    "%s %s -> 401 on a mutation with no parseable rejection; "
                    "not re-dispatched (ambiguous).",
                    method_u,
                    endpoint,
                )
                raise UnifiAmbiguousOutcomeError(
                    f"{method_u} {endpoint} -> 401; the session was rejected and the "
                    "write may or may not have landed. Not re-dispatched. Reconcile "
                    "controller state via GET before any further attempt."
                )
            # A received 2xx / 5xx / other status: return unchanged. A mutation is
            # never retried on a 5xx (a received response, not a lost one); the writer
            # classifies the envelope.
            return resp
        except UnifiAmbiguousOutcomeError:
            # Already the deliberate ambiguous classification above -- do not re-wrap.
            raise
        except Exception as exc:  # noqa: BLE001 - structural: post-send == unknown
            # ANY other post-send processing failure (CookieConflict,
            # LocalProtocolError, a cleanup RuntimeError, CloseError, DecodingError,
            # ReadError, ...) means the write may have landed. Ambiguous, single
            # dispatch, never a clean 'failed' the applier could replay.
            logger.warning(
                "%s %s post-send response processing failed (%s: %s); mutation "
                "outcome unknown, single dispatch, not replayed.",
                method_u,
                endpoint,
                type(exc).__name__,
                exc,
            )
            raise UnifiAmbiguousOutcomeError(
                f"{method_u} {endpoint} post-send response processing failed "
                f"({type(exc).__name__}: {exc}); the request was sent and the write "
                "may have landed. Not retried, not a definitive failure. Reconcile "
                "controller state via GET before any further attempt."
            ) from exc

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
        keeps the before-state and reconciles via a GET before trying again. The
        same rule governs a 401 on a mutation (C2): only a GET is re-dispatched
        after the single re-login; a non-GET that draws a 401 raises
        :class:`UnifiAmbiguousOutcomeError` without a second dispatch.
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
            except _POST_SEND_READ_EXC as exc:
                # A read/decode failure of the RESPONSE body -- the request was fully
                # sent and the controller answered, only the answer could not be
                # decoded (#w10a-1). On a MUTATION the write may have landed, so this
                # is AMBIGUOUS, never a definitive failure: surface it exactly like a
                # lost response so the caller records "unknown" and reconciles via a
                # GET, and never retries (a retry could double-apply). A GET keeps its
                # existing behavior: the error propagates for the caller to re-issue.
                if not idempotent:
                    raise UnifiAmbiguousOutcomeError(
                        f"{method_u} {endpoint} response could not be read/decoded "
                        f"({type(exc).__name__}: {exc}); the request was sent and the "
                        "write may have landed. Not retried. Reconcile controller "
                        "state via GET before any further attempt."
                    ) from exc
                raise
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
            except Exception as exc:  # noqa: BLE001 - structural single-dispatch guard
                # #w15a-1 (STRUCTURAL, not enumerate-more): the request bytes were
                # DISPATCHED and the exception surfaced from WITHIN the request/response
                # await ITSELF -- e.g. a ``RuntimeError`` (or any non-enumerated error)
                # raised while httpx finishes the response, closing/``aclose``-ing the
                # stream AFTER the controller already delivered its ``meta.rc=ok`` bytes.
                # Such an exception is neither a pre-send transport error nor a body
                # read/decode error, so it slipped past ``_RETRYABLE_EXC`` /
                # ``_POST_SEND_READ_EXC`` and past ``_finish_mutation`` (which only
                # guards processing AFTER the await returns) and escaped ``request``
                # unclassified -- the applier then recorded a clean 'failed' and a
                # REPLAY dispatched a SECOND PUT. For a MUTATION the ENTIRE response
                # lifecycle (dispatch, read, context-manager exit / ``aclose`` / cleanup)
                # is now inside the single-dispatch ambiguity guard: any exception here
                # that is NOT a parsed definitive rejection means the write may have
                # landed, so it is AMBIGUOUS -- single dispatch, never a clean 'failed',
                # never replayed. A GET re-raises unchanged (an idempotent read the
                # caller safely re-issues).
                if not idempotent:
                    raise UnifiAmbiguousOutcomeError(
                        f"{method_u} {endpoint} outcome unknown while finishing the "
                        f"response ({type(exc).__name__}: {exc}); the request was "
                        "dispatched and the write may have landed. Not retried. "
                        "Reconcile controller state via GET before any further attempt."
                    ) from exc
                raise

            # ---- post-send response processing (capture / parse / close) ----
            # #w14a-1 (STRUCTURAL, not enumerate-more): the request bytes are now
            # dispatched and the controller has answered. ANY exception raised while
            # PROCESSING that response -- cookie ``capture`` (a ``CookieConflict`` from
            # duplicate TOKEN cookies, a ``LocalProtocolError``), a cleanup
            # ``RuntimeError``, a ``CloseError``/``DecodingError`` finishing the body,
            # anything -- means a MUTATION's outcome is UNKNOWN: the write may already
            # have landed. Previously ``capture`` ran OUTSIDE the classifier, so such a
            # post-send error escaped ``request`` and reached the applier's generic
            # handler as a clean 'failed', permitting a REPLAY (a second PUT). For a
            # mutation we now wrap ALL post-send processing so any exception that is NOT
            # a PARSED definitive rejection is classified AMBIGUOUS -- single dispatch,
            # never a clean 'failed', never replayed. This is the general rule the
            # earlier CloseError/DecodingError fix only enumerated one arm of. A GET
            # keeps its existing behavior: these errors propagate to the caller, which
            # safely re-issues an idempotent read.
            if not idempotent:
                return self._finish_mutation(strategy, resp, method_u, endpoint)

            strategy.capture(resp, self._http.cookies)

            if resp.status_code == 401 and not relogged:
                # A GET is idempotent: a single re-login and retry is safe.
                logger.info("%s %s -> 401; re-logging in once.", method_u, endpoint)
                relogged = True
                await self._relogin(login_epoch)
                login_epoch = self._login_epoch  # adopt whichever login now stands
                continue

            # Retryable 5xx: only GETs are retried. A mutation that draws a 5xx is
            # a definite server-side failure (a received response, not a lost one),
            # so it is surfaced to the caller as a non-2xx outcome, never retried.
            if idempotent and resp.status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
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
        # BUG#6 / #w12a-2 / #w13a-1 (positive-proof of a real read): a 200 body counts
        # as a well-formed SUCCESSFUL read ONLY when it POSITIVELY presents the classic
        # UniFi success shape -- a ``data`` field that is an ACTUAL LIST (a real,
        # present list; possibly empty) AND, when a ``meta`` KEY is present, a ``meta``
        # that is a VALID dict carrying an explicit ``meta.rc == "ok"``. Nothing weaker
        # is a successful read:
        #
        #   * ``{"meta":{"rc":"ok"}}`` with NO ``data`` -- a success envelope over
        #     no rows is not a read of zero rows; the payload the caller reads is
        #     simply absent. rc=ok WITHOUT a real data list is NOT a valid read.
        #   * ``{"data":null}`` / ``{"data":false}`` -- ``data`` present but not a
        #     list; a null/false body is not an empty successful read.
        #   * ``{"meta":{"rc":"pending"},...}`` (or any meta.rc other than "ok") --
        #     a pending/other envelope is NOT a completed read even with ``data:[]``.
        #   * ``{"meta":null,...}`` / ``{"meta":false,...}`` / ``{"meta":[],...}`` /
        #     ``{"meta":"pending",...}`` -- the ``meta`` KEY is PRESENT but is not a
        #     valid dict, so the controller sent SOMETHING for meta that is not a
        #     success envelope. A present-but-non-dict meta is NOT proof of rc=ok and
        #     must NOT be treated as an absent meta (#w13a-1): if the ``meta`` key
        #     exists at all it MUST be a dict with rc=="ok" for the read to be a
        #     well-formed success. (Only a genuinely ABSENT ``meta`` key, over a real
        #     data list, is the bare ``{"data":[...]}`` success shape.)
        #
        # Previously this used ``meta_present = isinstance(meta, dict)``, so a present-
        # but-non-dict meta was treated as ABSENT and the read accepted (the #w13a-1
        # bug: ``{"meta":null,"data":[]}`` recorded 'complete'/coverage 1.0). Earlier
        # still it accepted ``has_data OR rc_ok`` and :meth:`_data` defaulted a
        # missing/falsy ``data`` to ``[]``, so every malformed body above parsed to
        # zero rows and read as empty-but-healthy; event catch-up then credited
        # ``event_history`` coverage (status 'complete', 1.0) for a window it never
        # actually read, defeating every detector coverage gate. Absent POSITIVE
        # proof of a real read this is a FAILED/UNAVAILABLE read and must raise, so
        # catch-up records a FAILED hole rather than fabricated coverage. A genuine
        # ``{"meta":{"rc":"ok"},"data":[]}`` (or a bare ``{"data":[...]}`` with NO
        # meta key) still reads as complete -- normal reads are unaffected.
        data_field = data.get("data")
        data_is_list = isinstance(data_field, list)
        meta_key_present = "meta" in data
        meta = data.get("meta")
        meta_is_dict = isinstance(meta, dict)
        # A present ``meta`` key is well-formed ONLY as a dict with rc=="ok". An absent
        # meta key is fine (the bare ``{"data":[...]}`` shape); a present non-dict meta,
        # or a dict whose rc != "ok", is not a success.
        meta_ok = meta_is_dict and str(meta.get("rc", "")).strip().lower() == "ok"
        if not data_is_list or (meta_key_present and not meta_ok):
            if not data_is_list:
                detail = (
                    data.get("error")
                    or data.get("message")
                    or (f"data is {type(data_field).__name__}, not a list")
                )
            elif not meta_is_dict:
                detail = f"meta is {type(meta).__name__}, not a dict (rc unverifiable)"
            else:
                detail = (
                    data.get("error")
                    or data.get("message")
                    or (f"meta.rc={meta.get('rc')!r} (not ok)")
                )
            raise UnifiError(
                f"{endpoint} -> unrecognized response (no well-formed data list / "
                f"meta.rc=ok): {detail}"
            )
        return data

    @staticmethod
    def _data(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data", [])
        if isinstance(data, list):
            return data
        return [data] if data else []


__all__ = ["UnifiClient", "envelope_error"]
