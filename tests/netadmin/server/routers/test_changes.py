"""ASGI-transport tests for GET /api/changes."""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def _client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_changes_decodes_state_and_resolves_entity(rich_app) -> None:
    async with await _client(rich_app) as c:
        resp = await c.get("/api/changes")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    ch = body["changes"][0]
    assert ch["action"] == "set_channel"
    assert ch["status"] == "applied"
    assert ch["reverted_ts"] is None
    assert ch["before"] == {"channel": 6}
    assert ch["after"] == {"channel": 11}
    assert ch["entity"]["name"] == "ap-office"


async def test_changes_filter_by_entity(rich_app) -> None:
    ap_id = rich_app.state.store.ap_id
    client_id = rich_app.state.store.client_id
    async with await _client(rich_app) as c:
        on_ap = await c.get("/api/changes", params={"entity_id": ap_id})
        on_client = await c.get("/api/changes", params={"entity_id": client_id})
    assert on_ap.json()["count"] == 1
    assert on_client.json()["count"] == 0


async def test_changes_503_when_store_absent(settings) -> None:
    from netadmin.server.main import DaemonComponents, create_app

    app = create_app(settings=settings, store=None, components=DaemonComponents())
    async with await _client(app) as c:
        resp = await c.get("/api/changes")
    assert resp.status_code == 503
