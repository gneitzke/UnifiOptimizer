"""ASGI-transport tests for GET /api/events."""

from __future__ import annotations

import httpx
import pytest

from ..conftest import BASE_TS

pytestmark = pytest.mark.asyncio


async def _client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_events_resolves_names_and_decodes_data(rich_app) -> None:
    async with await _client(rich_app) as c:
        resp = await c.get("/api/events")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    ev = body["events"][0]
    assert ev["key"] == "EVT_WU_Roam"
    assert ev["entity"]["name"] == "iPhone"
    assert ev["related_entity"]["name"] == "ap-office"
    assert ev["data"] == {"from": "ap-office"}


async def test_events_filter_by_keys(rich_app) -> None:
    async with await _client(rich_app) as c:
        hit = await c.get("/api/events", params={"keys": "EVT_WU_Roam"})
        miss = await c.get("/api/events", params={"keys": "EVT_NOPE,EVT_OTHER"})
    assert hit.json()["count"] == 1
    assert miss.json()["count"] == 0


async def test_events_filter_by_entity_and_since(rich_app) -> None:
    client_id = rich_app.state.store.client_id
    async with await _client(rich_app) as c:
        by_entity = await c.get("/api/events", params={"entity_id": client_id})
        future = await c.get("/api/events", params={"since_ts": BASE_TS + 10_000})
    assert by_entity.json()["count"] == 1
    assert future.json()["count"] == 0


async def test_events_503_when_store_absent(settings) -> None:
    from netadmin.server.main import DaemonComponents, create_app

    app = create_app(settings=settings, store=None, components=DaemonComponents())
    async with await _client(app) as c:
        resp = await c.get("/api/events")
    assert resp.status_code == 503
