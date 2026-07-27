"""Minimal static-token API authentication (ARCHITECTURE.md 12 + 18.1).

The daemon binds a LAN-reachable port. The auth model (revised in ARCHITECTURE.md
18.1, "already set up = just works"):

- **Reads are open once configured.** Every ``GET /api/*`` — issues, sle,
  inventory, metrics, incidents, events, health, and ``setup/status`` — is served
  without a token. A configured daemon just loads the dashboard for any device on
  the LAN; there is no returning-user gate for *viewing*.
- **State-changing requests require the token.** The mutating routes — the fix
  engine's apply/revert, ``ack``/``snooze``, and ``setup/connect`` once configured
  — require ``Authorization: Bearer <token>``. The UI keeps the token in
  ``localStorage`` and prompts for it just-in-time on the first mutation.

The ``/ws`` socket takes the same token as a ``?token=`` query parameter (browsers
cannot set WebSocket headers), so its auth lives beside the endpoint in
:mod:`netadmin.server.ws`.

Token comparison is **constant-time** (:func:`hmac.compare_digest` via the
module-level :data:`_compare` seam) so a timing side-channel cannot recover the
token byte by byte.

**Controller mutations fail closed regardless of the read posture.** The two
routes that can change the live controller — ``POST .../fix/apply`` and ``POST
.../fix/revert`` — are refused outright (403) when no token is configured, and
require the token when one is; they are never reachable unauthenticated, even on an
otherwise-open API, so the fix engine cannot be driven from a browser or curl call
unless the deploy has provisioned a token. These write ops are additionally **rate
limited** per client (the global write-op rule) so a token guess or an apply flood
cannot be hammered. Local CLI applies do not pass through this middleware — they
are an explicit human action on the box itself.

**Tradeoff, stated (18.1):** anyone on the LAN can *view* the network data without
a token — the dashboard is not a secret; the network-changing actions are what is
protected. Anyone wanting viewing gated too runs it behind a reverse proxy or the
loopback-only bind.

**The investigate route (``POST .../issues/{id}/investigate``) is provider-gated,
not middleware-gated.** The ``manual`` provider only writes a local markdown
dossier — no network call, no cost — so it never needs the token; ``copilot`` and
``anthropic`` still do. That decision depends on the request *body*, which this
middleware never parses, so the route is left open here and
:func:`netadmin.server.routers.issues.investigate_issue` enforces the token
itself once it has read the provider, reusing :func:`token_matches` and
:func:`extract_bearer` so the check matches this middleware's own exactly.

**The remote MCP mount (``/mcp``) is NOT gated here.** It sits outside
``/api``, carries its own credential (``NETADMIN_MCP_TOKEN``, never
``NETADMIN_API_TOKEN``), and is gated by :mod:`netadmin.server.mcp_mount`
(ARCHITECTURE.md 18.3). That module reuses this one's :func:`token_matches`,
:func:`extract_bearer`, :class:`FixedWindowRateLimiter` and :func:`client_key`,
so both gates compare and throttle identically; only the secret and the
"absent means 404" posture differ. Nothing in this middleware changes because of
it, and nothing there can authorize an ``/api`` request.

**Managing the MCP token IS gated here, though.** ``GET /api/system/mcp-token``
(reveal) and ``POST /api/system/mcp-token/regenerate`` are the Settings surface
for finding or rotating ``NETADMIN_MCP_TOKEN`` (ARCHITECTURE.md 18.4), and they
follow their access-token counterparts' gating exactly, credential and all: the
*API* bearer token (or a loopback peer, for reveal only) authorizes both, never
the MCP token itself — rotating a read-only credential must not be able to
authorize its own rotation.

This is deliberately a single shared secret, not per-user auth: the daemon is a
local-first single-operator tool (section 12), and a revocable static token keeps
a browser and a curl call on the same simple contract without a login/session
surface. CORS wraps this middleware (added after it in ``create_app``), so a 401 /
403 / 429 still carries CORS headers and the browser can read it.
"""

from __future__ import annotations

import hmac
import os
import time
from collections import deque
from typing import Callable, Deque, Dict, Optional

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

