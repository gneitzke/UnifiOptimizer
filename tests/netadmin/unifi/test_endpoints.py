"""Endpoint wrappers: fixture-replay parsing + request-shape assertions."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from netadmin.ingest.unifi.client import UnifiClient
from netadmin.ingest.unifi.endpoints import EVENT_PAGE_CAP, Endpoints

from .conftest import load_fixture

pytestmark = pytest.mark.asyncio

HOST = "https://ctrl.test"
SITE = "default"
OS_PROBE = f"{HOST}/proxy/network/"
OS_LOGIN = f"{HOST}/api/auth/login"
API = f"{HOST}/proxy/network/api/s/{SITE}"


def _mock_login() -> None:
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    respx.post(OS_LOGIN).mock(
        return_value=httpx.Response(200, headers={"X-CSRF-Token": "c"}, json={})
    )


async def _endpoints() -> tuple[UnifiClient, Endpoints]:
    client = UnifiClient(host=HOST, site=SITE, username="u", password="p", min_request_interval=0.0)
    await client.connect()
    return client, Endpoints(client)


# --------------------------------------------------------------------------- #
# Fixture replay: recorded controller payloads parse into models
# --------------------------------------------------------------------------- #
@respx.mock
async def test_stat_device_parses_recorded_fixture():
    _mock_login()
    respx.get(f"{API}/stat/device").mock(
        return_value=httpx.Response(200, json=load_fixture("stat_device.json"))
    )
    client, ep = await _endpoints()
    devices = await ep.stat_device()
    assert devices
    switch = next(d for d in devices if d.type == "usw" and d.port_table)
    assert switch.port_table[0].port_idx is not None
    ap = next(d for d in devices if d.radio_table_stats)
    assert any(r.cu_total is not None for r in ap.radio_table_stats)
    assert any(d.uplink is not None for d in devices)
    await client.aclose()


@respx.mock
async def test_sfp_and_poe_fields_from_synthetic_fixture():
    _mock_login()
    respx.get(f"{API}/stat/device").mock(
        return_value=httpx.Response(200, json=load_fixture("device_with_sfp.json"))
    )
    client, ep = await _endpoints()
    dev = (await ep.stat_device())[0]
    sfp_port = next(p for p in dev.port_table if p.sfp_found)
    assert sfp_port.sfp_rxpower == -4.7
    assert sfp_port.sfp_txfault is False
    assert sfp_port.autoneg is False
    poe_port = next(p for p in dev.port_table if p.poe_power)
    assert poe_port.poe_power == 7.4
    assert poe_port.full_duplex is False  # duplex-mismatch signal
    await client.aclose()


@respx.mock
async def test_stat_sta_and_health_parse():
    _mock_login()
    respx.get(f"{API}/stat/sta").mock(
        return_value=httpx.Response(200, json=load_fixture("stat_sta.json"))
    )
    respx.get(f"{API}/stat/health").mock(
        return_value=httpx.Response(200, json=load_fixture("stat_health.json"))
    )
    client, ep = await _endpoints()
    clients = await ep.stat_sta()
    assert clients
    assert any(c.rssi is not None for c in clients if not c.is_wired)
    health = await ep.stat_health()
    assert {h.subsystem for h in health} >= {"wan", "wlan"}
    await client.aclose()


@respx.mock
async def test_stat_report_parses_and_sends_ms_window():
    _mock_login()
    route = respx.post(f"{API}/stat/report/hourly.ap").mock(
        return_value=httpx.Response(200, json=load_fixture("stat_report_hourly_ap.json"))
    )
    client, ep = await _endpoints()
    rows = await ep.stat_report_hourly("ap", start_ms=1_000, end_ms=2_000, attrs=["bytes"])
    assert rows
    body = json.loads(route.calls.last.request.content)
    assert body["start"] == 1_000 and body["end"] == 2_000
    assert "time" in body["attrs"]  # auto-appended
    assert "bytes" in body["attrs"]
    await client.aclose()


# --------------------------------------------------------------------------- #
# Request-shape / paging behavior
# --------------------------------------------------------------------------- #
@respx.mock
async def test_stat_event_pages_with_start_and_cap():
    # GET is the primary method (UniFi OS serves stat/event over GET); paging
    # params ride the query string.
    _mock_login()
    full = [{"key": "EVT_X", "_id": f"id{i}"} for i in range(EVENT_PAGE_CAP)]
    tail = [{"key": "EVT_Y", "_id": "last"}]
    route = respx.get(f"{API}/stat/event").mock(
        side_effect=[
            httpx.Response(200, json={"data": full}),
            httpx.Response(200, json={"data": tail}),
        ]
    )
    client, ep = await _endpoints()
    events = await ep.stat_event(within_hours=24)
    assert len(events) == EVENT_PAGE_CAP + 1
    assert route.call_count == 2
    first = route.calls[0].request.url.params
    second = route.calls[1].request.url.params
    assert first["_start"] == "0" and first["_limit"] == str(EVENT_PAGE_CAP)
    assert first["within"] == "24"
    assert second["_start"] == str(EVENT_PAGE_CAP)  # paged forward
    await client.aclose()


@respx.mock
async def test_stat_event_respects_max_events():
    _mock_login()
    full = [{"key": "EVT_X", "_id": f"id{i}"} for i in range(EVENT_PAGE_CAP)]
    respx.get(f"{API}/stat/event").mock(return_value=httpx.Response(200, json={"data": full}))
    client, ep = await _endpoints()
    events = await ep.stat_event(max_events=10)
    assert len(events) == 10
    await client.aclose()


@respx.mock
async def test_stat_event_parses_synthetic_event_fixture():
    _mock_login()
    respx.get(f"{API}/stat/event").mock(
        return_value=httpx.Response(200, json=load_fixture("stat_event.json"))
    )
    client, ep = await _endpoints()
    events = await ep.stat_event()
    keys = {e.key for e in events}
    assert {"EVT_WU_Roam", "EVT_SW_PoeOverload", "EVT_AP_RadarDetected"} <= keys
    await client.aclose()


@respx.mock
async def test_stat_event_falls_back_to_post_on_get_404():
    # UniFi OS consoles answer GET; older controllers only accept POST. When GET
    # 404s (api.err.NotFound), the wrapper falls back to the documented POST
    # read-query. Regression guard for the live CloudKey Gen2 finding.
    _mock_login()
    get_route = respx.get(f"{API}/stat/event").mock(
        return_value=httpx.Response(
            404, json={"meta": {"rc": "error", "msg": "api.err.NotFound"}, "data": []}
        )
    )
    post_route = respx.post(f"{API}/stat/event").mock(
        return_value=httpx.Response(200, json={"data": [{"key": "EVT_Z", "_id": "z"}]})
    )
    client, ep = await _endpoints()
    events = await ep.stat_event(within_hours=24)
    assert [e.key for e in events] == ["EVT_Z"]
    assert get_route.called and post_route.called
    body = json.loads(post_route.calls.last.request.content)
    assert body["_start"] == 0 and body["within"] == 24
    await client.aclose()


_NOTFOUND = {"meta": {"rc": "error", "msg": "api.err.NotFound"}, "data": []}
_INVALID = {"meta": {"rc": "error", "msg": "api.err.InvalidObject"}, "data": []}


@respx.mock
async def test_stat_event_falls_through_to_list_event_when_stat_absent():
    # LIVE-VALIDATED QUIRK: this UniFi OS console removed stat/event (hard 404
    # api.err.NotFound for GET *and* POST); the surviving route is list/event.
    # The wrapper must fall through to it and then stick to it.
    _mock_login()
    get_se = respx.get(f"{API}/stat/event").mock(return_value=httpx.Response(404, json=_NOTFOUND))
    post_se = respx.post(f"{API}/stat/event").mock(return_value=httpx.Response(404, json=_NOTFOUND))
    le = respx.get(f"{API}/list/event").mock(
        return_value=httpx.Response(200, json={"data": [{"key": "EVT_LE", "_id": "le1"}]})
    )
    client, ep = await _endpoints()
    events = await ep.stat_event(within_hours=24)
    assert [e.key for e in events] == ["EVT_LE"]
    assert get_se.called and post_se.called and le.called

    # Sticky: the discovered endpoint is reused; stat/event is not re-probed.
    se_calls = get_se.call_count + post_se.call_count
    events2 = await ep.stat_event(within_hours=24)
    assert [e.key for e in events2] == ["EVT_LE"]
    assert get_se.call_count + post_se.call_count == se_calls  # no re-probe
    assert le.call_count == 2
    await client.aclose()


@respx.mock
async def test_stat_event_all_endpoints_absent_degrades_to_empty():
    # No usable event endpoint (stat/event 404, list/event 400 InvalidObject):
    # catch-up must degrade to [] (WS remains the source), log once, and NOT
    # re-hit the controller on subsequent calls -- the fix for the daemon bug
    # where events_catchup threw every poll cycle.
    _mock_login()
    get_se = respx.get(f"{API}/stat/event").mock(return_value=httpx.Response(404, json=_NOTFOUND))
    post_se = respx.post(f"{API}/stat/event").mock(return_value=httpx.Response(404, json=_NOTFOUND))
    get_le = respx.get(f"{API}/list/event").mock(return_value=httpx.Response(400, json=_INVALID))
    post_le = respx.post(f"{API}/list/event").mock(return_value=httpx.Response(400, json=_INVALID))
    client, ep = await _endpoints()

    assert await ep.stat_event(within_hours=2) == []
    total = sum(r.call_count for r in (get_se, post_se, get_le, post_le))
    assert total >= 2  # probed both candidates

    assert await ep.stat_event(within_hours=2) == []  # short-circuits, no HTTP
    assert sum(r.call_count for r in (get_se, post_se, get_le, post_le)) == total
    await client.aclose()


@respx.mock
async def test_stat_session_uses_seconds():
    _mock_login()
    route = respx.post(f"{API}/stat/session").mock(
        return_value=httpx.Response(200, json={"data": [{"mac": "02:00:00:00:00:01"}]})
    )
    client, ep = await _endpoints()
    sessions = await ep.stat_session(
        "02:00:00:00:00:01", start_s=1_600_000_000, end_s=1_600_003_600
    )
    assert sessions
    body = json.loads(route.calls.last.request.content)
    assert body["start"] == 1_600_000_000  # seconds, not ms
    assert body["mac"] == "02:00:00:00:00:01"
    assert body["type"] == "all"
    await client.aclose()


@respx.mock
async def test_stat_rogueap_within_and_alarm():
    _mock_login()
    rogue = respx.post(f"{API}/stat/rogueap").mock(
        return_value=httpx.Response(200, json={"data": [{"bssid": "02:00:00:00:00:aa"}]})
    )
    respx.post(f"{API}/list/alarm").mock(
        return_value=httpx.Response(200, json={"data": [{"key": "EVT_A", "archived": False}]})
    )
    client, ep = await _endpoints()
    assert await ep.stat_rogueap(within_hours=48)
    assert json.loads(rogue.calls.last.request.content)["within"] == 48
    alarms = await ep.list_alarm()
    assert alarms[0].key == "EVT_A"
    await client.aclose()


@respx.mock
async def test_rest_wlanconf_is_a_get():
    _mock_login()
    route = respx.get(f"{API}/rest/wlanconf").mock(
        return_value=httpx.Response(
            200, json={"data": [{"_id": "w1", "name": "HomeNet", "security": "wpapsk"}]}
        )
    )
    client, ep = await _endpoints()
    wlans = await ep.rest_wlanconf()
    assert [w.name for w in wlans] == ["HomeNet"]
    assert wlans[0].security == "wpapsk"
    assert route.calls.last.request.method == "GET"  # a read, never a write
    await client.aclose()


@respx.mock
async def test_rest_wlanconf_absent_route_degrades_to_empty():
    _mock_login()
    respx.get(f"{API}/rest/wlanconf").mock(
        return_value=httpx.Response(404, json={"meta": {"msg": "api.err.NotFound"}})
    )
    client, ep = await _endpoints()
    assert await ep.rest_wlanconf() == []  # absent route is data, not a crash
    await client.aclose()


@respx.mock
async def test_report_validates_interval_and_scope():
    _mock_login()
    client, ep = await _endpoints()
    with pytest.raises(ValueError):
        await ep.stat_report("weekly", "ap", start_ms=1, end_ms=2)
    with pytest.raises(ValueError):
        await ep.stat_report("hourly", "bogus", start_ms=1, end_ms=2)
    await client.aclose()
