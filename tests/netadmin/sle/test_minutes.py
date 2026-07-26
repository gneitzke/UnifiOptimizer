"""SLE minute job: the honest-by-construction properties (section 8).

Covers the load-bearing invariants: idle clients contribute zero, every failed
minute lands under exactly one classifier (never double-counted), minutes bucket
correctly across a boundary, and the gateway-less WAN/infra paths no-op or
integrate cleanly.
"""

from __future__ import annotations

from netadmin.sle.classifiers import (
    CLS_BUFFERBLOAT,
    CLS_CLIENT_LOAD,
    CLS_NON_WIFI_UTIL,
    CLS_PINGPONG,
    CLS_SW_DOWN,
    CLS_WAN_DOWN,
    CLS_WEAK_SIGNAL,
    OK,
    SLE_CAPACITY,
    SLE_CONNECT,
    SLE_COVERAGE,
    SLE_INFRA,
    SLE_ROAMING,
    SLE_WAN,
)
from netadmin.sle.minutes import SleMinutesJob, bucket_of
from netadmin.store.repository import Repository
from tests.netadmin.sle.conftest import (
    make_active,
    put,
    rssi,
    seed_ap,
    seed_client,
    seed_gateway,
    seed_radio,
    seed_switch,
)

B = 300


def _rows(repo: Repository, bucket_ts: int, *, sle=None, entity_id=None):
    rows = repo.query_sle_minutes(
        bucket_ts,
        bucket_ts + B,
        group_by=("sle", "classifier", "entity_id", "attributed_entity_id"),
    )
    out = []
    for r in rows:
        if sle is not None and r["sle"] != sle:
            continue
        if entity_id is not None and r["entity_id"] != entity_id:
            continue
        out.append(r)
    return out


def _by_classifier(rows) -> dict:
    return {r["classifier"]: r["minutes"] for r in rows}


# --------------------------------------------------------------------------- #
# THE property: idle client with bad RSSI contributes ZERO failed minutes
# --------------------------------------------------------------------------- #
def test_idle_client_with_bad_rssi_contributes_zero(repo: Repository) -> None:
    ap = seed_ap(repo)
    active = seed_client(repo, "c-active", parent_id=ap)
    idle = seed_client(repo, "c-idle", parent_id=ap)

    # Both sit at a terrible RSSI for the whole bucket.
    for cid in (active, idle):
        rssi(repo, cid, [(30, -85.0), (90, -85.0), (150, -85.0)])
    # Only the active one moves traffic.
    make_active(repo, active, 0)

    SleMinutesJob(repo).run_bucket(0)

    # The idle client produced NO SLE minutes at all — not even ok.
    assert _rows(repo, 0, entity_id=idle) == []
    # The active client, same bad RSSI, is charged weak_signal minutes.
    active_cov = _by_classifier(_rows(repo, 0, sle=SLE_COVERAGE, entity_id=active))
    assert active_cov.get(CLS_WEAK_SIGNAL, 0) > 0


def test_activity_gate_threshold(repo: Repository) -> None:
    ap = seed_ap(repo)
    below = seed_client(repo, "c-below", parent_id=ap)
    above = seed_client(repo, "c-above", parent_id=ap)
    rssi(repo, below, [(30, -85.0)])
    rssi(repo, above, [(30, -85.0)])
    # floor is ~1KB/min * 5 = 5120 bytes/bucket
    put(repo, below, "rx_bytes", [(30, 4000.0)])
    put(repo, above, "rx_bytes", [(30, 6000.0)])

    SleMinutesJob(repo).run_bucket(0)

    assert _rows(repo, 0, entity_id=below) == []
    assert _rows(repo, 0, sle=SLE_COVERAGE, entity_id=above) != []