__all__ = [
    "HEALTH_PATH",
    "API_PREFIX",
    "SETUP_PREFIX",
    "SETUP_STATUS_PATH",
    "SYSTEM_TOKEN_PATH",
    "SYSTEM_TOKEN_REGENERATE_PATH",
    "SYSTEM_MCP_TOKEN_PATH",
    "SYSTEM_MCP_TOKEN_REGENERATE_PATH",
    "SYSTEM_UPDATE_APPLY_PATH",
    "MUTATION_PATH_SUFFIXES",
    "WS_UNAUTHORIZED_CODE",
    "DEFAULT_WRITE_MAX",
    "DEFAULT_WRITE_WINDOW_S",
    "FixedWindowRateLimiter",
    "token_matches",
    "extract_bearer",
    "scope_header",
    "client_key",
    "is_controller_mutation",
    "is_token_regenerate",
    "is_mcp_token_regenerate",
    "is_investigate_route",
    "is_system_update_apply",
    "ApiTokenAuthMiddleware",
]

# The single unauthenticated ``/api`` route (a liveness probe must always reach it).
HEALTH_PATH = "/api/health"
API_PREFIX = "/api"

# First-run setup surface (ARCHITECTURE.md 18). ``GET /api/setup/status`` is the
# always-open discriminator the web app reads to choose the setup flow vs the token
# gate; the other ``/api/setup/*`` routes are reachable pre-auth ONLY while the
# daemon is unconfigured (the first-run window) and are gated normally once it is.
SETUP_PREFIX = "/api/setup"
SETUP_STATUS_PATH = "/api/setup/status"

# The controller-mutating routes (ARCHITECTURE.md 9): the fix engine's apply and
# revert. Matched by path suffix under ``/api`` on a POST; these fail closed even
# when the API is otherwise open, and are rate limited.
MUTATION_PATH_SUFFIXES = ("/fix/apply", "/fix/revert")

# The access-token surface (ARCHITECTURE.md 18.1 Settings addendum). The reveal is
# the ONE ``GET`` that is not open once configured -- it returns the secret itself,
# so it requires the bearer token OR a loopback peer (the on-box recovery path for a
# forgotten token). Regenerate mints + persists a new token and is a gated,
# rate-limited mutation like the controller writes.
SYSTEM_TOKEN_PATH = "/api/system/token"
SYSTEM_TOKEN_REGENERATE_PATH = "/api/system/token/regenerate"

# The remote-MCP-token surface (ARCHITECTURE.md 18.4 Settings addendum): reveal
# and regenerate for NETADMIN_MCP_TOKEN, gated exactly like SYSTEM_TOKEN_PATH /
# SYSTEM_TOKEN_REGENERATE_PATH above -- same bearer-or-loopback reveal, same
# gated-and-rate-limited regenerate -- but the credential that unlocks them is
# always the *API* token, never the MCP token being managed.
SYSTEM_MCP_TOKEN_PATH = "/api/system/mcp-token"
SYSTEM_MCP_TOKEN_REGENERATE_PATH = "/api/system/mcp-token/regenerate"

# The pip self-upgrade apply route (ARCHITECTURE.md 23). Not a *controller*
# mutation -- it upgrades the daemon's own code, never the live network -- but it
# fails closed exactly like one: see is_system_update_apply.
SYSTEM_UPDATE_APPLY_PATH = "/api/system/update/apply"

# WebSocket close code for an auth failure: 1008 (policy violation).
WS_UNAUTHORIZED_CODE = 1008

# Write-op rate limit defaults: at most N controller-mutation requests per client
# per rolling window. Generous for a single operator; low enough that a token guess
# or an apply flood is throttled rather than unbounded.
DEFAULT_WRITE_MAX = 10
DEFAULT_WRITE_WINDOW_S = 60.0

# Constant-time comparator, referenced through a module-level name so a test can
# assert the timing-safe path is the one actually exercised.
_compare = hmac.compare_digest


def is_controller_mutation(method: str, path: str) -> bool:
    """Whether ``method``/``path`` is a route that can change the live controller.

    The fix engine's apply/revert are the only controller-mutating HTTP routes;
    everything else either reads or mutates local state only. Matched structurally
    (POST + ``/api`` prefix + a known suffix) so it cannot be spoofed by a stray
    path, and so the rule is independent of the router's exact issue-id segment.
    """
    if method != "POST" or not path.startswith(API_PREFIX):
        return False
    return any(path.endswith(suffix) for suffix in MUTATION_PATH_SUFFIXES)


def is_token_regenerate(method: str, path: str) -> bool:
    """Whether ``method``/``path`` is the access-token regenerate route.

    A local, sensitive mutation (mints + persists a new bearer token). Not a
    *controller* mutation -- it never touches the live network -- so it does not
    fail closed the way apply/revert do, but it is token-gated and rate limited.
    """
    return method == "POST" and path == SYSTEM_TOKEN_REGENERATE_PATH


