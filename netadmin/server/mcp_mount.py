"""The remote MCP mount: a token-gated ``/mcp`` on the daemon (ARCHITECTURE.md 18.3).

The same 11 read-only tools the stdio server exposes (:mod:`netadmin.mcp.tools`,
unchanged and transport-agnostic), reachable over the network from a Claude
client that never had to be installed on the box the daemon runs on.

What this module owns is the *gate*, and the gate is the point. ``GET /api/*``
reads are open on the LAN once the daemon is configured (18.1) and the daemon is
LAN-published, so an ungated ``/mcp`` would hand any device on the network the
whole history store in one tool call. The postures, in the order they are
evaluated:

1. **No token configured -> 404.** The feature is absent, and it says so the way
   an absent feature does. A 401 here would advertise a surface that is not
   actually serving anything.
2. **Wrong or missing token -> 401** with ``WWW-Authenticate: Bearer``, compared
   in constant time by :func:`netadmin.server.auth.token_matches`. This check
   runs before the request body is touched at all: nothing calls ``receive``
   until the token has matched, so an unauthenticated caller cannot make the
   daemon parse, buffer, or dispatch a single byte of JSON-RPC.
3. **Repeated failures -> 429.** Only *failed* attempts are counted (a legitimate
   MCP client makes many successful requests per session), reusing
   :class:`netadmin.server.auth.FixedWindowRateLimiter` and its client-key rule,
   so the two gates throttle identically.
4. **Authenticated but the SDK is absent -> 503** naming ``pip install
   "unifioptimizer[mcp]"``. Deliberately *after* the token check: the operator is
   the only party who can act on that sentence, and an unauthenticated caller
   learns nothing about how the deploy is provisioned.

**The credential is ``NETADMIN_MCP_TOKEN``, never ``NETADMIN_API_TOKEN``, with no
fallback in either direction.** The API token authorizes controller mutations, so
pasting it into a Claude config on every laptop would turn one leaked config file
from a privacy problem into network control. The MCP token is read-only by
construction and rotates on its own.

**Rotation disarms the old value on the next request, whoever performed it.** The
gate does not enforce the startup snapshot of ``Settings``; it enforces what
``data/secrets.env`` says now, so ``netadmin mcp-token --regenerate`` in another
shell -- or an operator with an editor -- takes effect against a running daemon
without a restart, and deleting the token turns ``/mcp`` back to 404. An exported
``NETADMIN_MCP_TOKEN`` still outranks the file, and only this one key is
re-read. :class:`LiveMcpToken` is where that lives and why.

**Read-only, three ways, none of them by convention.** The mount opens its *own*
:meth:`netadmin.store.repository.Repository.open` connection with
``read_only=True``: SQLite ``mode=ro`` at the VFS layer, ``PRAGMA
query_only=ON`` on the connection, and a tool layer that imports nothing from
``netadmin.fixes`` or ``netadmin.ingest``. It never borrows the daemon's
read-write handle.

**What remote does not inherit.** The stdio server answers when the daemon is
down, because it opens the file itself. This one is part of the daemon, so it
answers only while the daemon runs. Both are supported; they are not
interchangeable.

Transport is the SDK's streamable HTTP in **stateless**, **JSON-response** mode:
a fresh transport per request and a plain JSON body rather than an SSE stream.
There is no session table to keep and no long-lived stream held open beside the
daemon's own ``/ws``, and these tools only ever answer a question, so a server
push channel would buy nothing. Both are within the streamable-HTTP spec and
clients negotiate either.

The SDK's DNS-rebinding protection is left off deliberately. It exists to protect
*unauthenticated* local MCP servers from a browser tricked into calling them; this
mount requires a bearer token a browser has no way to obtain, and pinning an
allow-list of Host headers would break exactly the LAN-by-IP access the feature is
for.
"""

from __future__ import annotations

import importlib.util
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from netadmin.config import SECRETS_ENV, env_var_is_set, read_env_file_secret
from netadmin.logging import get_logger
from netadmin.server.auth import (
    FixedWindowRateLimiter,
    client_key,
    extract_bearer,
    scope_header,
    token_matches,
)