# --------------------------------------------------------------------------- #
# attribution exclusivity: a failed minute is never double-counted
# --------------------------------------------------------------------------- #
def test_coverage_minutes_split_exactly_across_classifiers(repo: Repository) -> None:
    ap = seed_ap(repo)
    c = seed_client(repo, "c1", parent_id=ap)
    make_active(repo, c, 0)
    # 5 samples: 2 weak (-80), 3 ok (-60), all with a clean noise floor.
    rssi(repo, c, [(30, -80.0), (90, -80.0), (150, -60.0), (210, -60.0), (270, -60.0)])
    put(repo, c, "noise", [(t, -95.0) for t in (30, 90, 150, 210, 270)])

    SleMinutesJob(repo).run_bucket(0)

    cov = _by_classifier(_rows(repo, 0, sle=SLE_COVERAGE, entity_id=c))
    # exactly-one-classifier: minutes partition into ok + weak_signal, summing to
    # the client's exposed minutes (5 samples over a full bucket = 5.0), with no
    # minute counted twice.
    assert cov[CLS_WEAK_SIGNAL] == 2.0
    assert cov[OK] == 3.0
    assert abs(sum(cov.values()) - 5.0) < 1e-9
    assert set(cov) == {CLS_WEAK_SIGNAL, OK}


def test_failed_coverage_minute_attributed_to_the_ap(repo: Repository) -> None:
    ap = seed_ap(repo)
    c = seed_client(repo, "c1", parent_id=ap)
    make_active(repo, c, 0)
    rssi(repo, c, [(30, -85.0), (90, -85.0)])

    SleMinutesJob(repo).run_bucket(0)

    weak = [
        r
        for r in _rows(repo, 0, sle=SLE_COVERAGE, entity_id=c)
        if r["classifier"] == CLS_WEAK_SIGNAL
    ]
    assert len(weak) == 1
    assert weak[0]["attributed_entity_id"] == ap


def test_partial_presence_scales_minutes_down(repo: Repository) -> None:
    ap = seed_ap(repo)
    c = seed_client(repo, "c1", parent_id=ap)
    make_active(repo, c, 0)
    # Only 2 RSSI samples in a bucket that expects ~5 -> 2 minutes of evidence.
    rssi(repo, c, [(30, -85.0), (90, -85.0)])

    SleMinutesJob(repo).run_bucket(0)

    cov = _by_classifier(_rows(repo, 0, sle=SLE_COVERAGE, entity_id=c))
    assert abs(cov[CLS_WEAK_SIGNAL] - 2.0) < 1e-9


# --------------------------------------------------------------------------- #
# bucket math across boundaries
# --------------------------------------------------------------------------- #
def test_minutes_bucket_across_boundary(repo: Repository) -> None:
    ap = seed_ap(repo)
    c = seed_client(repo, "c1", parent_id=ap)
    make_active(repo, c, 0)
    make_active(repo, c, B)
    # bucket 0: three weak samples; bucket 1: two ok samples. A sample exactly on
    # the boundary (ts=300) belongs to bucket 1.
    rssi(
        repo,
        c,
        [(30, -85.0), (150, -85.0), (270, -85.0), (B, -60.0), (B + 60, -60.0)],
    )

    job = SleMinutesJob(repo)
    job.run_range(0, 2 * B)

    b0 = _by_classifier(_rows(repo, 0, sle=SLE_COVERAGE, entity_id=c))
    b1 = _by_classifier(_rows(repo, B, sle=SLE_COVERAGE, entity_id=c))
    assert b0 == {CLS_WEAK_SIGNAL: 3.0}
    assert b1 == {OK: 2.0}
    assert bucket_of(B) == B  # the boundary sample landed in bucket 1, not 0


# --------------------------------------------------------------------------- #
# capacity via the client's radio
# --------------------------------------------------------------------------- #
def test_capacity_non_wifi_util_attributed_to_radio(repo: Repository) -> None:
    ap = seed_ap(repo)
    radio = seed_radio(repo, ap)
    c = seed_client(repo, "c1", parent_id=ap)
    make_active(repo, c, 0)
    # busy radio, low self-share, no neighbour -> non_wifi_util on the radio
    put(repo, radio, "cu_total", [(30, 70.0), (90, 70.0)])
    put(repo, radio, "cu_self_rx", [(30, 5.0), (90, 5.0)])
    put(repo, radio, "cu_self_tx", [(30, 5.0), (90, 5.0)])

    SleMinutesJob(repo).run_bucket(0)

    cap = _rows(repo, 0, sle=SLE_CAPACITY, entity_id=c)
    by = _by_classifier(cap)
    assert CLS_NON_WIFI_UTIL in by
    row = [r for r in cap if r["classifier"] == CLS_NON_WIFI_UTIL][0]
    assert row["attributed_entity_id"] == radio