def is_mcp_token_regenerate(method: str, path: str) -> bool:
    """Whether ``method``/``path`` is the MCP-token regenerate route.

    Mints + persists a new ``NETADMIN_MCP_TOKEN``. Mirrors
    :func:`is_token_regenerate` exactly -- token-gated and rate limited, never a
    controller mutation -- but for the read-only remote-MCP credential
    (ARCHITECTURE.md 18.4). Gated on the *API* token like its sibling: the MCP
    token being rotated is never itself the credential that authorizes the
    rotation.
    """
    return method == "POST" and path == SYSTEM_MCP_TOKEN_REGENERATE_PATH


def is_system_update_apply(method: str, path: str) -> bool:
    """Whether ``method``/``path`` is ``POST /api/system/update/apply``.

    This is the pip self-upgrade trigger (ARCHITECTURE.md 23): it never touches
    the live network, so it is not a *controller* mutation, but an unconfigured/
    open install must not let a stray unauthenticated POST kick off a self-upgrade
    -- an even bigger blast radius than a stray config write -- so it gets the
    exact same fail-closed treatment as :func:`is_controller_mutation` in the
    middleware below, just tracked separately so that function's own contract
    (only the two controller-mutating routes) stays unchanged.
    """
    return method == "POST" and path == SYSTEM_UPDATE_APPLY_PATH


def is_investigate_route(method: str, path: str) -> bool:
    """Whether ``method``/``path`` is the per-issue investigate route.

    Whether this POST needs the token depends on the requested *provider*
    (``manual`` writes a local file and needs none; ``copilot``/``anthropic`` do)
    and the provider only lives in the request body, which this middleware never
    parses. So the route is left OPEN here and the handler
    (:func:`netadmin.server.routers.issues.investigate_issue`) enforces the token
    itself once it has read the body, for every provider except ``manual``.

    Matched structurally (POST + ``/api/issues/`` + trailing ``/investigate``) so
    it is independent of the exact issue-id segment and cannot be spoofed by a
    stray path. The two-segment discovery route (``GET
    /api/issues/investigate/providers``) never matches -- it is a GET.
    """
    if method != "POST" or not path.startswith(API_PREFIX + "/issues/"):
        return False
    return path.endswith("/investigate")


class FixedWindowRateLimiter:
    """A fixed-window per-client attempt counter.

    Used twice: for controller-mutation requests by the middleware below, and for
    failed bearer-token attempts on the remote MCP mount
    (:mod:`netadmin.server.mcp_mount`). Public rather than private precisely so
    the second gate reuses this implementation instead of growing a near-copy.

    In-process and lock-free: the daemon runs a single uvicorn worker (section 2),
    so one limiter instance sees every request and no cross-process coordination is
    needed. ``now_fn`` is injectable so a test can advance time deterministically.
    """

    def __init__(
        self, max_events: int, window_s: float, *, now_fn: Optional[Callable[[], float]] = None
    ) -> None:
        self._max = max_events
        self._window = window_s
        self._now = now_fn or time.monotonic
        self._hits: Dict[str, Deque[float]] = {}

    def allow(self, key: str) -> bool:
        """Record an attempt for ``key``; return False if it exceeds the window budget."""
        now = self._now()
        cutoff = now - self._window
        # Drop every bucket that has fully aged out, not just this caller's. The
        # dict is keyed by client address on a pre-auth path, so without this it
        # only ever grows: one entry per address that ever probed, retained for
        # the life of the process.
        for stale in [k for k, b in self._hits.items() if not b or b[-1] <= cutoff]:
            if stale != key:
                del self._hits[stale]
        bucket = self._hits.setdefault(key, deque())
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= self._max:
            return False
        bucket.append(now)
        return True


def token_matches(supplied: Optional[str], expected: Optional[str]) -> bool:
    """Constant-time compare ``supplied`` against the configured ``expected``.

    Returns False when either side is missing. A missing ``expected`` means "no
    token configured", which callers treat as open access *before* calling this —
    this function itself never green-lights an empty expected token.
    """
    if not expected or not supplied:
        return False
    # Compare as bytes, not str. ``hmac.compare_digest`` rejects a str containing
    # any codepoint above U+007F with TypeError, and Starlette decodes headers as
    # latin-1, so a single high byte in the bearer raised out of this function and
    # surfaced as an unauthenticated 500 -- one that never reached the rate
    # limiter. Encoding first keeps the comparison constant-time and total.
    return _compare(supplied.encode("utf-8", "surrogateescape"), expected.encode("utf-8"))