__all__ = [
    "MCP_PATH",
    "MCP_TOKEN_ENV",
    "DEFAULT_MCP_AUTH_MAX",
    "DEFAULT_MCP_AUTH_WINDOW_S",
    "SDK_MISSING_DETAIL",
    "NOT_RUNNING_DETAIL",
    "NOT_ENABLED_AT_BOOT_DETAIL",
    "McpMountState",
    "LiveMcpToken",
    "McpEndpoint",
    "sdk_available",
    "install_mcp_route",
    "start_mcp",
    "stop_mcp",
]

_log = get_logger("server.mcp")

# The one path this feature owns. Clients are configured with
# ``http://<host>:<port>/mcp``.
MCP_PATH = "/mcp"

# The one credential this feature reads, in ``data/secrets.env`` or the real
# environment. Named here rather than spelled inline so the gate, the refresher
# and the log lines can never drift apart.
MCP_TOKEN_ENV = "NETADMIN_MCP_TOKEN"

# Failed-token budget per client per rolling window. Mirrors the write-op limit in
# auth.py: generous for a human fixing a typo in a Claude config, low enough that
# a token guess is throttled rather than unbounded.
DEFAULT_MCP_AUTH_MAX = 10
DEFAULT_MCP_AUTH_WINDOW_S = 60.0

SDK_MISSING_DETAIL = (
    "the remote MCP endpoint needs the optional MCP SDK: "
    'pip install "unifioptimizer[mcp]", then restart the daemon'
)
NOT_RUNNING_DETAIL = "the remote MCP endpoint is not running"
# Said only to a caller who already authenticated with a token this daemon now
# accepts but did not have when it booted. Rotation applies to the next request
# (see :class:`LiveMcpToken`); *enabling* the feature does not, because the mount
# itself -- its read-only store handle and session manager -- is started once, in
# the lifespan. Naming that distinction here keeps the operator from reading the
# generic 503 as "install the extra".
NOT_ENABLED_AT_BOOT_DETAIL = (
    f"{MCP_TOKEN_ENV} was not configured when the daemon started, so the remote "
    "MCP endpoint was never mounted; restart the daemon to serve it"
)


def sdk_available() -> bool:
    """Whether the optional ``[mcp]`` extra is importable, without importing it.

    ``find_spec`` rather than a ``try: import`` so a probe at startup does not
    drag the SDK and its dependency tree into a daemon that will never mount the
    endpoint. Called through the module namespace so a test can flip it.
    """
    return importlib.util.find_spec("mcp") is not None


@dataclass
class McpMountState:
    """Everything the mount owns at runtime, or the honest reason it owns nothing.

    ``manager`` stays ``None`` whenever the endpoint cannot serve -- no token
    configured, SDK absent, lifespan never run -- and ``unavailable`` carries the
    sentence to hand back, so the endpoint never has to re-derive why.
    """

    manager: Any = None  # StreamableHTTPSessionManager, when the mount is live
    repo: Any = None  # Repository opened read_only=True; the mount's own handle
    stack: Optional[AsyncExitStack] = None
    unavailable: Optional[str] = None


# The stat fields a change is judged by, and the "no file there" answer.
_Signature = Tuple[int, int, int, int]
# Distinguishable from ``None``, which is a perfectly good parsed value ("the key
# is not in the file"). Means "this refresher has never parsed the file at all".
_NEVER_PARSED = object()


def _stat_signature(path: Path) -> Optional[_Signature]:
    """``(mtime_ns, size, inode, device)``, or ``None`` when the file is absent.

    All four fields, because no smaller set is honest. mtime alone has coarse
    granularity on some filesystems; size does not move at all when a token is
    replaced by another of the same length (every minted token is
    ``token_urlsafe(32)``, always 43 characters); and
    :func:`netadmin.config.write_secrets` publishes through ``os.replace``, which
    always lands the new content on a *different inode*. Together they cannot miss
    a rotation, and they cost one ``os.stat`` on the request path.

    Only "not there" is answered as ``None``. Every other ``OSError`` propagates:
    a file we are not allowed to stat is a malfunction, not a deliberate absence,
    and the two must not be collapsed into the same answer.
    """
    try:
        st = path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino, st.st_dev)


def _baseline_signature(path: Path) -> Optional[_Signature]:
    """:func:`_stat_signature`, with an unreadable path treated as absent.

    Used only when establishing a baseline, where there is nothing yet to protect
    and the next request re-derives the answer anyway. Separate from the reading
    path on purpose: taking a baseline must never be able to raise into
    ``create_app``.
    """
    try:
        return _stat_signature(path)
    except OSError:
        return None


