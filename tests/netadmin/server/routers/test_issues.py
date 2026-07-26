"""ASGI-transport tests for the read-only issues router."""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def _client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_list_issues_returns_all(app: object) -> None:
    async with await _client(app) as c:
        resp = await c.get("/api/issues")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    fps = {i["fingerprint"] for i in body["issues"]}
    assert fps == {"fp-active", "fp-pending"}
    # evidence is decoded to an object, not left a JSON string
    active = next(i for i in body["issues"] if i["fingerprint"] == "fp-active")
    assert active["evidence"] == {"rx_errors_per_min": 42}


async def test_list_issues_filter_by_state(app: object) -> None:
    async with await _client(app) as c:
        resp = await c.get("/api/issues", params={"state": "active"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["issues"][0]["fingerprint"] == "fp-active"


async def test_list_issues_filter_by_severity(app: object) -> None:
    async with await _client(app) as c:
        resp = await c.get("/api/issues", params={"severity": "p3"})
    body = resp.json()
    assert body["count"] == 1
    assert body["issues"][0]["fingerprint"] == "fp-pending"


async def test_list_issues_invalid_state_is_422(app: object) -> None:
    async with await _client(app) as c:
        resp = await c.get("/api/issues", params={"state": "bogus"})
    assert resp.status_code == 422


async def test_issue_detail_includes_event_trail(app: object) -> None:
    async with await _client(app) as c:
        listing = (await c.get("/api/issues", params={"state": "active"})).json()
        issue_id = listing["issues"][0]["id"]
        resp = await c.get(f"/api/issues/{issue_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["issue"]["fingerprint"] == "fp-active"
    kinds = [e["kind"] for e in body["events"]]
    assert kinds == ["detected", "escalated"]
    # detail JSON decoded to objects and ordered by ts
    assert body["events"][0]["detail"] == {"m": 1}
    assert body["events"][1]["detail"] == {"m": 3}


async def test_issue_detail_unknown_is_404(app: object) -> None:
    async with await _client(app) as c:
        resp = await c.get("/api/issues/999999")
    assert resp.status_code == 404


async def test_issues_503_when_store_absent(settings: object) -> None:
    from netadmin.server.main import DaemonComponents, create_app

    app = create_app(settings=settings, store=None, components=DaemonComponents())
    async with await _client(app) as c:
        resp = await c.get("/api/issues")
    assert resp.status_code == 503
