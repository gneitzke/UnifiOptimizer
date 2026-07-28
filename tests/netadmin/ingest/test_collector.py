"""Collector tests: firewall, inventory/metric writes, counter deltas, scheduler.

The controller is faked (:class:`FakeEndpoints`); the repository is a real
migrated SQLite temp DB, so these exercise the mapping -> repository seam and the
delta/rollup machinery end to end without a network.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from typing import Optional

import pytest

from netadmin.domain.types import EntityType
from netadmin.ingest.collector import (
    JOB_FAST_DEVICE,
    JOB_FAST_HEALTH,
    Collector,
    CollectorStatus,
    build_scheduler,
)
from netadmin.ingest.unifi.models import Alarm, Anomaly, Device, HealthSubsystem, RogueAp, Wlan

from .conftest import make_device, make_health

pytestmark = pytest.mark.asyncio


class FakeEndpoints:
    """Stand-in for :class:`netadmin.ingest.unifi.endpoints.Endpoints`."""

    def __init__(
        self,
        *,
        devices=None,
        clients=None,
        health=None,
        rogues=None,
        wlans=None,
        alarms=None,
        anomalies=None,
    ):
        self.devices = devices or []
        self.clients = clients or []
        self.health = health or []
        self.rogues = rogues or []
        self.wlans = wlans or []
        self.alarms = alarms or []
        self.anomalies = anomalies or []
        self.device_error: Optional[Exception] = None
        self.calls: dict[str, int] = {
            "device": 0,
            "sta": 0,
            "health": 0,
            "rogueap": 0,
            "wlanconf": 0,
            "alarm": 0,
            "anomalies": 0,
        }

    async def stat_device(self) -> list[Device]:
        self.calls["device"] += 1
        if self.device_error is not None:
            raise self.device_error
        return list(self.devices)

    async def stat_sta(self):
        self.calls["sta"] += 1
        return list(self.clients)

    async def stat_health(self) -> list[HealthSubsystem]:
        self.calls["health"] += 1
        return list(self.health)

    async def stat_rogueap(self, *, within_hours: int = 24) -> list[RogueAp]:
        self.calls["rogueap"] += 1
        return list(self.rogues)

    async def rest_wlanconf(self) -> list[Wlan]:
        self.calls["wlanconf"] += 1
        return list(self.wlans)

    async def list_alarm(self, *, archived: bool = False) -> list[Alarm]:
        self.calls["alarm"] += 1
        return list(self.alarms)

    async def stat_anomalies(self, **_kw) -> list[Anomaly]:
        self.calls["anomalies"] += 1
        return list(self.anomalies)


def _clock(start: int = 1_000_000, step: int = 60):
    counter = itertools.count(start, step)
    return lambda: next(counter)


def _port_device(mac: str, rx_errors: int) -> Device:
    return make_device(
        mac=mac,
        type="usw",
        model="US8",
        name="sw-test",
        version="6.6.65",
        state=1,
        port_table=[
            {
                "port_idx": 1,
                "media": "GE",
                "up": True,
                "enable": True,
                "speed": 1000,
                "full_duplex": True,
                "rx_errors": rx_errors,
                "poe_power": 4.5,
            }
        ],
    )


# --------------------------------------------------------------------------- #
# CollectorStatus
# --------------------------------------------------------------------------- #
async def test_status_counts_consecutive_failures_and_resets():
    st = CollectorStatus()
    st.record("j", False, ts=1, error="boom")
    st.record("j", False, ts=2, error="boom")
    assert st.consecutive_failures("j") == 2
    st.record("j", True, ts=3)
    assert st.consecutive_failures("j") == 0
    assert st.last_ok_ts["j"] == 3


async def test_status_worst_across_jobs():
    st = CollectorStatus()
    st.record("a", False, ts=1, error="x")
    st.record("b", False, ts=1, error="x")
    st.record("b", False, ts=2, error="x")
    assert st.consecutive_failures() == 2


# --------------------------------------------------------------------------- #
# firewall
# --------------------------------------------------------------------------- #
async def test_firewall_isolates_crash_and_next_cycle_runs(repo):
    ep = FakeEndpoints(devices=[_port_device("aa:bb:cc:00:00:01", 10)])
    ep.device_error = RuntimeError("controller unreachable")
    col = Collector(ep, repo, clock=_clock())

    # Crashing cycle: must not raise, must record a failed poll_run.
    ok = await col.fast_device()
    assert ok is False
    assert col.status.consecutive_failures(JOB_FAST_DEVICE) == 1
    runs = repo.read_poll_runs(JOB_FAST_DEVICE, 0, 9_999_999)
    assert len(runs) == 1 and runs[0]["ok"] == 0
    assert runs[0]["error"]

    # Next cycle succeeds: the scheduler was never killed and the counter resets.
    ep.device_error = None
    ok = await col.fast_device()
    assert ok is True
    assert col.status.consecutive_failures(JOB_FAST_DEVICE) == 0
    runs = repo.read_poll_runs(JOB_FAST_DEVICE, 0, 9_999_999)
    assert [r["ok"] for r in runs] == [0, 1]


async def test_placeholder_jobs_record_ok(repo):
    col = Collector(FakeEndpoints(), repo, clock=_clock())
    assert await col.events_catchup() is True
    assert await col.reports_5min() is True


async def test_injected_catchup_and_reports_are_invoked(repo):
    calls: list[str] = []

    async def catch(ts: int) -> None:
        calls.append(f"events:{ts}")

    async def reps(ts: int) -> None:
        calls.append(f"reports:{ts}")

    col = Collector(
        FakeEndpoints(), repo, clock=_clock(), event_catchup=catch, reports_backfill=reps
    )
    assert await col.events_catchup() is True
    assert await col.reports_5min() is True
    assert [c.split(":")[0] for c in calls] == ["events", "reports"]


async def test_injected_callable_failure_is_firewalled(repo):
    async def boom(ts: int) -> None:
        raise RuntimeError("catchup exploded")

    col = Collector(FakeEndpoints(), repo, clock=_clock(), event_catchup=boom)
    assert await col.events_catchup() is False
    runs = repo.read_poll_runs("events_catchup", 0, 9_999_999)
    assert runs and runs[0]["ok"] == 0 and runs[0]["error"]


# --------------------------------------------------------------------------- #
# device job: inventory + metrics
# --------------------------------------------------------------------------- #
async def test_fast_device_writes_inventory_and_gauge_samples(repo, sfp_devices):
    ep = FakeEndpoints(devices=sfp_devices)
    col = Collector(ep, repo, clock=_clock())
    assert await col.fast_device() is True

    switch = repo.find_entity(EntityType.SWITCH, "02:00:11:22:33:0a")
    assert switch is not None
    ports = repo.list_entities(EntityType.PORT)
    assert len(ports) == 2
    # Parentage resolved to the switch's id.
    assert all(p["parent_id"] == switch["entity_id"] for p in ports)

    # A gauge (poe_power) is written on the very first poll; counters only seed.
    port2 = repo.find_entity(EntityType.PORT, "02:00:11:22:33:0a:2")
    poe_series = repo.get_series(int(port2["entity_id"]), "poe_power")
    assert poe_series is not None
    assert repo.read_raw(poe_series, 0, 9_999_999)  # has a row

    # State history recorded firmware for the switch.
    assert repo.current_state(int(switch["entity_id"]), "firmware") == "6.6.65"


async def test_counter_deltas_across_two_device_polls(repo):
    ep = FakeEndpoints(devices=[_port_device("aa:bb:cc:00:00:02", 100)])
    col = Collector(ep, repo, clock=_clock(step=60))

    await col.fast_device()  # seeds the rx_errors counter, writes no row
    port = repo.find_entity(EntityType.PORT, "aa:bb:cc:00:00:02:1")
    series = repo.get_series(int(port["entity_id"]), "rx_errors")
    assert repo.read_raw(series, 0, 9_999_999) == []  # first counter reading seeds only

    ep.devices = [_port_device("aa:bb:cc:00:00:02", 175)]
    await col.fast_device()  # delta = 175 - 100 = 75
    rows = repo.read_raw(series, 0, 9_999_999)
    assert len(rows) == 1
    assert rows[0]["value"] == 75.0


# --------------------------------------------------------------------------- #
# client job
# --------------------------------------------------------------------------- #
async def test_fast_sta_links_client_to_existing_ap(repo):
    ap = make_device(mac="aa:bb:cc:00:00:0a", type="uap", model="U6", state=1)
    dev_ep = FakeEndpoints(devices=[ap])
    await Collector(dev_ep, repo, clock=_clock()).fast_device()

    from .conftest import make_client

    client = make_client(
        mac="aa:bb:cc:00:00:c1",
        ap_mac="aa:bb:cc:00:00:0a",
        is_wired=False,
        signal=-70,
        noise=-95,
    )
    sta_ep = FakeEndpoints(clients=[client])
    col = Collector(sta_ep, repo, clock=_clock())
    assert await col.fast_sta() is True

    ap_row = repo.find_entity(EntityType.AP, "aa:bb:cc:00:00:0a")
    client_row = repo.find_entity(EntityType.CLIENT, "aa:bb:cc:00:00:c1")
    assert client_row["parent_id"] == ap_row["entity_id"]
    rssi_series = repo.get_series(int(client_row["entity_id"]), "rssi")
    assert repo.read_raw(rssi_series, 0, 9_999_999)[0]["value"] == -70.0


# --------------------------------------------------------------------------- #
# health job
# --------------------------------------------------------------------------- #
async def test_fast_health_all_unknown_writes_no_samples(repo, health_subsystems):
    ep = FakeEndpoints(health=health_subsystems)
    col = Collector(ep, repo, clock=_clock())
    assert await col.fast_health() is True
    # No gateway, no series created.
    assert repo.list_entities(EntityType.GATEWAY) == []


async def test_fast_health_creates_gateway_and_wan_samples(repo):
    subs = [make_health(subsystem="wan", status="ok", gw_mac="aa:bb:cc:00:00:g1", latency=15)]
    ep = FakeEndpoints(health=subs)
    col = Collector(ep, repo, clock=_clock())
    assert await col.fast_health() is True

    gw = repo.find_entity(EntityType.GATEWAY, "aa:bb:cc:00:00:g1")
    assert gw is not None
    series = repo.get_series(int(gw["entity_id"]), "wan_latency")
    assert repo.read_raw(series, 0, 9_999_999)[0]["value"] == 15.0


# --------------------------------------------------------------------------- #
# read-set jobs: rogue BSS inventory, alarms, anomalies (ARCHITECTURE.md 5.1)
# --------------------------------------------------------------------------- #
async def test_rogueap_upserts_bss_inventory_and_refreshes(repo):
    rogues = [
        RogueAp(
            bssid="02:00:00:00:00:aa",
            essid="Neighbor",
            channel=6,
            rssi=-60,
            band="ng",
            is_rogue=False,
            is_ubnt=True,
            ap_mac="02:00:00:00:00:01",
        )
    ]
    ep = FakeEndpoints(rogues=rogues)
    col = Collector(ep, repo, clock=_clock())
    assert await col.rogueap() is True

    rows = repo.list_entities("rogue_bss")
    assert len(rows) == 1
    assert rows[0]["native_id"] == "02:00:00:00:00:aa"
    assert rows[0]["name"] == "Neighbor"
    meta = json.loads(rows[0]["meta"])
    assert (
        meta["channel"] == 6 and meta["rssi"] == -60 and meta["seen_by_ap"] == "02:00:00:00:00:01"
    )
    # Own-hardware flag captured; the per-scan sighting log starts with this scan.
    assert meta["is_ubnt"] is True
    assert isinstance(meta["scan_ts"], list) and len(meta["scan_ts"]) == 1

    # A second poll of the same BSSID refreshes, never duplicates, and appends the
    # new scan to the sighting log (persistence = distinct recent scans).
    assert await col.rogueap() is True
    assert len(repo.list_entities("rogue_bss")) == 1
    meta2 = json.loads(repo.list_entities("rogue_bss")[0]["meta"])
    assert len(meta2["scan_ts"]) == 2 and meta2["scan_ts"] == sorted(meta2["scan_ts"])


async def test_rogueap_logs_distinct_channels_for_a_hopping_neighbour(repo):
    """Channel left the fingerprint, so the channels a BSS hops across are evidence."""
    rogue = RogueAp(bssid="02:00:00:00:00:bb", essid="Hopper", channel=36, rssi=-60, band="na")
    ep = FakeEndpoints(rogues=[rogue])
    col = Collector(ep, repo, clock=_clock())
    assert await col.rogueap() is True
    ep.rogues = [rogue.model_copy(update={"channel": 40})]
    assert await col.rogueap() is True

    meta = json.loads(repo.list_entities("rogue_bss")[0]["meta"])
    assert meta["channel"] == 40  # latest
    assert meta["channels"] == [36, 40]  # distinct, most recent last


async def test_wlanconf_upserts_our_ssids(repo):
    wlans = [
        Wlan(_id="w1", name="HomeNet", enabled=True, security="wpapsk"),
        Wlan(_id="w2", name="Guest", enabled=False, security="open"),
    ]
    ep = FakeEndpoints(wlans=wlans)
    col = Collector(ep, repo, clock=_clock())
    assert await col.wlanconf() is True

    rows = sorted(repo.list_entities(EntityType.WLAN), key=lambda r: r["native_id"])
    assert [r["name"] for r in rows] == ["HomeNet", "Guest"]
    assert json.loads(rows[0]["meta"])["security"] == "wpapsk"
    assert json.loads(rows[1]["meta"])["enabled"] is False

    # A second poll refreshes in place; the SSID set never duplicates.
    assert await col.wlanconf() is True
    assert len(repo.list_entities(EntityType.WLAN)) == 2


async def test_wlanconf_absent_route_leaves_inventory_untouched(repo):
    """A console that serves no WLAN config is data, not a failure."""
    col = Collector(FakeEndpoints(wlans=[]), repo, clock=_clock())
    assert await col.wlanconf() is True
    assert repo.list_entities(EntityType.WLAN) == []


async def test_rogueap_skips_rows_without_bssid(repo):
    ep = FakeEndpoints(rogues=[RogueAp(essid="no-bssid")])
    col = Collector(ep, repo, clock=_clock())
    assert await col.rogueap() is True
    assert repo.list_entities("rogue_bss") == []


async def test_alarms_recorded_as_events_and_deduped(repo):
    alarms = [Alarm(_id="al1", key="EVT_IPS_IpsAlert", time=1_000_000_000_000, msg="alert")]
    ep = FakeEndpoints(alarms=alarms)
    col = Collector(ep, repo, clock=_clock())
    assert await col.alarms() is True

    evs = repo.read_events(0, 2_000_000_000)
    assert len(evs) == 1
    assert evs[0]["key"] == "EVT_IPS_IpsAlert"
    assert evs[0]["ts"] == 1_000_000_000  # ms folded to seconds
    assert evs[0]["native_id"] == "a:al1"

    # Re-poll of the same open alarm is deduped on native id.
    assert await col.alarms() is True
    assert len(repo.read_events(0, 2_000_000_000)) == 1


async def test_alarms_job_ok_when_console_has_no_alarm_route(repo):
    # The real Endpoints wrapper over a console that rejects every list/alarm
    # body: the job must record nothing and still report ok. One dead read route
    # holding /api/health at degraded forever is what costs the banner its
    # credibility -- a failing job should mean something is actually wrong.
    from netadmin.ingest.unifi.auth import UnifiError
    from netadmin.ingest.unifi.endpoints import Endpoints

    class NoAlarmRouteClient:
        """Minimal client stand-in: list/alarm answers 400 api.err.InvalidObject."""

        def __init__(self):
            self.calls = 0

        async def post_data(self, endpoint, body=None):
            self.calls += 1
            raise UnifiError(
                f'{endpoint} -> 400: {{"meta":{{"rc":"error","msg":"api.err.InvalidObject"}}}}'
            )

    fake_client = NoAlarmRouteClient()
    col = Collector(Endpoints(fake_client), repo, clock=_clock())

    assert await col.alarms() is True
    assert repo.read_events(0, 2_000_000_000) == []

    assert await col.alarms() is True
    assert fake_client.calls == 1  # latched off, controller not re-asked


async def test_alarms_without_id_hash_dedupes_distinct(repo):
    alarms = [
        Alarm(key="EVT_LAN_Loop", time=1_000_000_000_000, msg="loop A"),
        Alarm(key="EVT_LAN_Loop", time=1_000_000_000_000, msg="loop B"),
    ]
    col = Collector(FakeEndpoints(alarms=alarms), repo, clock=_clock())
    assert await col.alarms() is True
    # Distinct msg -> distinct hash -> both stored (no same-second collision).
    assert len(repo.read_events(0, 2_000_000_000)) == 2


async def test_anomalies_resolve_client_and_dedupe(repo):
    ap = make_device(mac="aa:bb:cc:00:00:0a", type="uap", model="U6", state=1)
    await Collector(FakeEndpoints(devices=[ap]), repo, clock=_clock()).fast_device()
    from .conftest import make_client

    client = make_client(
        mac="aa:bb:cc:00:00:c1", ap_mac="aa:bb:cc:00:00:0a", is_wired=False, signal=-70, noise=-95
    )
    await Collector(FakeEndpoints(clients=[client]), repo, clock=_clock()).fast_sta()

    anomalies = [Anomaly(mac="aa:bb:cc:00:00:c1", anomaly="dns_slow", start_time=1_000_000_000_000)]
    col = Collector(FakeEndpoints(anomalies=anomalies), repo, clock=_clock())
    assert await col.anomalies() is True

    client_row = repo.find_entity(EntityType.CLIENT, "aa:bb:cc:00:00:c1")
    evs = repo.read_events(0, 2_000_000_000, entity_id=int(client_row["entity_id"]))
    assert len(evs) == 1
    assert evs[0]["key"] == "ANOMALY_DNS_SLOW"
    assert evs[0]["ts"] == 1_000_000_000

    assert await col.anomalies() is True  # deduped on (ts, mac, type)
    assert len(repo.read_events(0, 2_000_000_000, entity_id=int(client_row["entity_id"]))) == 1


async def test_readset_jobs_are_firewalled(repo):
    ep = FakeEndpoints()

    async def boom():
        raise RuntimeError("rogueap exploded")

    ep.stat_rogueap = lambda **_k: boom()  # type: ignore[assignment]
    col = Collector(ep, repo, clock=_clock())
    assert await col.rogueap() is False
    runs = repo.read_poll_runs("rogueap", 0, 9_999_999)
    assert runs and runs[0]["ok"] == 0 and runs[0]["error"]


# --------------------------------------------------------------------------- #
# scheduler wiring
# --------------------------------------------------------------------------- #
class _Poll:
    device_s = 60
    sta_s = 60
    health_s = 60
    event_catchup_s = 300
    report_5min_s = 21_600


async def test_build_scheduler_wires_all_jobs_max_instance_one(repo):
    col = Collector(FakeEndpoints(), repo, clock=_clock())
    sched = build_scheduler(col, _Poll())
    try:
        ids = {j.id for j in sched.get_jobs()}
        assert ids == {
            "fast_device",
            "fast_sta",
            "fast_health",
            "events_catchup",
            "reports_5min",
            "rogueap",
            "wlanconf",
            "alarms",
            "anomalies",
        }
        for job in sched.get_jobs():
            assert job.max_instances == 1
            assert job.coalesce is True
        dev = sched.get_job("fast_device")
        assert int(dev.trigger.interval.total_seconds()) == 60
    finally:
        if sched.running:
            sched.shutdown(wait=False)


async def test_scheduler_runs_jobs_on_short_intervals(repo, sfp_devices):
    ep = FakeEndpoints(devices=sfp_devices, health=[])
    col = Collector(ep, repo, clock=_clock())

    class FastPoll:
        device_s = 0.05
        sta_s = 0.05
        health_s = 0.05
        event_catchup_s = 0.05
        report_5min_s = 0.05

    sched = build_scheduler(col, FastPoll(), stagger_s=0.0)
    sched.start()
    try:
        await asyncio.sleep(0.25)
    finally:
        sched.shutdown(wait=False)

    # The device job ran at least once and left a poll_run trail + status.
    runs = repo.read_poll_runs(JOB_FAST_DEVICE, 0, 9_999_999)
    assert runs, "device job never fired"
    assert col.status.total_runs >= 1
    # Health job with no gateway also ran and recorded ok.
    hruns = repo.read_poll_runs(JOB_FAST_HEALTH, 0, 9_999_999)
    assert hruns


# --------------------------------------------------------------------------- #
# Finding: one poll cycle = one transaction (inventory + samples atomic)
# --------------------------------------------------------------------------- #
async def test_device_cycle_is_atomic_inventory_rolls_back_with_samples(repo):
    # A cycle that fails while writing samples must NOT leave inventory committed
    # (the pre-fix per-call BEGIN IMMEDIATE committed each upsert independently).
    ep = FakeEndpoints(devices=[_port_device("aa:bb:cc:00:00:07", 10)])
    col = Collector(ep, repo, clock=_clock())

    def boom(*_a, **_k):
        raise RuntimeError("samples write failed mid-cycle")

    repo.record_samples = boom  # type: ignore[assignment, method-assign]

    ok = await col.fast_device()  # firewalled: records failure, does not raise
    assert ok is False

    # Whole cycle rolled back: no switch, no ports survived the failed samples.
    assert repo.list_entities(EntityType.SWITCH) == []
    assert repo.list_entities(EntityType.PORT) == []
    runs = repo.read_poll_runs(JOB_FAST_DEVICE, 0, 9_999_999)
    assert runs and runs[-1]["ok"] == 0