class LiveMcpToken:
    """The token ``/mcp`` enforces right now, tracked against ``secrets.env``.

    ``app.state.settings`` is a snapshot taken once at startup, so a rotation
    performed by *another process* (``netadmin mcp-token --regenerate``, an
    operator with an editor) was invisible to a running daemon: the superseded
    token kept authenticating until the next restart, which is the one outcome
    rotation exists to prevent (Gitea #35). This refresher closes that gap for the
    MCP token **only** -- deliberately not by rebuilding ``Settings``, which would
    swap live controller credentials, the database path and the log destination
    out from under a running daemon to fix an auth bug.

    The rule, in precedence order, evaluated per request:

    1. **A real environment variable wins, always.** If ``NETADMIN_MCP_TOKEN`` is
       exported into the process (containers, systemd units), the file is not even
       consulted, and an edit to ``secrets.env`` cannot override the deployment.
       What is returned in that case is the settings object's own value rather
       than the raw variable, because init kwargs outrank the environment in turn
       and because the Settings-UI rotation mutates that object in place -- both
       must keep working.
    2. **Otherwise, "has the file moved since we started watching it?"** While the
       signature matches the one taken at construction, whatever the loader
       resolved at boot still stands -- which is what keeps a token that came from
       somewhere *other* than the file (init kwargs in tests, a rotation applied
       in-process) authoritative. That framing, rather than "the file is the
       authority", is what makes this safe to layer under the existing precedence
       chain instead of replacing it.
    3. **Once it has moved, the file is the answer** -- including when the answer
       is "the key is gone" or "the file is gone", which turns the mount back off
       and returns ``/mcp`` to 404 rather than leaving it stuck armed.

    Re-parsing is gated on that signature, so the steady state costs one
    ``os.stat`` per request and no parse at all. An unreadable file (permissions,
    a bad decode, anything short of honest absence) keeps the **last known-good**
    value and logs once: rotation publishes atomically via ``os.replace``, so a
    torn read should be impossible, but if one ever happens the gate must not flap
    -- losing the token would 401 every correctly-configured client, and the value
    being held was already valid a moment ago, so nothing loosens by holding it.
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        # The endpoint is driven from one event loop, so ``read`` is already
        # atomic against other coroutines; the lock is for the threadpool and for
        # anything that later reads this from another thread, and it costs an
        # uncontended acquire on a path that is about to touch the filesystem.
        self._lock = threading.Lock()
        self._path = self._secrets_path()
        self._baseline = _baseline_signature(self._path)
        self._parsed_sig: Any = _NEVER_PARSED
        self._parsed: Optional[str] = None
        self._read_failed = False

    def _secrets_path(self) -> Path:
        """The file to watch: the app's settable seam, else the real one."""
        return Path(getattr(self._app.state, "secrets_path", None) or SECRETS_ENV)

    def _snapshot(self) -> Optional[str]:
        """What the live ``Settings`` object says, normalised (blank -> ``None``)."""
        settings = getattr(self._app.state, "settings", None)
        return getattr(settings, "mcp_token", None) if settings is not None else None

    def read(self) -> Optional[str]:
        """The token to enforce for this request, or ``None`` when there is none."""
        snapshot = self._snapshot()
        if env_var_is_set(MCP_TOKEN_ENV):
            return snapshot

        path = self._secrets_path()
        with self._lock:
            if path != self._path:
                # The watched file was re-pointed after construction (tests, and
                # any embedder that moves the seam). Re-baseline on the new file
                # rather than reading it as a change: nothing has been rotated,
                # the question being asked has simply changed.
                self._path = path
                self._baseline = _baseline_signature(path)
                self._parsed_sig = _NEVER_PARSED
                self._parsed = None
                return snapshot

            try:
                sig = _stat_signature(path)
            except OSError:
                return self._hold_last_known_good(path, snapshot)

            if sig == self._baseline:
                return snapshot
            if sig == self._parsed_sig:
                return self._parsed

            if sig is None:
                value: Optional[str] = None  # deleted: the feature is off, honestly
            else:
                try:
                    value = read_env_file_secret(MCP_TOKEN_ENV, path=path)
                except FileNotFoundError:
                    value = None  # raced with a delete; absence is unambiguous
                except Exception:  # noqa: BLE001 - a bad secrets file must never 500 /mcp
                    return self._hold_last_known_good(path, snapshot)

            self._read_failed = False
            self._parsed_sig, self._parsed = sig, value
            return value

    def _hold_last_known_good(self, path: Path, snapshot: Optional[str]) -> Optional[str]:
        """Answer an unreadable file with the last value we trusted. Lock held.

        Neither the cache nor the signature is updated, so the next request tries
        again and a transient failure heals itself. Logged once per failure run,
        because this is on a request path a client may hit every few seconds.
        """
        if not self._read_failed:
            self._read_failed = True
            _log.warning(
                "remote MCP: cannot re-read %s; keeping the last known-good %s "
                "until it is readable again",
                path,
                MCP_TOKEN_ENV,
                exc_info=True,
            )
        return self._parsed if self._parsed_sig is not _NEVER_PARSED else snapshot


