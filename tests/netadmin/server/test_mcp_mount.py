"""The token-gated remote MCP mount at ``/mcp`` (ARCHITECTURE.md 18.3, Gitea #30).

This is the security-critical surface: ``GET /api/*`` reads are open on the LAN
once configured (18.1) and the daemon is LAN-published, so an ungated ``/mcp``
would hand any guest device the entire history store in one tool call. These
tests pin the whole posture ladder and the read-only guarantee:

- no ``NETADMIN_MCP_TOKEN`` -> 404, not a gate for a feature that is not there,
  and specifically not the SPA's ``index.html``;
- wrong or missing token -> 401 with ``WWW-Authenticate: Bearer``, decided
  before a single byte of the request body is read;
- repeated failures -> 429 with ``Retry-After``, while a *successful* client is
  never throttled;
- SDK absent with a token set -> 503 naming the install line, but only for a
  caller who already authenticated;
- authenticated -> a real MCP session serving all 11 tools;
- the connection behind those tools cannot write, and is not the daemon's;
- the schema gate still fires here, not just at stdio startup;
- and the two credentials never cross over in either direction.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

import httpx
import pytest

from netadmin import __version__
from netadmin.config import Settings
from netadmin.mcp import tools as mcp_tools
from netadmin.server import mcp_mount
from netadmin.server.main import DaemonComponents, create_app
from netadmin.store.repository import Repository

pytestmark = pytest.mark.asyncio

# Everything that drives a real MCP session needs the optional ``[mcp]`` extra.
# The gate itself does not: 404, 401, 429 and the SDK-missing 503 are all decided
# before the SDK is ever reached, and they stay under test on a core install --
# which is the posture most deploys actually run.
requires_sdk = pytest.mark.skipif(
    not mcp_mount.sdk_available(), reason="the optional [mcp] extra is not installed"
)

MCP_TOKEN = "s3cr3t-mcp-test-token"
API_TOKEN = "s3cr3t-api-test-token"

# What an MCP client sends. The mount runs the SDK's streamable HTTP in JSON-
# response mode, so ``application/json`` is the only strictly required Accept
# type, but real clients offer both and so do these tests.
_JSON_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "netadmin-tests", "version": "0"},
    },
}
_TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


def _headers(token: Optional[str] = MCP_TOKEN) -> dict[str, str]:
    headers = dict(_JSON_HEADERS)
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


@pytest.fixture
def mcp_settings(tmp_db_path: Path) -> Settings:
    """A configured daemon with the MCP token set and NO API token."""
    return Settings(
        _env_file=None,
        db_path=tmp_db_path,
        site_id="default",
        netadmin_mcp_token=MCP_TOKEN,
    )


@pytest.fixture
def mcp_app(mcp_settings: Settings, seeded_store: Repository) -> Any:
    return create_app(settings=mcp_settings, store=seeded_store, components=DaemonComponents())


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _session(client: httpx.AsyncClient, token: Optional[str] = MCP_TOKEN) -> None:
    """Drive the MCP initialize handshake so ``tools/*`` is legal afterwards."""
    init = await client.post("/mcp", headers=_headers(token), json=_INITIALIZE)
    assert init.status_code == 200, init.text
    ack = await client.post(
        "/mcp",
        headers=_headers(token),
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert ack.status_code == 202, ack.text


# --------------------------------------------------------------------------- #
# Posture 1: no token configured -> the feature is absent
# --------------------------------------------------------------------------- #
async def test_mcp_returns_404_when_no_token_is_configured(app: Any) -> None:
    """The default install has no NETADMIN_MCP_TOKEN, so /mcp is simply not there."""
    async with app.router.lifespan_context(app), _client(app) as client:
        posted = await client.post("/mcp", headers=_headers(None), json=_TOOLS_LIST)
        assert posted.status_code == 404
        assert posted.json()["code"] == "mcp_disabled"
        assert "NETADMIN_MCP_TOKEN" in posted.json()["detail"]


async def test_mcp_404_is_not_the_spa_index_html(app: Any) -> None:
    """A GET must 404 too, rather than falling through to the SPA catch-all.

    The route is registered before ``_mount_spa`` precisely so an operator who
    mistypes the token config gets a 404 instead of a 200 full of HTML.
    """
    async with app.router.lifespan_context(app), _client(app) as client:
        got = await client.get("/mcp", headers=_headers(None))
        assert got.status_code == 404
        assert got.headers["content-type"].startswith("application/json")


async def test_a_valid_looking_token_does_not_open_a_disabled_mount(app: Any) -> None:
    """No configured token means no token opens it. There is nothing to guess."""
    async with app.router.lifespan_context(app), _client(app) as client:
        posted = await client.post("/mcp", headers=_headers(MCP_TOKEN), json=_TOOLS_LIST)
        assert posted.status_code == 404


# --------------------------------------------------------------------------- #
# Posture 2: wrong or missing token -> 401
# --------------------------------------------------------------------------- #
async def test_wrong_token_is_401_with_www_authenticate(mcp_app: Any) -> None:
    async with mcp_app.router.lifespan_context(mcp_app), _client(mcp_app) as client:
        refused = await client.post("/mcp", headers=_headers("wrong-token"), json=_TOOLS_LIST)
        assert refused.status_code == 401
        assert refused.headers["www-authenticate"] == "Bearer"
        assert refused.json() == {"detail": "authentication required", "code": "unauthorized"}


async def test_missing_authorization_header_is_401(mcp_app: Any) -> None:
    async with mcp_app.router.lifespan_context(mcp_app), _client(mcp_app) as client:
        refused = await client.post("/mcp", headers=_headers(None), json=_TOOLS_LIST)
        assert refused.status_code == 401


async def test_non_bearer_authorization_is_401(mcp_app: Any) -> None:
    headers = dict(_JSON_HEADERS)
    headers["Authorization"] = f"Basic {MCP_TOKEN}"
    async with mcp_app.router.lifespan_context(mcp_app), _client(mcp_app) as client:
        refused = await client.post("/mcp", headers=headers, json=_TOOLS_LIST)
        assert refused.status_code == 401


async def test_token_comparison_is_constant_time(
    mcp_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate must go through the timing-safe comparator, not ``==``.

    Patched at the same module-level seam ``test_auth.py`` uses for the API gate,
    which is what proves both surfaces share one comparison path.
    """
    from netadmin.server import auth as auth_mod

    calls: list[tuple[bytes, bytes]] = []

    def _spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return False

    monkeypatch.setattr(auth_mod, "_compare", _spy)
    async with mcp_app.router.lifespan_context(mcp_app), _client(mcp_app) as client:
        refused = await client.post("/mcp", headers=_headers(MCP_TOKEN), json=_TOOLS_LIST)
    assert refused.status_code == 401
    assert calls == [(MCP_TOKEN.encode(), MCP_TOKEN.encode())]


async def test_auth_is_decided_before_any_body_is_read(mcp_app: Any) -> None:
    """The 401 must be reachable without the daemon reading one request byte.

    Driven at the ASGI layer rather than through httpx so ``receive`` can be a
    tripwire: an unauthenticated caller must never get the daemon to buffer,
    parse or dispatch a JSON-RPC body, whatever size it claims to be.
    """
    endpoint = mcp_mount.McpEndpoint(mcp_app)
    received: list[str] = []
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:  # pragma: no cover - must never run
        received.append("read")
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [
            (b"authorization", b"Bearer not-the-token"),
            (b"content-type", b"application/json"),
            (b"content-length", b"9999999"),
        ],
        "client": ("192.168.1.50", 51234),
        "query_string": b"",
    }
    await endpoint(scope, receive, send)

    assert received == []
    assert sent[0]["status"] == 401


# --------------------------------------------------------------------------- #
# Posture 3: repeated failures are rate limited, successes are not
# --------------------------------------------------------------------------- #
async def test_repeated_failures_are_rate_limited(mcp_app: Any) -> None:
    async with mcp_app.router.lifespan_context(mcp_app), _client(mcp_app) as client:
        statuses = [
            (
                await client.post("/mcp", headers=_headers(f"guess-{i}"), json=_TOOLS_LIST)
            ).status_code
            for i in range(mcp_mount.DEFAULT_MCP_AUTH_MAX + 3)
        ]
        limited = await client.post("/mcp", headers=_headers("guess-again"), json=_TOOLS_LIST)

    assert statuses[: mcp_mount.DEFAULT_MCP_AUTH_MAX] == [401] * mcp_mount.DEFAULT_MCP_AUTH_MAX
    assert statuses[mcp_mount.DEFAULT_MCP_AUTH_MAX :] == [429, 429, 429]
    assert limited.status_code == 429
    assert limited.json()["code"] == "rate_limited"
    assert limited.headers["retry-after"] == str(int(mcp_mount.DEFAULT_MCP_AUTH_WINDOW_S))


async def test_the_rate_limit_window_expires(mcp_app: Any) -> None:
    """A throttled client is not locked out forever: the window rolls.

    Built with a non-default budget and window so ``Retry-After`` is checked
    against the endpoint's own configuration, not the module constant.
    """
    clock = {"t": 0.0}
    endpoint = mcp_mount.McpEndpoint(
        mcp_app, auth_max=3, auth_window_s=30.0, now_fn=lambda: clock["t"]
    )
    transport = httpx.ASGITransport(app=endpoint)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(3):
            assert (await client.post("/mcp", headers=_headers("nope"))).status_code == 401
        limited = await client.post("/mcp", headers=_headers("nope"))
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == "30"
        clock["t"] = 31.0
        assert (await client.post("/mcp", headers=_headers("nope"))).status_code == 401


@requires_sdk
async def test_successful_requests_are_never_throttled(mcp_app: Any) -> None:
    """Only failures count. A working client makes many calls per session."""
    async with mcp_app.router.lifespan_context(mcp_app), _client(mcp_app) as client:
        await _session(client)
        for _ in range(mcp_mount.DEFAULT_MCP_AUTH_MAX * 2):
            listed = await client.post("/mcp", headers=_headers(), json=_TOOLS_LIST)
            assert listed.status_code == 200


# --------------------------------------------------------------------------- #
# Posture 4: SDK absent but a token is set -> 503, only for an authenticated peer
# --------------------------------------------------------------------------- #
async def test_503_names_the_install_line_when_the_sdk_is_absent(
    mcp_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp_mount, "sdk_available", lambda: False)
    async with mcp_app.router.lifespan_context(mcp_app), _client(mcp_app) as client:
        refused = await client.post("/mcp", headers=_headers(), json=_TOOLS_LIST)
    assert refused.status_code == 503
    body = refused.json()
    assert body["code"] == "mcp_unavailable"
    assert 'pip install "unifioptimizer[mcp]"' in body["detail"]


async def test_missing_sdk_is_not_disclosed_to_an_unauthenticated_caller(
    mcp_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 503 is deployment state. Only the operator, who can act on it, sees it."""
    monkeypatch.setattr(mcp_mount, "sdk_available", lambda: False)
    async with mcp_app.router.lifespan_context(mcp_app), _client(mcp_app) as client:
        refused = await client.post("/mcp", headers=_headers("wrong-token"), json=_TOOLS_LIST)
    assert refused.status_code == 401


async def test_no_store_handle_is_opened_when_the_sdk_is_absent(
    mcp_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp_mount, "sdk_available", lambda: False)
    async with mcp_app.router.lifespan_context(mcp_app):
        assert mcp_app.state.mcp.repo is None
        assert mcp_app.state.mcp.manager is None


@requires_sdk
async def test_a_failed_start_answers_503_rather_than_downing_the_daemon(
    mcp_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken mount is a 503 on one path, never a daemon that will not boot."""

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("no store for you")

    monkeypatch.setattr(Repository, "open", staticmethod(_boom))
    async with mcp_app.router.lifespan_context(mcp_app), _client(mcp_app) as client:
        assert mcp_app.state.daemon.ready is True
        refused = await client.post("/mcp", headers=_headers(), json=_TOOLS_LIST)
    assert refused.status_code == 503
    assert "no store for you" in refused.json()["detail"]


async def test_503_when_the_lifespan_never_ran(mcp_app: Any) -> None:
    """An app built but not started serves an honest 503, never a traceback."""
    async with _client(mcp_app) as client:
        refused = await client.post("/mcp", headers=_headers(), json=_TOOLS_LIST)
    assert refused.status_code == 503
    assert refused.json()["detail"] == mcp_mount.NOT_RUNNING_DETAIL


# --------------------------------------------------------------------------- #
# Posture 5: authenticated -> a real MCP session over all 11 tools
# --------------------------------------------------------------------------- #
@requires_sdk
async def test_initialize_announces_this_build(mcp_app: Any) -> None:
    async with mcp_app.router.lifespan_context(mcp_app), _client(mcp_app) as client:
        init = await client.post("/mcp", headers=_headers(), json=_INITIALIZE)
    assert init.status_code == 200
    info = init.json()["result"]["serverInfo"]
    assert info["name"] == "unifioptimizer"
    assert info["version"] == __version__


@requires_sdk
async def test_tools_list_returns_all_eleven_tools(mcp_app: Any) -> None:
    async with mcp_app.router.lifespan_context(mcp_app), _client(mcp_app) as client:
        await _session(client)
        listed = await client.post("/mcp", headers=_headers(), json=_TOOLS_LIST)

    assert listed.status_code == 200
    served = listed.json()["result"]["tools"]
    assert len(served) == 11
    assert {tool["name"] for tool in served} == set(mcp_tools.TOOLS)
    # The same registry the stdio server binds, descriptions and schemas included.
    for tool in served:
        spec = mcp_tools.TOOLS[tool["name"]]
        assert tool["description"] == spec.description
        assert tool["inputSchema"] == spec.input_schema


@requires_sdk
async def test_tools_call_answers_from_the_history_store(mcp_app: Any) -> None:
    async with mcp_app.router.lifespan_context(mcp_app), _client(mcp_app) as client:
        await _session(client)
        called = await client.post(
            "/mcp",
            headers=_headers(),
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "netadmin_issues", "arguments": {"open_only": True}},
            },
        )

    assert called.status_code == 200
    payload = json.loads(called.json()["result"]["content"][0]["text"])
    assert "summary" in payload
    titles = [row["title"] for row in payload["issues"]["items"]]
    assert "rx_errors climbing on port 5" in titles


@requires_sdk
async def test_the_schema_gate_fires_on_this_surface(
    mcp_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store this build does not understand is a sentence, on every transport.

    The gate lives inside ``tools.call_tool``, so it reaches HTTP callers the
    same way it reaches stdio ones. Pinned here because a mount that bound the
    handlers directly would silently skip it.
    """
    monkeypatch.setattr(
        mcp_tools._db, "latest_migration_version", lambda: 9999  # type: ignore[attr-defined]
    )
    async with mcp_app.router.lifespan_context(mcp_app), _client(mcp_app) as client:
        await _session(client)
        called = await client.post(
            "/mcp",
            headers=_headers(),
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "netadmin_overview", "arguments": {}},
            },
        )

    payload = json.loads(called.json()["result"]["content"][0]["text"])
    assert payload["error"] == "schema_mismatch"
    assert "9999" in payload["summary"]


# --------------------------------------------------------------------------- #
# Read-only, proven rather than asserted in a docstring
# --------------------------------------------------------------------------- #
@requires_sdk
async def test_the_mcp_connection_cannot_write_to_the_store(mcp_app: Any) -> None:
    """Three independent locks, checked one at a time.

    ``mode=ro`` at the VFS layer, ``PRAGMA query_only=ON`` on the connection, and
    a repository whose write methods therefore fail. Any one of them regressing
    fails here.
    """
    async with mcp_app.router.lifespan_context(mcp_app):
        repo: Repository = mcp_app.state.mcp.repo
        assert repo is not None
        conn = repo.connection

        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1

        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO issues (fingerprint, detector_key) VALUES ('x', 'y')")

        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM issues")

        with pytest.raises(sqlite3.OperationalError):
            repo.record_issue_event(1, "detected", ts=0)

        # query_only alone could be flipped back off by a stray PRAGMA; mode=ro
        # cannot, because the file itself was opened O_RDONLY.
        conn.execute("PRAGMA query_only=OFF")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM issues")


@requires_sdk
async def test_the_mount_never_borrows_the_daemons_writable_handle(
    mcp_app: Any, seeded_store: Repository
) -> None:
    async with mcp_app.router.lifespan_context(mcp_app):
        assert mcp_app.state.mcp.repo is not seeded_store
        assert mcp_app.state.mcp.repo.connection is not seeded_store.connection
        # ...and the daemon's own handle is untouched by any of this.
        seeded_store.record_issue_event(1, "detected", ts=1)


@requires_sdk
async def test_a_tools_call_leaves_the_store_unchanged(mcp_app: Any, tmp_db_path: Path) -> None:
    """End to end: drive every tool over HTTP, then diff the file on disk."""
    async with mcp_app.router.lifespan_context(mcp_app), _client(mcp_app) as client:
        await _session(client)
        before = tmp_db_path.read_bytes()
        for index, name in enumerate(mcp_tools.TOOLS):
            called = await client.post(
                "/mcp",
                headers=_headers(),
                json={
                    "jsonrpc": "2.0",
                    "id": 100 + index,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": {}},
                },
            )
            assert called.status_code == 200
        assert tmp_db_path.read_bytes() == before


@requires_sdk
async def test_the_mount_closes_its_handle_on_shutdown(mcp_app: Any) -> None:
    async with mcp_app.router.lifespan_context(mcp_app):
        repo: Repository = mcp_app.state.mcp.repo
        assert repo.connection.execute("SELECT 1").fetchone()[0] == 1
    assert mcp_app.state.mcp.repo is None
    assert mcp_app.state.mcp.manager is None
    with pytest.raises(sqlite3.ProgrammingError):
        repo.connection.execute("SELECT 1")


# --------------------------------------------------------------------------- #
# The two credentials never cross over
# --------------------------------------------------------------------------- #
@requires_sdk
async def test_the_api_token_does_not_authorize_the_mcp_mount(
    tmp_db_path: Path, seeded_store: Repository
) -> None:
    """A leaked API token must not become a history-store reader, and vice versa.

    The API token authorizes controller mutations. If it also opened ``/mcp``,
    every laptop with a Claude config would be holding network-change authority.
    """
    settings = Settings(
        _env_file=None,
        db_path=tmp_db_path,
        site_id="default",
        netadmin_api_token=API_TOKEN,
        netadmin_mcp_token=MCP_TOKEN,
    )
    app = create_app(settings=settings, store=seeded_store, components=DaemonComponents())
    async with app.router.lifespan_context(app), _client(app) as client:
        refused = await client.post("/mcp", headers=_headers(API_TOKEN), json=_TOOLS_LIST)
        assert refused.status_code == 401
        accepted = await client.post("/mcp", headers=_headers(MCP_TOKEN), json=_INITIALIZE)
        assert accepted.status_code == 200


async def test_the_mcp_token_does_not_authorize_an_api_mutation(
    tmp_db_path: Path, seeded_store: Repository
) -> None:
    settings = Settings(
        _env_file=None,
        db_path=tmp_db_path,
        site_id="default",
        netadmin_api_token=API_TOKEN,
        netadmin_mcp_token=MCP_TOKEN,
    )
    app = create_app(settings=settings, store=seeded_store, components=DaemonComponents())
    async with app.router.lifespan_context(app), _client(app) as client:
        refused = await client.post(
            "/api/issues/1/fix/apply",
            headers={"Authorization": f"Bearer {MCP_TOKEN}"},
            json={},
        )
        assert refused.status_code == 401


async def test_an_mcp_token_alone_does_not_unlock_controller_mutations(mcp_app: Any) -> None:
    """With only NETADMIN_MCP_TOKEN set, apply/revert stay failed-closed (403)."""
    async with mcp_app.router.lifespan_context(mcp_app), _client(mcp_app) as client:
        refused = await client.post(
            "/api/issues/1/fix/apply",
            headers={"Authorization": f"Bearer {MCP_TOKEN}"},
            json={},
        )
        assert refused.status_code == 403
        assert refused.json()["code"] == "mutation_locked"


@requires_sdk
async def test_a_rotated_token_takes_effect_without_a_restart(mcp_app: Any) -> None:
    """The token is read live off ``app.state.settings``, like the API token."""
    async with mcp_app.router.lifespan_context(mcp_app), _client(mcp_app) as client:
        assert (await client.post("/mcp", headers=_headers(), json=_INITIALIZE)).status_code == 200
        mcp_app.state.settings = mcp_app.state.settings.model_copy(
            update={"netadmin_mcp_token": "rotated-mcp-token"}
        )
        assert (await client.post("/mcp", headers=_headers(), json=_TOOLS_LIST)).status_code == 401
        assert (
            await client.post("/mcp", headers=_headers("rotated-mcp-token"), json=_INITIALIZE)
        ).status_code == 200


# --------------------------------------------------------------------------- #
# Regression: adding /mcp changed nothing about the existing /api gate
# --------------------------------------------------------------------------- #
# ``test_auth.py`` is the full contract for ``ApiTokenAuthMiddleware`` and still
# passes unchanged. What follows is the narrower question this change raises:
# does a daemon that now carries a *second* credential and a *second* gate still
# behave exactly as before on ``/api``? Each case below is run on an app whose
# ``/mcp`` mount is live, which the original suite never exercises.


async def test_api_reads_stay_open_with_the_mcp_mount_live(mcp_app: Any) -> None:
    """18.1's open-read posture is not tightened by the MCP token existing."""
    async with mcp_app.router.lifespan_context(mcp_app), _client(mcp_app) as client:
        for path in ("/api/health", "/api/issues", "/api/setup/status"):
            got = await client.get(path)
            assert got.status_code == 200, path


async def test_api_mutations_still_require_the_api_token(
    tmp_db_path: Path, seeded_store: Repository
) -> None:
    settings = Settings(
        _env_file=None,
        db_path=tmp_db_path,
        site_id="default",
        netadmin_api_token=API_TOKEN,
        netadmin_mcp_token=MCP_TOKEN,
    )
    app = create_app(settings=settings, store=seeded_store, components=DaemonComponents())
    async with app.router.lifespan_context(app), _client(app) as client:
        assert (await client.post("/api/issues/1/ack", json={})).status_code == 401
        acked = await client.post(
            "/api/issues/1/ack",
            headers={"Authorization": f"Bearer {API_TOKEN}"},
            json={},
        )
        assert acked.status_code == 200


async def test_controller_mutations_still_fail_closed_without_an_api_token(app: Any) -> None:
    """No API token at all: apply/revert are refused outright, MCP or not."""
    async with app.router.lifespan_context(app), _client(app) as client:
        for path in ("/api/issues/1/fix/apply", "/api/issues/1/fix/revert"):
            refused = await client.post(path, json={})
            assert refused.status_code == 403, path
            assert refused.json()["code"] == "mutation_locked"


async def test_the_api_gate_never_sees_the_mcp_path(mcp_app: Any) -> None:
    """``/mcp`` is outside ``/api``, so ``ApiTokenAuthMiddleware`` is a pass-through.

    Pinned so a future widening of that middleware's prefix cannot quietly become
    the thing that decides who reaches the history store.
    """
    from netadmin.server import auth as auth_mod

    assert not mcp_mount.MCP_PATH.startswith(auth_mod.API_PREFIX)
    assert auth_mod.is_controller_mutation("POST", mcp_mount.MCP_PATH) is False
    assert auth_mod.is_investigate_route("POST", mcp_mount.MCP_PATH) is False
    assert auth_mod.is_token_regenerate("POST", mcp_mount.MCP_PATH) is False
    assert auth_mod.is_system_update_apply("POST", mcp_mount.MCP_PATH) is False


async def test_both_gates_share_one_comparator_and_one_limiter() -> None:
    """The MCP gate is not a second, subtly different implementation."""
    from netadmin.server import auth as auth_mod

    assert mcp_mount.token_matches is auth_mod.token_matches
    assert mcp_mount.extract_bearer is auth_mod.extract_bearer
    assert mcp_mount.client_key is auth_mod.client_key
    assert mcp_mount.FixedWindowRateLimiter is auth_mod.FixedWindowRateLimiter


# --------------------------------------------------------------------------- #
# Startup posture logging
# --------------------------------------------------------------------------- #
class _LogCapture(logging.Handler):
    """Attached directly to ``netadmin.server.mcp``.

    The project's loggers set ``propagate=False``, so caplog (root-attached)
    never sees them; this mirrors the workaround in ``test_main_lifespan.py``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


async def _startup_log(app: Any) -> list[str]:
    logger = logging.getLogger("netadmin.server.mcp")
    handler = _LogCapture()
    logger.addHandler(handler)
    try:
        async with app.router.lifespan_context(app):
            pass
    finally:
        logger.removeHandler(handler)
    return handler.messages


@requires_sdk
async def test_startup_logs_the_mounted_posture(mcp_app: Any) -> None:
    messages = await _startup_log(mcp_app)
    assert any("/mcp mounted" in m and "11 tools" in m for m in messages)


async def test_startup_logs_the_disabled_posture(app: Any) -> None:
    """An operator must be able to tell "off" from "broken" at a glance."""
    messages = await _startup_log(app)
    assert any("disabled" in m and "404" in m for m in messages)


async def test_startup_warns_when_the_sdk_is_missing(
    mcp_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp_mount, "sdk_available", lambda: False)
    messages = await _startup_log(mcp_app)
    assert any('pip install "unifioptimizer[mcp]"' in m for m in messages)


# --------------------------------------------------------------------------- #
# Regressions from the adversarial security review of the mount (Gitea #30).
# Every one of these was reachable on the shipped build; each is pinned so the
# next refactor of the ladder above cannot quietly reopen it.
# --------------------------------------------------------------------------- #


async def test_trailing_slash_does_not_fall_through_to_the_spa(mcp_app: Any) -> None:
    """``GET /mcp/`` must hit the gate, not Starlette's catch-all.

    The route was registered for the exact path only. redirect_slashes never
    fires because the SPA catch-all matches everything, so the trailing-slash
    form skipped the gate entirely and answered 200 ``text/html``.
    """
    async with mcp_app.router.lifespan_context(mcp_app), _client(mcp_app) as client:
        resp = await client.get("/mcp/")
    assert resp.status_code != 200
    assert "text/html" not in resp.headers.get("content-type", "")


async def test_a_non_ascii_bearer_is_a_401_not_a_500(mcp_app: Any) -> None:
    """A high byte in the token must not raise out of the comparator.

    Starlette decodes headers as latin-1 and ``hmac.compare_digest`` rejects a
    str with any codepoint above U+007F, so this used to be an unauthenticated
    500 -- raised before the rate limiter, so unthrottled too.
    """
    async with mcp_app.router.lifespan_context(mcp_app), _client(mcp_app) as client:
        resp = await client.post(
            "/mcp",
            # Raw bytes: httpx refuses to encode a non-ASCII str header, but a
            # real client puts these bytes on the wire and Starlette hands them
            # to the middleware latin-1 decoded.
            headers={"Authorization": "Bearer café-not-the-token".encode("latin-1")},
            json=_TOOLS_LIST,
        )
    assert resp.status_code == 401


async def test_rate_limit_is_not_escapable_with_a_forwarded_header(mcp_app: Any) -> None:
    """Guessing must stay capped when the caller rotates ``X-Forwarded-For``.

    The limiter buckets by client key, which is computed before authentication.
    While that key trusted an unvalidated forwarded header, one header per guess
    bought a fresh budget and the 429 rung never applied.
    """
    async with mcp_app.router.lifespan_context(mcp_app), _client(mcp_app) as client:
        codes = [
            (
                await client.post(
                    "/mcp",
                    headers={**_headers("wrong"), "X-Forwarded-For": f"203.0.113.{i}"},
                    json=_TOOLS_LIST,
                )
            ).status_code
            for i in range(40)
        ]
    assert 429 in codes, "rotating X-Forwarded-For bought unlimited token guesses"
