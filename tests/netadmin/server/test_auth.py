"""Static-token API auth (ARCHITECTURE.md 12 + 18.1): HTTP gate + WebSocket gate.

The daemon is LAN-reachable. Under the 18.1 "already set up = just works" model,
GET reads are open once configured; only state-changing requests (the fix engine's
apply/revert, ack/snooze, setup/connect) require ``NETADMIN_API_TOKEN``. Unset
means fully open access with a startup warning (controller mutations still fail
closed). These tests pin: GET reads open even with a token set, a state-changing
POST gated without the token, health always open, OPTIONS/preflight pass-through,
the constant-time comparator is the one exercised on a gated route, controller
mutations fail closed + rate limited, and the WebSocket ``?token=`` gate.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import httpx
import pytest

from netadmin.config import Settings
from netadmin.server import auth as auth_mod
from netadmin.server.auth import (
    ApiTokenAuthMiddleware,
    extract_bearer,
    is_controller_mutation,
    is_investigate_route,
    token_matches,
)
from netadmin.server.main import DaemonComponents, create_app
from netadmin.server.ws import websocket_endpoint
from netadmin.store.repository import Repository

TOKEN = "s3cr3t-test-token"


async def _ok_app(scope: Any, receive: Any, send: Any) -> None:
    """A trivial downstream ASGI app: 200 for anything that clears the gate."""
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": b'{"ok":true}'})


async def _mw_client(mw: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=mw)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


_APPLY_PATH = "/api/issues/1/fix/apply"
_REVERT_PATH = "/api/issues/1/fix/revert"


@pytest.fixture
def token_settings(tmp_db_path: Path) -> Settings:
    return Settings(
        _env_file=None, db_path=tmp_db_path, site_id="default", netadmin_api_token=TOKEN
    )


@pytest.fixture
def token_app(token_settings: Settings, seeded_store: Repository) -> Any:
    return create_app(settings=token_settings, store=seeded_store, components=DaemonComponents())


async def _client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


# --- unit: comparator + header parsing ------------------------------------- #


def test_token_matches_rejects_missing_sides() -> None:
    assert token_matches(None, TOKEN) is False
    assert token_matches(TOKEN, None) is False
    assert token_matches("", TOKEN) is False
    assert token_matches(TOKEN, TOKEN) is True
    assert token_matches("wrong", TOKEN) is False


def test_extract_bearer_parses_scheme_case_insensitively() -> None:
    assert extract_bearer("Bearer abc") == "abc"
    assert extract_bearer("bearer abc") == "abc"
    assert extract_bearer("Basic abc") is None
    assert extract_bearer("abc") is None
    assert extract_bearer(None) is None


def test_settings_api_token_strips_blank() -> None:
    s = Settings(_env_file=None, netadmin_api_token="   ")
    assert s.api_token is None
    s2 = Settings(_env_file=None, netadmin_api_token="  tok  ")
    assert s2.api_token == "tok"


def test_is_investigate_route_matches_only_the_post_route() -> None:
    assert is_investigate_route("POST", "/api/issues/42/investigate")
    assert is_investigate_route("POST", "/api/issues/1/investigate")
    assert not is_investigate_route("GET", "/api/issues/42/investigate")
    assert not is_investigate_route("POST", "/api/issues/investigate/providers")
    assert not is_investigate_route("POST", "/api/issues/42/investigations/import")
    assert not is_investigate_route("POST", "/api/issues/42/investigate/extra")


# --- HTTP gate ------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_get_read_open_even_with_token_configured(token_app: object) -> None:
    # ARCHITECTURE.md 18.1: reads are open on the LAN once configured -- a GET is
    # served WITHOUT a token, so a configured daemon just loads the dashboard.
    async with await _client(token_app) as c:
        resp = await c.get("/api/issues")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_api_route_200_with_token(token_app: object) -> None:
    async with await _client(token_app) as c:
        resp = await c.get("/api/issues", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_read_open_even_with_wrong_token(token_app: object) -> None:
    # A wrong token on a GET does not lock viewing: reads are open regardless (the
    # token gates mutations, not reads). The stated 18.1 tradeoff.
    async with await _client(token_app) as c:
        resp = await c.get("/api/issues", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_state_changing_post_requires_token_when_configured() -> None:
    # A non-controller state-changing route (ack/snooze) still requires the token
    # once configured -- only reads are open, mutations are gated just-in-time.
    ack_path = "/api/issues/1/ack"
    mw = ApiTokenAuthMiddleware(_ok_app, token=TOKEN)
    async with await _mw_client(mw) as c:
        no_tok = await c.post(ack_path, json={})
        good = await c.post(ack_path, json={}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert no_tok.status_code == 401
    assert no_tok.headers.get("www-authenticate") == "Bearer"
    assert good.status_code == 200


@pytest.mark.asyncio
async def test_investigate_route_open_at_middleware_even_with_token_configured() -> None:
    # The middleware never parses the body, so it cannot tell manual from
    # copilot/anthropic -- it lets every investigate POST through unconditionally
    # and leaves the per-provider token decision to the handler (routers/issues.py,
    # covered in tests/netadmin/server/routers/test_investigate.py).
    mw = ApiTokenAuthMiddleware(_ok_app, token=TOKEN)
    async with await _mw_client(mw) as c:
        no_tok = await c.post("/api/issues/1/investigate", json={"provider": "manual"})
    assert no_tok.status_code == 200


@pytest.mark.asyncio
async def test_health_open_even_with_token(token_app: object) -> None:
    async with await _client(token_app) as c:
        resp = await c.get("/api/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_open_without_token_configured(app: object) -> None:
    # No token configured -> the whole API is open (and health specifically).
    async with await _client(app) as c:
        assert (await c.get("/api/health")).status_code == 200
        assert (await c.get("/api/issues")).status_code == 200


@pytest.mark.asyncio
async def test_options_preflight_not_gated(token_app: object) -> None:
    # A CORS preflight carries no Authorization header; it must reach the CORS layer.
    async with await _client(token_app) as c:
        resp = await c.options(
            "/api/issues",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert resp.status_code < 400


@pytest.mark.asyncio
async def test_constant_time_comparator_is_used(
    token_app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Bytes, not str: token_matches encodes before comparing, because
    # hmac.compare_digest raises TypeError on a non-ASCII str and Starlette
    # decodes headers as latin-1.
    calls: list[tuple[bytes, bytes]] = []
    real = auth_mod._compare

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(auth_mod, "_compare", spy)
    # A GET read is open (18.1) and never consults the token, so exercise the
    # comparator on a gated route: the controller-mutation apply path.
    async with await _client(token_app) as c:
        await c.post(_APPLY_PATH, json={}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert calls, "auth did not go through the constant-time comparator"
    assert calls[-1] == (TOKEN.encode(), TOKEN.encode())


def test_unauthenticated_startup_logs_warning(settings: Settings, seeded_store: Repository) -> None:
    # The ``netadmin`` logger sets propagate=False, so caplog (root-attached) misses
    # it; attach a handler to the module logger directly to capture the one warning.
    import asyncio

    messages: list[str] = []

    class _Cap(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    logger = logging.getLogger("netadmin.server.main")
    handler = _Cap()
    logger.addHandler(handler)
    app = create_app(settings=settings, store=seeded_store, components=DaemonComponents())

    async def _run() -> None:
        async with app.router.lifespan_context(app):
            pass

    try:
        asyncio.run(_run())
    finally:
        logger.removeHandler(handler)
    assert any("NETADMIN_API_TOKEN" in m for m in messages)


# --- WebSocket gate -------------------------------------------------------- #


class _FakeQueryParams:
    def __init__(self, params: dict[str, str]) -> None:
        self._p = params

    def get(self, key: str) -> Optional[str]:
        return self._p.get(key)


class _AuthFakeWebSocket:
    """A WebSocket stand-in that carries query params + app state for auth checks."""

    def __init__(self, app: object, token: Optional[str]) -> None:
        from starlette.websockets import WebSocketState

        self.app = app
        self._State = WebSocketState
        self.application_state = WebSocketState.CONNECTING
        self.accepted = False
        self.closed = False
        self.close_code: Optional[int] = None
        self.query_params = _FakeQueryParams({"token": token} if token is not None else {})
        self.sent: list[dict] = []

    async def accept(self) -> None:
        self.accepted = True
        self.application_state = self._State.CONNECTED

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def receive_text(self) -> str:
        raise AssertionError("auth path should not read frames in these tests")

    async def close(self, code: int = 1000) -> None:
        self.closed = True
        self.close_code = code
        self.application_state = self._State.DISCONNECTED


@pytest.mark.asyncio
async def test_ws_rejected_without_token(token_app: object) -> None:
    ws = _AuthFakeWebSocket(token_app, token=None)
    await websocket_endpoint(ws)  # type: ignore[arg-type]
    assert ws.accepted is False
    assert ws.closed is True
    assert ws.close_code == auth_mod.WS_UNAUTHORIZED_CODE


@pytest.mark.asyncio
async def test_ws_rejected_with_wrong_token(token_app: object) -> None:
    ws = _AuthFakeWebSocket(token_app, token="wrong")
    await websocket_endpoint(ws)  # type: ignore[arg-type]
    assert ws.accepted is False
    assert ws.close_code == auth_mod.WS_UNAUTHORIZED_CODE


@pytest.mark.asyncio
async def test_ws_accepted_with_token(token_app: object) -> None:
    ws = _AuthFakeWebSocket(token_app, token=TOKEN)
    import asyncio

    task = asyncio.create_task(websocket_endpoint(ws))  # type: ignore[arg-type]
    for _ in range(10):
        await asyncio.sleep(0)
        if ws.accepted:
            break
    assert ws.accepted is True
    # Tear down cleanly.
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass


# --- Controller mutations fail closed + are rate limited -------------------- #


def test_is_controller_mutation_classifies_only_the_write_routes() -> None:
    assert is_controller_mutation("POST", "/api/issues/1/fix/apply")
    assert is_controller_mutation("POST", "/api/issues/42/fix/revert")
    # Reads, local-state mutations, and non-/api paths are not controller writes.
    assert not is_controller_mutation("GET", "/api/issues/1/fix-plan")
    assert not is_controller_mutation("POST", "/api/issues/1/ack")
    assert not is_controller_mutation("GET", "/api/issues/1/fix/apply")
    assert not is_controller_mutation("POST", "/other/fix/apply")


@pytest.mark.asyncio
async def test_mutation_fails_closed_when_no_token_configured() -> None:
    # No token => the middleware is open for reads, but a controller mutation is
    # refused outright (403) rather than passed through to the fix engine.
    mw = ApiTokenAuthMiddleware(_ok_app, token=None)
    async with await _mw_client(mw) as c:
        apply_resp = await c.post(_APPLY_PATH, json={"confirm": True, "confirm_token": "x"})
        revert_resp = await c.post(_REVERT_PATH, json={"change_id": 1})
    assert apply_resp.status_code == 403
    assert apply_resp.json()["code"] == "mutation_locked"
    assert revert_resp.status_code == 403


@pytest.mark.asyncio
async def test_open_api_reads_stay_open_but_mutation_is_refused(app: object) -> None:
    # End-to-end on the real (unauthenticated) app: the exact fail-open path from
    # the finding is gone. A read is open; POST /fix/apply is refused before the
    # route (and therefore before any controller contact) can run.
    async with await _client(app) as c:
        assert (await c.get("/api/issues")).status_code == 200
        resp = await c.post(_APPLY_PATH, json={"confirm": True, "confirm_token": "x"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "mutation_locked"


@pytest.mark.asyncio
async def test_mutation_requires_token_when_configured() -> None:
    mw = ApiTokenAuthMiddleware(_ok_app, token=TOKEN)
    async with await _mw_client(mw) as c:
        no_tok = await c.post(_APPLY_PATH, json={})
        wrong = await c.post(_APPLY_PATH, json={}, headers={"Authorization": "Bearer nope"})
        good = await c.post(_APPLY_PATH, json={}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert no_tok.status_code == 401
    assert wrong.status_code == 401
    assert good.status_code == 200  # cleared the gate to the downstream app


@pytest.mark.asyncio
async def test_write_ops_are_rate_limited() -> None:
    mw = ApiTokenAuthMiddleware(_ok_app, token=TOKEN, write_max=2, write_window_s=60.0)
    hdr = {"Authorization": f"Bearer {TOKEN}"}
    async with await _mw_client(mw) as c:
        r1 = await c.post(_APPLY_PATH, json={}, headers=hdr)
        r2 = await c.post(_APPLY_PATH, json={}, headers=hdr)
        r3 = await c.post(_APPLY_PATH, json={}, headers=hdr)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r3.status_code == 429
    assert r3.json()["code"] == "rate_limited"


@pytest.mark.asyncio
async def test_rate_limit_window_expires() -> None:
    clock = {"t": 0.0}
    mw = ApiTokenAuthMiddleware(
        _ok_app, token=TOKEN, write_max=1, write_window_s=10.0, now_fn=lambda: clock["t"]
    )
    hdr = {"Authorization": f"Bearer {TOKEN}"}
    async with await _mw_client(mw) as c:
        first = await c.post(_APPLY_PATH, json={}, headers=hdr)
        blocked = await c.post(_APPLY_PATH, json={}, headers=hdr)
        clock["t"] = 11.0  # window elapsed
        after = await c.post(_APPLY_PATH, json={}, headers=hdr)
    assert first.status_code == 200
    assert blocked.status_code == 429
    assert after.status_code == 200


@pytest.mark.asyncio
async def test_reads_are_not_rate_limited() -> None:
    # The write-op limiter must not touch reads: many GETs never trip it.
    mw = ApiTokenAuthMiddleware(_ok_app, token=TOKEN, write_max=1, write_window_s=60.0)
    hdr = {"Authorization": f"Bearer {TOKEN}"}
    async with await _mw_client(mw) as c:
        for _ in range(5):
            assert (await c.get("/api/issues", headers=hdr)).status_code == 200