class McpEndpoint:
    """The ASGI app behind ``/mcp``: the gate, then the SDK's transport.

    Holds the app rather than a snapshot of its settings so the token is read
    **live** on every request, matching how
    :class:`netadmin.server.auth.ApiTokenAuthMiddleware` reads the API token: a
    rotated token takes effect without a restart, and so does removing one. Live
    means live for an *external* rotation too, not just one performed in this
    process -- see :class:`LiveMcpToken`.
    """

    def __init__(
        self,
        app: Any,
        *,
        auth_max: int = DEFAULT_MCP_AUTH_MAX,
        auth_window_s: float = DEFAULT_MCP_AUTH_WINDOW_S,
        now_fn: Optional[Callable[[], float]] = None,
    ) -> None:
        self._app = app
        self._auth_window_s = auth_window_s
        self._failures = FixedWindowRateLimiter(auth_max, auth_window_s, now_fn=now_fn)
        self._live_token = LiveMcpToken(app)

    @property
    def token(self) -> Optional[str]:
        """The MCP token to enforce, or ``None`` when the feature is off."""
        return self._live_token.read()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # pragma: no cover - Route only dispatches http
            return

        token = self.token
        if token is None:
            await _refuse(
                scope,
                receive,
                send,
                status=404,
                detail=(
                    "remote MCP is not enabled on this daemon: set NETADMIN_MCP_TOKEN "
                    "to enable it"
                ),
                code="mcp_disabled",
            )
            return

        # Constant-time, and BEFORE anything reads the body. `receive` is not
        # called anywhere above the delegation at the bottom of this method.
        supplied = extract_bearer(scope_header(scope, b"authorization"))
        if not token_matches(supplied, token):
            if not self._failures.allow(client_key(scope)):
                await _refuse(
                    scope,
                    receive,
                    send,
                    status=429,
                    detail="too many failed MCP authentication attempts; slow down",
                    code="rate_limited",
                    headers={"Retry-After": str(int(self._auth_window_s))},
                )
                return
            await _refuse(
                scope,
                receive,
                send,
                status=401,
                detail="authentication required",
                code="unauthorized",
                headers={"WWW-Authenticate": "Bearer"},
            )
            return

        state: Optional[McpMountState] = getattr(self._app.state, "mcp", None)
        if state is None or state.manager is None:
            detail = (state.unavailable if state is not None else None) or NOT_RUNNING_DETAIL
            await _refuse(scope, receive, send, status=503, detail=detail, code="mcp_unavailable")
            return

        await state.manager.handle_request(scope, receive, send)


async def _refuse(
    scope: Scope,
    receive: Receive,
    send: Send,
    *,
    status: int,
    detail: str,
    code: str,
    headers: Optional[Dict[str, str]] = None,
) -> None:
    """Answer without ever touching ``receive``.

    Starlette's ``Response.__call__`` accepts ``receive`` for signature
    compatibility but never awaits it, so a refusal reads no request bytes at
    all. That is the property the 401 depends on, and
    ``test_auth_is_decided_before_any_body_is_read`` is what keeps it true.
    """
    response = JSONResponse({"detail": detail, "code": code}, status_code=status, headers=headers)
    await response(scope, receive, send)