def extract_bearer(header_value: Optional[str]) -> Optional[str]:
    """Pull the token out of an ``Authorization: Bearer <token>`` header value."""
    if not header_value:
        return None
    prefix = "bearer "
    if header_value[: len(prefix)].lower() != prefix:
        return None
    token = header_value[len(prefix) :].strip()
    return token or None


class ApiTokenAuthMiddleware:
    """ASGI middleware gating ``/api/*`` on a token, with mutations failing closed.

    Ordinary ``/api`` reads are a no-op when ``token`` is falsy (open access);
    ``OPTIONS`` preflights pass through so the CORS layer can answer them, and
    non-``/api`` paths (docs, the built SPA) are never gated. The two
    controller-mutating routes (:data:`MUTATION_PATH_SUFFIXES`) are the exception:
    they **fail closed** — refused with 403 when no token is configured, required to
    present the token when one is — and are **rate limited** per client. The ``/ws``
    socket is a separate ASGI scope handled at the endpoint, not here.
    """

    def __init__(
        self,
        app: ASGIApp,
        token: Optional[str] = None,
        *,
        token_provider: Optional[Callable[[], Optional[str]]] = None,
        configured_provider: Optional[Callable[[], bool]] = None,
        write_max: int = DEFAULT_WRITE_MAX,
        write_window_s: float = DEFAULT_WRITE_WINDOW_S,
        now_fn: Optional[Callable[[], float]] = None,
    ) -> None:
        self.app = app
        self._static_token = token or None
        # Live seams (ARCHITECTURE.md 18): with a ``token_provider`` the effective
        # token is read per-request, so the first-run connect that mints a token
        # locks the API in-process with no restart. ``configured_provider`` gives the
        # live setup state that gates ``/api/setup/*``. Both default to the static
        # posture (fixed token; "configured" == a token is set) for the direct-
        # construction path the unit tests use.
        self._token_provider = token_provider
        self._configured_provider = configured_provider
        self._write_limiter = FixedWindowRateLimiter(write_max, write_window_s, now_fn=now_fn)

    @property
    def token(self) -> Optional[str]:
        """The effective bearer token: the live provider's value, else the static one."""
        if self._token_provider is not None:
            value = self._token_provider()
            return value or None
        return self._static_token

    def _configured(self) -> bool:
        """Live setup state: whether the daemon is configured (setup is locked).

        A read failure fails **safe** — treated as configured, so a transient error
        locks the setup surface rather than leaving it open. Without a provider,
        "configured" collapses to "a token is set" (the static posture).
        """
        if self._configured_provider is not None:
            try:
                return bool(self._configured_provider())
            except Exception:  # noqa: BLE001 - a state read must never wedge auth
                return True
        return self.token is not None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method: str = scope.get("method", "GET")
        path: str = scope.get("path", "")

        # First-run setup surface (ARCHITECTURE.md 18). ``GET /api/setup/status`` is
        # the unauthenticated discriminator the web app reads on every load to pick
        # the setup flow vs the token gate -- always open, like /api/health. The
        # other ``/api/setup/*`` routes are reachable pre-auth ONLY while the daemon
        # is unconfigured (the first-run window); once configured they fall through
        # to the normal token gate below and the connect handler additionally 409s,
        # so setup can never reconfigure a live install.
        if method == "GET" and path == SETUP_STATUS_PATH:
            await self.app(scope, receive, send)
            return
        if path == SETUP_PREFIX or path.startswith(SETUP_PREFIX + "/"):
            if not self._configured():
                await self.app(scope, receive, send)
                return
            # configured: fall through to the normal token enforcement below.

        # Controller-mutating routes fail closed regardless of read posture. This
        # runs BEFORE the open-access shortcut so an unauthenticated apply/revert is
        # never reachable, even when the rest of the API is open.
        if is_controller_mutation(method, path):
            if self.token is None:
                await self._refuse(
                    scope,
                    receive,
                    send,
                    status=403,
                    detail=(
                        "controller mutation is disabled: set NETADMIN_API_TOKEN to "
                        "enable fix apply/revert"
                    ),
                    code="mutation_locked",
                )
                return
            if not self._write_limiter.allow(client_key(scope)):
                await self._refuse(
                    scope,
                    receive,
                    send,
                    status=429,
                    detail="too many write requests; slow down",
                    code="rate_limited",
                    headers={"Retry-After": str(int(DEFAULT_WRITE_WINDOW_S))},
                )
                return
            await self._require_token(scope, receive, send)
            return

        # The pip self-upgrade apply route (ARCHITECTURE.md 23) fails closed exactly
        # like a controller mutation, for the same reason and in the same place: an
        # unconfigured/open install must not let a stray POST trigger a self-upgrade.
        if is_system_update_apply(method, path):
            if self.token is None:
                await self._refuse(
                    scope,
                    receive,
                    send,
                    status=403,
                    detail=(
                        "self-upgrade is disabled: set NETADMIN_API_TOKEN to enable "
                        "POST /api/system/update/apply"
                    ),
                    code="mutation_locked",
                )
                return
            if not self._write_limiter.allow(client_key(scope)):
                await self._refuse(
                    scope,
                    receive,
                    send,
                    status=429,
                    detail="too many write requests; slow down",
                    code="rate_limited",
                    headers={"Retry-After": str(int(DEFAULT_WRITE_WINDOW_S))},
                )
                return
            await self._require_token(scope, receive, send)
            return

        # Token regenerate (ARCHITECTURE.md 18.1) is a sensitive LOCAL mutation: it
        # mints and persists a new bearer token. It requires the token like any
        # write and is RATE LIMITED with the controller writes, so a leaked token
        # cannot be spun to churn the secret. Guarded on a configured token so an
        # unconfigured/open install still mints its first token through the open
        # shortcut below (there is nothing yet to protect).
        if self.token is not None and is_token_regenerate(method, path):
            if not self._write_limiter.allow(client_key(scope)):
                await self._refuse(
                    scope,
                    receive,
                    send,
                    status=429,
                    detail="too many write requests; slow down",
                    code="rate_limited",
                    headers={"Retry-After": str(int(DEFAULT_WRITE_WINDOW_S))},
                )
                return
            await self._require_token(scope, receive, send)
            return

        # MCP-token regenerate (ARCHITECTURE.md 18.4). Deliberately NOT guarded on
        # ``self.token is not None``, unlike the access-token regenerate above. The
        # two credentials protect different things: the API token guards network
        # mutations, the MCP token guards the whole history store. Gating this on
        # the API token meant that on the documented setup path -- which mints only
        # NETADMIN_MCP_TOKEN and never asks for an API token -- the open-reads
        # shortcut below served this route to any LAN peer, who could then rotate
        # the /mcp credential out from under every configured client. Handled here,
        # above that shortcut, so posture is decided by THIS token's own rules.
        if is_mcp_token_regenerate(method, path):
            if not self._write_limiter.allow(client_key(scope)):
                await self._refuse(
                    scope,
                    receive,
                    send,
                    status=429,
                    detail="too many write requests; slow down",
                    code="rate_limited",
                    headers={"Retry-After": str(int(DEFAULT_WRITE_WINDOW_S))},
                )
                return
            await self._require_token(scope, receive, send)
            return

        # The MCP-token reveal, for the same reason and with the same placement:
        # it returns NETADMIN_MCP_TOKEN itself. Loopback is allowed unauthenticated
        # -- the operator standing on the box, the same recovery path the access
        # token gets, and the bootstrap that lets `netadmin mcp-token` and a local
        # browser work on an install with no API token. A remote peer must present
        # the API token; if none is configured, ``_require_token`` refuses every
        # remote caller, which is the intended posture. Minting or reading the
        # credential to the whole history store is not a thing the LAN may do
        # anonymously just because network mutations happen to be unlocked.
        if method == "GET" and path == SYSTEM_MCP_TOKEN_PATH:
            if _is_loopback(scope):
                await self.app(scope, receive, send)
                return
            await self._require_token(scope, receive, send)
            return

        # Investigate (ARCHITECTURE.md 10 addendum): OPEN here regardless of
        # posture. The provider (manual/copilot/anthropic) decides whether a
        # token is required, and that decision needs the request BODY, which
        # this middleware never parses -- so the gate moves into the handler,
        # which enforces the token for every provider except ``manual`` (no
        # network call, no cost) using the same token_matches/extract_bearer
        # helpers this middleware uses.
        if is_investigate_route(method, path):
            await self.app(scope, receive, send)
            return

        # Everything below here is a non-controller-mutation request. With no token
        # configured the API is open (unconfigured install / opt-out); a startup
        # WARNING already flagged that.
        if self.token is None:
            await self.app(scope, receive, send)
            return

        # Preflights carry no Authorization header; let CORS handle them. Anything
        # outside the /api prefix (SPA assets, /docs) is not gated.
        if method == "OPTIONS" or not path.startswith(API_PREFIX):
            await self.app(scope, receive, send)
            return

        # The access-token reveal is the ONE GET that is not open (ARCHITECTURE.md
        # 18.1 Settings addendum): it returns the secret itself. Require the bearer
        # token OR a loopback peer -- the operator on the box, the documented
        # recovery path for a forgotten token. Reached only when a token is
        # configured (the open shortcut above already served it otherwise).
        if method == "GET" and path == SYSTEM_TOKEN_PATH:
            if _is_loopback(scope):
                await self.app(scope, receive, send)
                return
            await self._require_token(scope, receive, send)
            return

        # Reads are open on the LAN once configured (ARCHITECTURE.md 18.1). Every
        # GET -- issues, sle, inventory, metrics, incidents, events, health, setup
        # status -- is served without a token: a configured daemon just loads the
        # dashboard for any device on the network, no returning-user gate. Only
        # state-changing requests (below) require the token, prompted just-in-time.
        # The tradeoff is stated in 18.1: viewing is not gated on a trusted LAN; the
        # network-changing actions are. Anyone wanting viewing gated too runs it
        # behind a reverse proxy or the loopback-only bind.
        if method == "GET":
            await self.app(scope, receive, send)
            return

        # A state-changing request (POST/PUT/PATCH/DELETE): ack/snooze, setup/connect
        # once configured, etc. These require the bearer token (controller mutations
        # were already handled, and fail closed, above).
        await self._require_token(scope, receive, send)

    async def _require_token(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass through when the bearer token matches; otherwise 401."""
        header_value = scope_header(scope, b"authorization")
        if token_matches(extract_bearer(header_value), self.token):
            await self.app(scope, receive, send)
            return
        await self._refuse(
            scope,
            receive,
            send,
            status=401,
            detail="authentication required",
            code="unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )

    @staticmethod
    async def _refuse(
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        status: int,
        detail: str,
        code: str,
        headers: Optional[dict] = None,
    ) -> None:
        response = JSONResponse(
            {"detail": detail, "code": code}, status_code=status, headers=headers or {}
        )
        await response(scope, receive, send)


def scope_header(scope: Scope, name: bytes) -> Optional[str]:
    """Read one request header (case-insensitive) from a raw ASGI scope."""
    lname = name.lower()
    for key, value in scope.get("headers", []):
        if key.lower() == lname:
            return value.decode("latin-1")
    return None


def _is_loopback(scope: Scope) -> bool:
    """Whether the request's *direct* peer is loopback (127.0.0.0/8 or ``::1``).

    The token-reveal bypass: the operator on the box can read the access token
    without presenting it (recovering a forgotten token). A forwarded request
    (``X-Forwarded-For`` present) is treated as **non**-loopback — behind a reverse
    proxy the ASGI peer is the proxy, never the real client, so the bypass must not
    fire for a remote caller whose packets merely arrived via localhost.
    """
    if scope_header(scope, b"x-forwarded-for"):
        return False
    client = scope.get("client")
    host = client[0] if client else None
    if not host:
        return False
    return host == "::1" or host.startswith("127.")


def client_key(scope: Scope) -> str:
    """The rate-limit bucket key: the client host, or a stable fallback.

    The ASGI peer address, which the kernel supplies and a caller cannot forge.

    ``X-Forwarded-For`` is honoured ONLY when ``NETADMIN_TRUST_PROXY`` is set,
    because this key is computed *before* authentication: if an unauthenticated
    caller can choose their own bucket, they can rotate one header and never hit
    the 429, which turns a rate-limited token into an unlimited guessing game.
    Behind a real reverse proxy every peer address collapses to the proxy's, so
    the header is the only way to tell clients apart -- hence the opt-in rather
    than dropping it. Set it only when a proxy you control rewrites the header.

    A missing client collapses to a single shared bucket, so an unidentifiable
    flood is still throttled.
    """
    if os.environ.get("NETADMIN_TRUST_PROXY", "").strip().lower() in ("1", "true", "yes"):
        forwarded = scope_header(scope, b"x-forwarded-for")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first
    client = scope.get("client")
    if client and client[0]:
        return str(client[0])
    return "unknown"