def test_capacity_client_load_when_self_dominates(repo: Repository) -> None:
    ap = seed_ap(repo)
    radio = seed_radio(repo, ap)
    c = seed_client(repo, "c1", parent_id=ap)
    make_active(repo, c, 0)
    put(repo, radio, "cu_total", [(30, 80.0)])
    put(repo, radio, "cu_self_rx", [(30, 40.0)])
    put(repo, radio, "cu_self_tx", [(30, 20.0)])

    SleMinutesJob(repo).run_bucket(0)
    by = _by_classifier(_rows(repo, 0, sle=SLE_CAPACITY, entity_id=c))
    assert CLS_CLIENT_LOAD in by


# --------------------------------------------------------------------------- #
# roaming (per-bucket)
# --------------------------------------------------------------------------- #
def test_roaming_pingpong_from_roam_count(repo: Repository) -> None:
    ap = seed_ap(repo)
    c = seed_client(repo, "c1", parent_id=ap)
    make_active(repo, c, 0)
    rssi(repo, c, [(30, -60.0), (90, -60.0)])
    # 5 roams in the bucket (counter deltas) -> ping-pong, whole bucket minutes
    put(repo, c, "roam_count", [(30, 3.0), (90, 2.0)])

    SleMinutesJob(repo).run_bucket(0)
    by = _by_classifier(_rows(repo, 0, sle=SLE_ROAMING, entity_id=c))
    assert by == {CLS_PINGPONG: 5.0}


def test_no_roam_means_no_roaming_rows(repo: Repository) -> None:
    ap = seed_ap(repo)
    c = seed_client(repo, "c1", parent_id=ap)
    make_active(repo, c, 0)
    rssi(repo, c, [(30, -60.0)])
    SleMinutesJob(repo).run_bucket(0)
    assert _rows(repo, 0, sle=SLE_ROAMING, entity_id=c) == []


# --------------------------------------------------------------------------- #
# connect (measured only at connection time)
# --------------------------------------------------------------------------- #
def test_connect_ok_on_connected_event(repo: Repository) -> None:
    ap = seed_ap(repo)
    c = seed_client(repo, "c1", parent_id=ap)
    make_active(repo, c, 0)
    repo.record_event(ts=60, key="EVT_WU_Connected", entity_id=c)
    SleMinutesJob(repo).run_bucket(0)
    by = _by_classifier(_rows(repo, 0, sle=SLE_CONNECT, entity_id=c))
    assert by == {OK: 5.0}


def test_connect_dhcp_on_link_local(repo: Repository) -> None:
    ap = seed_ap(repo)
    c = seed_client(repo, "c1", parent_id=ap)
    make_active(repo, c, 0)
    repo.record_state_change(c, "ip", "169.254.9.9", ts=30)
    SleMinutesJob(repo).run_bucket(0)
    by = _by_classifier(_rows(repo, 0, sle=SLE_CONNECT, entity_id=c))
    assert "dhcp" in by


def test_connect_silent_when_no_connection_event(repo: Repository) -> None:
    ap = seed_ap(repo)
    c = seed_client(repo, "c1", parent_id=ap)
    make_active(repo, c, 0)
    repo.record_state_change(c, "ip", "192.168.1.5", ts=30)
    SleMinutesJob(repo).run_bucket(0)
    assert _rows(repo, 0, sle=SLE_CONNECT, entity_id=c) == []