def install_mcp_route(app: Any) -> None:
    """Register ``/mcp`` on ``app``. Always registered; the endpoint decides.

    Registered unconditionally rather than only when a token is set, for two
    reasons. The posture is read live (:class:`LiveMcpToken`), so a token added
    to or rotated in ``data/secrets.env`` after boot must not need a different
    route table. And an unregistered path would fall through to the SPA
    catch-all, which answers ``GET /mcp`` with ``index.html`` and a 200 -- a far
    worse answer than 404.

    Must be called before :func:`netadmin.server.main._mount_spa`, which appends
    that catch-all: Starlette matches routes in registration order.
    """
    app.state.mcp = McpMountState()
    endpoint = McpEndpoint(app)
    app.router.routes.append(Route(MCP_PATH, endpoint=endpoint, name="mcp"))
    # The trailing-slash form too. Starlette's redirect_slashes only fires when
    # NOTHING matches, and the SPA catch-all matches everything -- so without this
    # ``GET /mcp/`` skipped the gate entirely and got index.html with a 200, the
    # exact outcome the comment above says registering the route prevents.
    app.router.routes.append(Route(MCP_PATH + "/", endpoint=endpoint, name="mcp_slash"))


async def start_mcp(app: Any) -> None:
    """Open the mount's read-only store handle and start the session manager.

    Called from the daemon lifespan after ``app.state.store`` exists, because
    ``mode=ro`` cannot create or migrate a database -- the daemon's own
    read-write open is what guarantees there is a file to read. Every failure
    here is recorded on :class:`McpMountState` and answered as a 503 to an
    authenticated caller; none of them is fatal to the daemon.
    """
    state: McpMountState = app.state.mcp
    settings = app.state.settings

    if settings.mcp_token is None:
        # Recorded, not just logged: the gate reads its token live, so a token
        # written to secrets.env after boot starts authenticating immediately --
        # and would otherwise land on the generic "not running" 503, which reads
        # like the SDK is missing. This one says the actual remedy.
        state.unavailable = NOT_ENABLED_AT_BOOT_DETAIL
        _log.info(
            "remote MCP: disabled (no %s configured); %s returns 404", MCP_TOKEN_ENV, MCP_PATH
        )
        return

    if not sdk_available():
        state.unavailable = SDK_MISSING_DETAIL
        _log.warning(
            "remote MCP: NETADMIN_MCP_TOKEN is configured but the MCP SDK is not "
            'installed, so %s answers 503. Run: pip install "unifioptimizer[mcp]"',
            MCP_PATH,
        )
        return

    from netadmin.mcp import tools

    try:
        # Imported inside the try, not above it: ``sdk_available`` proves the
        # package is findable, not that it imports. A half-installed extra or an
        # SDK too old for this call must degrade to a 503 on one path, exactly
        # like a store that will not open, rather than kill the whole daemon.
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

        from netadmin.mcp.server import build_server
        from netadmin.store.repository import Repository

        # The mount's OWN connection, never the daemon's read-write handle.
        state.repo = Repository.open(settings.db_path, site_id=settings.site_id, read_only=True)
        manager = StreamableHTTPSessionManager(
            app=build_server(state.repo),
            event_store=None,
            # See the module docstring: JSON bodies rather than SSE, no session
            # table, and DNS-rebinding protection left off (security_settings
            # unset) because a bearer token is the stronger guard and a Host
            # allow-list would break LAN-by-IP access.
            json_response=True,
            stateless=True,
        )
        state.stack = AsyncExitStack()
        await state.stack.enter_async_context(manager.run())
    except Exception as exc:  # noqa: BLE001 - a broken mount must not down the daemon
        state.unavailable = f"the remote MCP endpoint failed to start: {exc}"
        _log.warning("remote MCP: failed to start; %s answers 503", MCP_PATH, exc_info=True)
        await _close_repo(state)
        return

    state.manager = manager
    state.unavailable = None
    _log.info(
        "remote MCP: %s mounted (streamable HTTP, stateless, read-only), %d tools, "
        "bearer auth via NETADMIN_MCP_TOKEN%s",
        MCP_PATH,
        len(tools.TOOLS),
        " (redaction is ON)" if tools.redaction_enabled() else "",
    )


async def stop_mcp(app: Any) -> None:
    """Stop the session manager and close the mount's store handle."""
    state: Optional[McpMountState] = getattr(app.state, "mcp", None)
    if state is None:
        return
    state.manager = None
    if state.stack is not None:
        try:
            await state.stack.aclose()
        except Exception:  # noqa: BLE001 - best-effort cleanup, we are tearing down
            _log.warning("error stopping the remote MCP session manager", exc_info=True)
        state.stack = None
    await _close_repo(state)


async def _close_repo(state: McpMountState) -> None:
    if state.repo is not None:
        try:
            state.repo.close()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            _log.warning("error closing the remote MCP store handle", exc_info=True)
        state.repo = None
