"""ASGI-transport tests for the inventory router (devices + clients)."""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def _client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_list_devices_rolls_up_state_metrics_and_issues(rich_app) -> None:
    async with await _client(rich_app) as c:
        resp = await c.get("/api/inventory/devices")
    assert resp.status_code == 200
    body = resp.json()
    # ap + switch + gateway (clients are their own surface)
    assert body["count"] == 3
    by_name = {d["name"]: d for d in body["devices"]}
    assert set(by_name) == {"ap-office", "sw-core", "gw"}

    ap = by_name["ap-office"]
    # current firmware is the latest state-change value, not the first
    assert ap["state"]["firmware"] == "6.6.77"
    # current metric value present (latest cpu reading = 10 + 11)
    cpu = next(m for m in ap["metrics"] if m["metric"] == "cpu")
    assert cpu["value"] == 21.0
    # one open issue on the AP (the resolved one is not counted)
    assert ap["issue_counts"]["total"] == 1
    assert ap["issue_counts"]["p2"] == 1


async def test_device_detail_has_children_history_and_issues(rich_app) -> None:
    ap_id = rich_app.state.store.ap_id
    async with await _client(rich_app) as c:
        resp = await c.get(f"/api/inventory/devices/{ap_id}")
    assert resp.status_code == 200
    dev = resp.json()["device"]
    # both radios show up as children
    assert len(dev["children"]) == 2
    assert all(ch["type"] == "radio" for ch in dev["children"])
    # firmware history: two firmware rows + a state row
    attrs = {h["attr"] for h in dev["state_changes"]}
    assert {"firmware", "state"} <= attrs
    # open vs resolved split
    open_fps = {i["fingerprint"] for i in dev["issues_open"]}
    resolved_fps = {i["fingerprint"] for i in dev["issues_resolved"]}
    assert "fp-active" in open_fps
    assert "fp-resolved" in resolved_fps


async def test_device_detail_404_for_client_id(rich_app) -> None:
    client_id = rich_app.state.store.client_id
    async with await _client(rich_app) as c:
        resp = await c.get(f"/api/inventory/devices/{client_id}")
    assert resp.status_code == 404


async def test_list_clients(rich_app) -> None:
    async with await _client(rich_app) as c:
        resp = await c.get("/api/inventory/clients")
    body = resp.json()
    assert body["count"] == 2
    names = {cl["name"] for cl in body["clients"]}
    assert names == {"iPhone", "Desktop"}


async def test_client_detail_has_journey_and_current_ap(rich_app) -> None:
    client_id = rich_app.state.store.client_id
    async with await _client(rich_app) as c:
        resp = await c.get(f"/api/inventory/clients/{client_id}")
    assert resp.status_code == 200
    cl = resp.json()["client"]
    assert cl["current_ap"]["name"] == "ap-office"
    keys = {e["key"] for e in cl["journey"]}
    assert "EVT_WU_Roam" in keys
    roam = next(e for e in cl["journey"] if e["key"] == "EVT_WU_Roam")
    assert roam["related_entity"]["name"] == "ap-office"
    assert roam["data"] == {"from": "ap-office"}
    assert {i["fingerprint"] for i in cl["issues_open"]} == {"fp-client"}


async def test_client_detail_404_for_device_id(rich_app) -> None:
    ap_id = rich_app.state.store.ap_id
    async with await _client(rich_app) as c:
        resp = await c.get(f"/api/inventory/clients/{ap_id}")
    assert resp.status_code == 404


async def test_inventory_503_when_store_absent(settings) -> None:
    from netadmin.server.main import DaemonComponents, create_app

    app = create_app(settings=settings, store=None, components=DaemonComponents())
    async with await _client(app) as c:
        resp = await c.get("/api/inventory/devices")
    assert resp.status_code == 503