# --------------------------------------------------------------------------- #
# WAN: gateway-less no-op vs probe-driven evaluation
# --------------------------------------------------------------------------- #
def test_wan_noop_on_gatewayless_site(repo: Repository) -> None:
    ap = seed_ap(repo)
    c = seed_client(repo, "c1", parent_id=ap)
    make_active(repo, c, 0)
    rssi(repo, c, [(30, -60.0)])
    result = SleMinutesJob(repo).run_bucket(0)
    assert result.wan_evaluated is False
    assert _rows(repo, 0, sle=SLE_WAN) == []


def test_wan_bufferbloat_not_fired_without_load_gate(repo: Repository) -> None:
    # Finding 2: a bare ICMP RTT spike with NO throughput/plan signal to prove the
    # link was under load must NOT be branded bufferbloat for every client. On a
    # gateway-less site (no xput series) the load premise is unprovable, so the WAN
    # minutes stay OK — the SLE mirrors the wan.bufferbloat detector's discipline.
    gw = seed_gateway(repo)
    ap = seed_ap(repo)
    c = seed_client(repo, "c1", parent_id=ap)
    make_active(repo, c, 0)
    put(repo, gw, "gw_rtt_ms", [(30, 20.0), (90, 20.0), (150, 260.0)])  # spike, no load

    result = SleMinutesJob(repo).run_bucket(0)
    assert result.wan_evaluated is True
    by = _by_classifier(_rows(repo, 0, sle=SLE_WAN, entity_id=c))
    assert by == {OK: 5.0}


def test_wan_bufferbloat_fires_with_near_plan_throughput(repo: Repository) -> None:
    # With a configured plan rate AND WAN throughput near it (the load gate met),
    # the loaded-minus-idle RTT spike is genuine bufferbloat.
    from types import SimpleNamespace

    gw = seed_gateway(repo)
    ap = seed_ap(repo)
    c = seed_client(repo, "c1", parent_id=ap)
    make_active(repo, c, 0)
    put(repo, gw, "gw_rtt_ms", [(30, 20.0), (90, 20.0), (150, 260.0)])
    put(repo, gw, "wan_xput_down", [(150, 90.0)])  # 90 of a 100 Mbps plan -> near plan
    settings = SimpleNamespace(wan_plan_down_mbps=100)

    result = SleMinutesJob(repo, settings=settings).run_bucket(0)
    assert result.wan_evaluated is True
    wan = _rows(repo, 0, sle=SLE_WAN, entity_id=c)
    by = _by_classifier(wan)
    assert by == {CLS_BUFFERBLOAT: 5.0}
    assert wan[0]["attributed_entity_id"] == gw


def test_wan_down_on_sustained_probe_failures(repo: Repository) -> None:
    gw = seed_gateway(repo)
    ap = seed_ap(repo)
    c = seed_client(repo, "c1", parent_id=ap)
    make_active(repo, c, 0)
    # No RTT samples, and every probe in the lookback failed -> sustained down.
    for ts in (30, 90, 150):
        repo.record_poll_run(job="probe.gw_rtt", ok=False, ts=ts, error="unreachable")

    result = SleMinutesJob(repo).run_bucket(0)
    assert result.wan_evaluated is True
    by = _by_classifier(_rows(repo, 0, sle=SLE_WAN, entity_id=c))
    assert by == {CLS_WAN_DOWN: 5.0}


def test_wan_not_down_when_dns_anchor_resolves(repo: Repository) -> None:
    # The real-world bug: inside a container the gateway RTT probe fails every poll
    # (no unprivileged ICMP; nothing to TCP-fall-back to), but the public DNS anchor
    # resolves fine — the internet is up. Absent/failed gw_rtt must NOT brand the
    # WAN down while the anchor is green.
    seed_gateway(repo)
    ap = seed_ap(repo)
    c = seed_client(repo, "c1", parent_id=ap)
    make_active(repo, c, 0)
    for ts in (30, 90, 150):  # every gateway-RTT probe failed
        repo.record_poll_run(job="probe.gw_rtt", ok=False, ts=ts, error="unreachable")
    for ts in (30, 90, 150):  # ...but the public DNS anchor resolved every time
        repo.record_poll_run(job="probe.dns.anchor", ok=True, ts=ts)

    result = SleMinutesJob(repo).run_bucket(0)
    assert result.wan_evaluated is True
    by = _by_classifier(_rows(repo, 0, sle=SLE_WAN, entity_id=c))
    assert CLS_WAN_DOWN not in by  # not down — the internet is demonstrably up
    assert by == {"ok": 5.0}


