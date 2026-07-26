"""Tests for the extended issues surface: entity filter, names, confounders, ack/snooze."""

from __future__ import annotations

import httpx
import pytest

from ..conftest import BASE_TS

pytestmark = pytest.mark.asyncio


async def _client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _active_issue_id(c: httpx.AsyncClient) -> int:
    listing = (await c.get("/api/issues", params={"severity": "p2"})).json()
    return listing["issues"][0]["id"]


async def test_list_filters_by_entity_and_resolves_names(rich_app) -> None:
    ap_id = rich_app.state.store.ap_id
    async with await _client(rich_app) as c:
        resp = await c.get("/api/issues", params={"entity_id": ap_id})
    body = resp.json()
    # both the active and the resolved AP issues, each with the owner resolved
    fps = {i["fingerprint"] for i in body["issues"]}
    assert fps == {"fp-active", "fp-resolved"}
    assert all(i["entity"]["name"] == "ap-office" for i in body["issues"])


async def test_detail_surfaces_confounders_and_entity(rich_app) -> None:
    async with await _client(rich_app) as c:
        issue_id = await _active_issue_id(c)
        resp = await c.get(f"/api/issues/{issue_id}")
    body = resp.json()
    assert body["confounders"] == ["poe_flap", "duplex_mismatch"]
    assert body["entity"]["name"] == "ap-office"
    assert [e["kind"] for e in body["events"]] == ["detected", "escalated"]


async def test_ack_sets_flag_and_writes_event(rich_app) -> None:
    async with await _client(rich_app) as c:
        issue_id = await _active_issue_id(c)
        ack = await c.post(f"/api/issues/{issue_id}/ack")
        assert ack.status_code == 200
        assert ack.json()["issue"]["ack_ts"] is not None
        detail = (await c.get(f"/api/issues/{issue_id}")).json()
    assert "acked" in [e["kind"] for e in detail["events"]]


async def test_snooze_sets_until_and_writes_event(rich_app) -> None:
    until = BASE_TS + 999_999
    async with await _client(rich_app) as c:
        issue_id = await _active_issue_id(c)
        snooze = await c.post(f"/api/issues/{issue_id}/snooze", json={"until_ts": until})
        assert snooze.status_code == 200
        assert snooze.json()["issue"]["snooze_until_ts"] == until
        detail = (await c.get(f"/api/issues/{issue_id}")).json()
    snoozed = [e for e in detail["events"] if e["kind"] == "snoozed"]
    assert snoozed and snoozed[0]["detail"]["until_ts"] == until


async def test_snooze_requires_until_ts(rich_app) -> None:
    async with await _client(rich_app) as c:
        issue_id = await _active_issue_id(c)
        resp = await c.post(f"/api/issues/{issue_id}/snooze", json={})
    assert resp.status_code == 422


async def test_ack_and_snooze_unknown_issue_404(rich_app) -> None:
    async with await _client(rich_app) as c:
        ack = await c.post("/api/issues/999999/ack")
        snooze = await c.post("/api/issues/999999/snooze", json={"until_ts": 1})
    assert ack.status_code == 404
    assert snooze.status_code == 404