def test_wan_down_not_fired_on_single_bucket_hiccup(repo: Repository) -> None:
    # Finding 2: one bucket where the prober hiccuped (a few failures) while the
    # sustained window is overwhelmingly healthy must NOT brand the WAN down for
    # every active client. wan_down needs a sustained majority of failed probes.
    gw = seed_gateway(repo)
    ap = seed_ap(repo)
    c = seed_client(repo, "c1", parent_id=ap)
    make_active(repo, c, 0)
    # Ten successful probes across the 900 s lookback, three failures this bucket.
    for ts in range(-570, 0, 60):  # -570, -510, ... , -30  (10 successes)
        repo.record_poll_run(job="probe.gw_rtt", ok=True, ts=ts)
    for ts in (30, 90, 150):
        repo.record_poll_run(job="probe.gw_rtt", ok=False, ts=ts, error="unreachable")

    result = SleMinutesJob(repo).run_bucket(0)
    assert result.wan_evaluated is True
    by = _by_classifier(_rows(repo, 0, sle=SLE_WAN, entity_id=c))
    assert CLS_WAN_DOWN not in by
    assert by == {OK: 5.0}


def test_wan_isp_latency_robust_to_single_handoff_spike(repo: Repository) -> None:
    # A Starlink handoff spike: a single 110 ms sample among an otherwise-calm 35 ms
    # window. The bucket's MAX would be 110 (>100 ms floor) and brand every client
    # isp_latency; the windowed p50 stays ~35, so the WAN minutes stay OK.
    gw = seed_gateway(repo)
    ap = seed_ap(repo)
    c = seed_client(repo, "c1", parent_id=ap)
    make_active(repo, c, 0)
    put(
        repo,
        gw,
        "gw_rtt_ms",
        [(30, 35.0), (90, 34.0), (150, 110.0), (210, 36.0), (270, 35.0)],  # one spike
    )
    result = SleMinutesJob(repo).run_bucket(0)
    assert result.wan_evaluated is True
    by = _by_classifier(_rows(repo, 0, sle=SLE_WAN, entity_id=c))
    assert by == {OK: 5.0}


def test_wan_isp_latency_fires_on_sustained_gw_rtt(repo: Repository) -> None:
    # A sustained shift: the whole trailing window sits at ~130 ms gw_rtt. The
    # windowed p50 clears the absolute floor -> isp_latency, attributed to the
    # gateway. Proves the gateway-less Starlink site is judged via the probe.
    from netadmin.sle.classifiers import CLS_ISP_LATENCY

    gw = seed_gateway(repo)
    ap = seed_ap(repo)
    c = seed_client(repo, "c1", parent_id=ap)
    make_active(repo, c, 0)
    put(repo, gw, "gw_rtt_ms", [(t, 130.0) for t in range(30, 300, 30)])  # 9 samples
    result = SleMinutesJob(repo).run_bucket(0)
    assert result.wan_evaluated is True
    wan = _rows(repo, 0, sle=SLE_WAN, entity_id=c)
    by = _by_classifier(wan)
    assert CLS_ISP_LATENCY in by
    assert wan[0]["attributed_entity_id"] == gw


def test_wan_impact_weighted_by_active_clients(repo: Repository) -> None:
    gw = seed_gateway(repo)
    ap = seed_ap(repo)
    c1 = seed_client(repo, "c1", parent_id=ap)
    c2 = seed_client(repo, "c2", parent_id=ap)
    idle = seed_client(repo, "c-idle", parent_id=ap)
    make_active(repo, c1, 0)
    make_active(repo, c2, 0)
    put(repo, gw, "gw_rtt_ms", [(30, 20.0), (150, 260.0)])

    SleMinutesJob(repo).run_bucket(0)
    # both active clients accrue WAN minutes; the idle one does not
    assert _rows(repo, 0, sle=SLE_WAN, entity_id=c1) != []
    assert _rows(repo, 0, sle=SLE_WAN, entity_id=c2) != []
    assert _rows(repo, 0, sle=SLE_WAN, entity_id=idle) == []


# --------------------------------------------------------------------------- #
# infra: state-timeline integration (device-keyed)
# --------------------------------------------------------------------------- #
def test_infra_full_down_bucket(repo: Repository) -> None:
    sw = seed_switch(repo)
    repo.record_state_change(sw, "state", "0", ts=-10)  # down before the bucket
    SleMinutesJob(repo).run_bucket(0)
    by = _by_classifier(_rows(repo, 0, sle=SLE_INFRA, entity_id=sw))
    assert by == {CLS_SW_DOWN: 5.0}


def test_infra_online_device_is_ok(repo: Repository) -> None:
    sw = seed_switch(repo)
    repo.record_state_change(sw, "state", "1", ts=-10)
    SleMinutesJob(repo).run_bucket(0)
    by = _by_classifier(_rows(repo, 0, sle=SLE_INFRA, entity_id=sw))
    assert by == {OK: 5.0}


def test_infra_partial_down_splits_at_transition(repo: Repository) -> None:
    sw = seed_switch(repo)
    repo.record_state_change(sw, "state", "1", ts=-10)  # up at bucket start
    repo.record_state_change(sw, "state", "0", ts=180)  # goes down 180s in
    SleMinutesJob(repo).run_bucket(0)
    by = _by_classifier(_rows(repo, 0, sle=SLE_INFRA, entity_id=sw))
    assert abs(by[OK] - 3.0) < 1e-9  # 180 s up
    assert abs(by[CLS_SW_DOWN] - 2.0) < 1e-9  # 120 s down


def test_infra_restart_loop(repo: Repository) -> None:
    sw = seed_switch(repo)
    repo.record_state_change(sw, "state", "0", ts=-10)
    repo.record_state_change(sw, "state", "1", ts=100)
    repo.record_state_change(sw, "state", "0", ts=200)
    repo.record_state_change(sw, "state", "1", ts=250)  # second down->up cycle
    SleMinutesJob(repo).run_bucket(0)
    by = _by_classifier(_rows(repo, 0, sle=SLE_INFRA, entity_id=sw))
    assert "restart_loop" in by
    assert "sw_down" not in by  # restart overrides the plain down classifier


# --------------------------------------------------------------------------- #
# wired client: wireless SLEs no-op
# --------------------------------------------------------------------------- #
def test_wired_client_no_wireless_sles(repo: Repository) -> None:
    sw = seed_switch(repo)
    c = seed_client(repo, "wired-1", parent_id=sw)
    make_active(repo, c, 0)  # active, but wired (no rssi/radio)
    SleMinutesJob(repo).run_bucket(0)
    assert _rows(repo, 0, sle=SLE_COVERAGE, entity_id=c) == []
    assert _rows(repo, 0, sle=SLE_CAPACITY, entity_id=c) == []
    assert _rows(repo, 0, sle=SLE_ROAMING, entity_id=c) == []


# --------------------------------------------------------------------------- #
# idempotent recompute
# --------------------------------------------------------------------------- #
def test_recompute_is_idempotent(repo: Repository) -> None:
    ap = seed_ap(repo)
    c = seed_client(repo, "c1", parent_id=ap)
    make_active(repo, c, 0)
    rssi(repo, c, [(30, -85.0), (90, -60.0)])

    job = SleMinutesJob(repo)
    job.run_bucket(0)
    first = _by_classifier(_rows(repo, 0, sle=SLE_COVERAGE, entity_id=c))
    job.run_bucket(0)  # recompute same inputs
    second = _by_classifier(_rows(repo, 0, sle=SLE_COVERAGE, entity_id=c))
    assert first == second
