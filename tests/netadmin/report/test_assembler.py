"""Assembler tests: the model is built from real queries, empties stay honest.

Drives :func:`netadmin.report.build_report` over a hand-seeded migrated store
whose every number is known, and asserts the four gates the task fixes: scorecard
math (severity counts sum to the total, health score = round(headline x 100)),
the RSSI histogram summing to the client count, per-channel neighbour aggregation
(never per-BSSID), findings-template completeness with correlated issues grouped
into one finding, environmental noise collapsed to one finding, pending issues
excluded -- and that an empty store returns honest empties, never a fabricated
value.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.report import build_report, report_to_dict
from netadmin.report.assembler import ROGUE_BSS_TYPE
from netadmin.report.models import ReportModel
from netadmin.store.repository import Repository, SampleReading

NOW = 1_700_000_000
WINDOW_S = 7 * 86_400
START = NOW - WINDOW_S
BUCKET = NOW - 3_600  # inside the window
SEEN = NOW - 100  # last_seen inside the window


@pytest.fixture
def repo(tmp_db_path: Path) -> Repository:
    r = Repository.open(tmp_db_path)
    yield r
    r.close()


def _ent(repo: Repository, etype, native_id, **kw) -> int:
    return repo.upsert_entity(Entity(entity_type=etype, native_id=native_id, **kw), ts=SEEN)


@pytest.fixture
def seeded(repo: Repository) -> dict[str, int]:
    """A small, fully-known network: gateway, switch, two APs, radios, clients,
    neighbour BSSes, SLE minutes, issues (one incident + one solo + two
    environmental + one excluded pending)."""
    ids: dict[str, int] = {}

    gw = _ent(repo, EntityType.GATEWAY, "gw:00", name="Gateway", model="UDM")
    sw = _ent(repo, EntityType.SWITCH, "sw:00", name="Switch", model="USW-24")
    ap_core = _ent(repo, EntityType.AP, "ap:core", name="Core AP", model="U6-Pro")
    ap_mesh = _ent(repo, EntityType.AP, "ap:mesh", name="Mesh AP", model="U6-Mesh")
    r_core = _ent(repo, EntityType.RADIO, "ap:core:ng", parent_id=ap_core, meta={"band": "ng"})
    r_mesh = _ent(repo, EntityType.RADIO, "ap:mesh:ng", parent_id=ap_mesh, meta={"band": "ng"})
    ids.update(gw=gw, sw=sw, ap_core=ap_core, ap_mesh=ap_mesh, r_core=r_core, r_mesh=r_mesh)

    # uplink types
    repo.record_state_change(gw, "uplink_type", "wire", ts=SEEN)
    repo.record_state_change(sw, "uplink_type", "wire", ts=SEEN)
    repo.record_state_change(ap_core, "uplink_type", "wire", ts=SEEN)
    repo.record_state_change(ap_mesh, "uplink_type", "wireless", ts=SEEN)
    # radio channels
    repo.record_state_change(r_core, "channel", "6", ts=SEEN)
    repo.record_state_change(r_mesh, "channel", "1", ts=SEEN)

    # 5 wireless clients (parented to Core AP) with RSSI; 2 wired (no RSSI).
    wifi_rssi = [-55.0, -60.0, -68.0, -74.0, -80.0]  # two weak (< -72)
    client_ids = []
    for i, rssi in enumerate(wifi_rssi):
        cid = _ent(
            repo,
            EntityType.CLIENT,
            f"cl:w{i}",
            name=f"wifi{i}",
            parent_id=ap_core,
            meta={"is_wired": False},
        )
        client_ids.append(cid)
        repo.record_samples([SampleReading(entity_id=cid, metric="rssi", ts=BUCKET, value=rssi)])
    for i in range(2):
        _ent(
            repo,
            EntityType.CLIENT,
            f"cl:e{i}",
            name=f"wired{i}",
            parent_id=sw,
            meta={"is_wired": True},
        )
    ids["client0"] = client_ids[0]

    # mesh backhaul RSSI sample + radio utilisation samples
    repo.record_samples(
        [
            SampleReading(entity_id=ap_mesh, metric="uplink_rssi", ts=BUCKET, value=-78.0),
            SampleReading(entity_id=r_core, metric="cu_total", ts=BUCKET, value=45.0),
            SampleReading(entity_id=r_core, metric="cu_self_rx", ts=BUCKET, value=10.0),
            SampleReading(entity_id=r_core, metric="cu_self_tx", ts=BUCKET, value=5.0),
            SampleReading(entity_id=r_mesh, metric="cu_total", ts=BUCKET, value=30.0),
        ]
    )

    # Neighbour BSSes: 4 on ch6, 2 on ch11, 1 on ch36 -> 7 total, 3 channel bars.
    plan = [("ng", 6)] * 4 + [("ng", 11)] * 2 + [("na", 36)]
    for i, (band, chan) in enumerate(plan):
        _ent(
            repo,
            ROGUE_BSS_TYPE,
            f"02:00:00:00:00:{i:02d}",
            name=f"NEIGH-{i}",
            meta={"band": band, "channel": chan, "rssi": -70},
        )

    # SLE minutes: coverage 90 ok + 10 weak_signal attributed to the mesh AP.
    repo.upsert_sle_minute(
        bucket_ts=BUCKET,
        sle="coverage",
        classifier="ok",
        entity_id=client_ids[0],
        minutes=90.0,
        attributed_entity_id=None,
    )
    repo.upsert_sle_minute(
        bucket_ts=BUCKET,
        sle="coverage",
        classifier="weak_signal",
        entity_id=client_ids[0],
        minutes=10.0,
        attributed_entity_id=ap_mesh,
    )

    # Issues.
    mesh = repo.insert_issue(
        fingerprint="fp-mesh",
        detector_key="wifi.mesh_uplink",
        severity="p2",
        state="active",
        first_seen_ts=BUCKET,
        last_seen_ts=SEEN,
        title="Weak mesh backhaul on Mesh AP",
        entity_id=ap_mesh,
        evidence={"uplink_rssi_dbm": -78, "confounders_checked": ["wireless_uplink_confirmed"]},
    )
    cov = repo.insert_issue(
        fingerprint="fp-cov",
        detector_key="net.coverage_hole",
        severity="p2",
        state="active",
        first_seen_ts=BUCKET,
        last_seen_ts=SEEN,
        title="Coverage hole on Mesh AP",
        entity_id=ap_mesh,
    )
    flaky = repo.insert_issue(
        fingerprint="fp-flaky",
        detector_key="client.flaky",
        severity="p3",
        state="active",
        first_seen_ts=BUCKET,
        last_seen_ts=SEEN,
        title="Client dropping on Mesh AP",
        entity_id=client_ids[0],
    )
    repo.insert_issue(
        fingerprint="fp-cable",
        detector_key="wired.bad_cable",
        severity="p2",
        state="active",
        first_seen_ts=BUCKET,
        last_seen_ts=SEEN,
        title="Cable fault on Switch port 5",
        entity_id=sw,
    )
    # Site-scoped, per-band: the neighbour-density issue carries no entity.
    density = repo.insert_issue(
        fingerprint="fp-density",
        detector_key="wifi.neighbor_density",
        severity="p3",
        state="active",
        first_seen_ts=BUCKET,
        last_seen_ts=SEEN,
        title="7 neighbouring networks share our 2.4 GHz channels",
    )
    chan = repo.insert_issue(
        fingerprint="fp-chan",
        detector_key="wifi.channel_plan",
        severity="p3",
        state="active",
        first_seen_ts=BUCKET,
        last_seen_ts=SEEN,
        title="Channel-plan contention on Core AP",
        entity_id=r_core,
    )
    # Pending (unconfirmed) -> must be excluded from findings.
    repo.insert_issue(
        fingerprint="fp-pending",
        detector_key="wifi.sticky_client",
        severity="p3",
        state="pending",
        first_seen_ts=BUCKET,
        last_seen_ts=SEEN,
        title="Sticky client (pending)",
        entity_id=client_ids[0],
    )
    ids.update(mesh=mesh, cov=cov, flaky=flaky, density=density, chan=chan)

    # Incident grouping the mesh root + two symptoms.
    inc = repo.insert_incident(
        fingerprint="inc-mesh",
        root_issue_id=mesh,
        severity="p2",
        state="active",
        first_seen_ts=BUCKET,
        last_seen_ts=SEEN,
        title="Mesh cluster",
        summary="Weak backhaul with coverage + client symptoms",
    )
    repo.replace_incident_members(
        inc,
        [
            {"issue_id": mesh, "role": "root", "rule": "", "rationale": ""},
            {"issue_id": cov, "role": "symptom", "rule": "same_ap", "rationale": "same AP"},
            {"issue_id": flaky, "role": "symptom", "rule": "under_ap", "rationale": "under AP"},
        ],
    )
    ids["incident"] = inc
    return ids


def _report(repo: Repository) -> ReportModel:
    return build_report(repo, None, now=NOW, window_s=WINDOW_S)


# --------------------------------------------------------------------------- #
# Scorecard math + health
# --------------------------------------------------------------------------- #
def test_scorecard_counts_sum_to_total(seeded, repo) -> None:
    doc = report_to_dict(_report(repo))
    sc = doc["executive_summary"]["scorecard"]
    assert sum(sc["findings_by_severity"].values()) == sc["total_findings"]
    assert sc["total_findings"] == len(doc["findings"])


def test_health_score_is_round_of_headline(seeded, repo) -> None:
    doc = report_to_dict(_report(repo))
    # coverage 90 ok / 100 total = 0.9, the only SLE with data -> headline 90.
    assert doc["health"]["headline_score"] == 90
    assert doc["executive_summary"]["scorecard"]["health_score"] == 90
    trend = doc["health"]["trend"]
    assert len(trend) == 1 and trend[0]["score"] == 90


# --------------------------------------------------------------------------- #
# Histogram sums to client count
# --------------------------------------------------------------------------- #
def test_histogram_sums_to_client_count(seeded, repo) -> None:
    doc = report_to_dict(_report(repo))
    hist = doc["clients"]["rssi_histogram"]
    assert hist["total"] == 5  # five clients had an RSSI sample
    assert sum(b["count"] for b in hist["bins"]) == 5
    assert hist["weak_count"] == 2  # -74 and -80 fall below the -72 floor
    assert doc["clients"]["clients_without_rssi"] == 2  # the two wired clients


def test_clients_per_ap_counts(seeded, repo) -> None:
    doc = report_to_dict(_report(repo))
    core = next(c for c in doc["clients"]["clients_per_ap"] if c["name"] == "Core AP")
    assert core["client_count"] == 5


# --------------------------------------------------------------------------- #
# Neighbour aggregation
# --------------------------------------------------------------------------- #
def test_neighbor_density_aggregated_not_per_bssid(seeded, repo) -> None:
    doc = report_to_dict(_report(repo))
    dens = doc["rf"]["neighbor_density"]
    assert dens["total"] == 7
    assert len(dens["by_channel"]) == 3  # 7 BSSes -> 3 channels, never 7 alarms
    by = {(b["band"], b["channel"]): b["count"] for b in dens["by_channel"]}
    assert by == {("2.4", 6): 4, ("2.4", 11): 2, ("5", 36): 1}


# --------------------------------------------------------------------------- #
# Findings: grouping, environmental collapse, pending exclusion, completeness
# --------------------------------------------------------------------------- #
def test_correlated_issues_group_into_one_finding(seeded, repo) -> None:
    doc = report_to_dict(_report(repo))
    mesh = next(f for f in doc["findings"] if f["detector_key"] == "wifi.mesh_uplink")
    assert mesh["incident_id"] == seeded["incident"]
    assert len(mesh["symptoms"]) == 2
    assert set(mesh["source_issue_ids"]) == {seeded["mesh"], seeded["cov"], seeded["flaky"]}
    # P2 with 10 failed client-minutes attributed to the AP -> High, one client.
    assert mesh["severity"] == "high"
    assert mesh["impact"]["fail_minutes"] == 10.0
    assert mesh["impact"]["affected_clients"] == 1
    assert mesh["root_cause"].startswith("Root cause of 2 correlated")


def test_environmental_noise_collapses_to_one_finding(seeded, repo) -> None:
    doc = report_to_dict(_report(repo))
    env = [f for f in doc["findings"] if f["detector_key"] == "wifi.rf_environment"]
    assert len(env) == 1
    assert set(env[0]["source_issue_ids"]) == {seeded["density"], seeded["chan"]}
    # No per-detector density/channel findings leak through.
    assert not any(
        f["detector_key"] in ("wifi.neighbor_density", "wifi.channel_plan") for f in doc["findings"]
    )


def test_rogue_ap_is_not_collapsed_into_the_environment(seeded, repo) -> None:
    """A security claim gets its own ranked finding, never the environmental bucket."""
    repo.insert_issue(
        fingerprint="fp-spoof",
        detector_key="wifi.rogue_ap",
        severity="p1",
        state="active",
        first_seen_ts=BUCKET,
        last_seen_ts=SEEN,
        title="Foreign AP broadcasting our SSID HomeNet",
        entity_id=seeded["r_core"],
    )
    doc = report_to_dict(_report(repo))
    rogue = [f for f in doc["findings"] if f["detector_key"] == "wifi.rogue_ap"]
    assert len(rogue) == 1
    env = [f for f in doc["findings"] if f["detector_key"] == "wifi.rf_environment"]
    assert rogue[0]["id"] not in {f["id"] for f in env}
    # It outranks the environmental summary: findings are ordered worst-first.
    ids = [f["id"] for f in doc["findings"]]
    assert ids.index(rogue[0]["id"]) < ids.index(env[0]["id"])


def test_pending_issue_is_excluded(seeded, repo) -> None:
    doc = report_to_dict(_report(repo))
    assert all(f["detector_key"] != "wifi.sticky_client" for f in doc["findings"])


def test_three_findings_and_stable_ids(seeded, repo) -> None:
    doc = report_to_dict(_report(repo))
    # mesh incident (High), bad_cable solo (Medium), environmental (Low).
    assert len(doc["findings"]) == 3
    ids = [f["id"] for f in doc["findings"]]
    assert ids == ["WLAN-01", "LAN-01", "ENV-01"]


def test_findings_template_is_complete(seeded, repo) -> None:
    doc = report_to_dict(_report(repo))
    for f in doc["findings"]:
        assert f["id"] and f["title"]
        assert f["severity"] in ("critical", "high", "medium", "low", "info")
        assert f["affected_assets"]  # at least one asset
        assert f["observation"].strip()
        assert isinstance(f["evidence"], dict)
        assert isinstance(f["impact"]["fail_minutes"], (int, float))
        assert f["impact"]["summary"].strip()
        # Root cause and recommendation are always present and specific.
        assert len(f["root_cause"].strip()) > 10
        assert len(f["recommendation"].strip()) > 10


def test_roadmap_phases_trace_to_findings(seeded, repo) -> None:
    doc = report_to_dict(_report(repo))
    finding_ids = {f["id"] for f in doc["findings"]}
    for phase in ("now", "soon", "strategic"):
        for rec in doc["roadmap"][phase]:
            assert rec["finding_id"] in finding_ids
            assert rec["text"].strip()
    # High -> now, Medium -> soon, Low -> strategic.
    assert [r["finding_id"] for r in doc["roadmap"]["now"]] == ["WLAN-01"]
    assert [r["finding_id"] for r in doc["roadmap"]["soon"]] == ["LAN-01"]
    assert [r["finding_id"] for r in doc["roadmap"]["strategic"]] == ["ENV-01"]


# --------------------------------------------------------------------------- #
# Topology / inventory / RF wiring
# --------------------------------------------------------------------------- #
def test_topology_mesh_backhaul_and_parentage(seeded, repo) -> None:
    doc = report_to_dict(_report(repo))
    topo = doc["topology"]
    assert topo["gateway"]["role"] == "gateway"
    assert len(topo["switches"]) == 1 and len(topo["aps"]) == 2
    mesh = next(a for a in topo["aps"] if a["name"] == "Mesh AP")
    assert mesh["uplink"] == "wireless"
    assert mesh["mesh_uplink_rssi"] == -78.0
    assert mesh["backhaul_status"] == "bad"  # -78 is below the -70 warn floor
    core = next(a for a in topo["aps"] if a["name"] == "Core AP")
    assert core["uplink"] == "wire" and core["client_count"] == 5


def test_inventory_counts_and_rows(seeded, repo) -> None:
    doc = report_to_dict(_report(repo))
    inv = doc["inventory"]
    assert inv["counts"] == {"ap": 2, "switch": 1, "gateway": 1}
    assert len(inv["devices"]) == 4
    core = next(d for d in inv["devices"] if d["name"] == "Core AP")
    assert core["role"] == "ap" and core["uplink"] == "wire" and core["model"] == "U6-Pro"


def test_rf_utilization_self_split(seeded, repo) -> None:
    doc = report_to_dict(_report(repo))
    core = next(u for u in doc["rf"]["utilization"] if u["cu_total"] == 45.0)
    assert core["band"] == "2.4" and core["channel"] == "6"
    assert core["cu_self"] == 15.0  # 10 rx + 5 tx
    assert core["cu_non_self"] == 30.0
    assert doc["rf"]["utilization_reference_pct"] == 70.0


def test_cover_and_scope(seeded, repo) -> None:
    doc = report_to_dict(_report(repo))
    assert doc["cover"]["counts"] == {"aps": 2, "switches": 1, "gateways": 1, "clients": 7}
    assert doc["cover"]["window"]["duration_s"] == WINDOW_S
    # Coverage is measured (no poll_runs seeded -> 0.0), reported as partial, not hidden.
    jobs = {c["job"]: c for c in doc["scope"]["coverage"]}
    assert "fast_device" in jobs and jobs["fast_device"]["fraction"] == 0.0
    assert doc["scope"]["limitations"]  # honest limitations listed


def test_report_is_json_serialisable(seeded, repo) -> None:
    json.dumps(report_to_dict(_report(repo)))  # must not raise


# --------------------------------------------------------------------------- #
# Honest empties (no fabrication when data is absent)
# --------------------------------------------------------------------------- #
def test_empty_store_returns_honest_empties(repo) -> None:
    doc = report_to_dict(_report(repo))

    # No fabricated health: the score is None, not 0, and the verdict says so.
    assert doc["health"]["headline_score"] is None
    assert doc["health"]["trend"] == []
    sc = doc["executive_summary"]["scorecard"]
    assert sc["health_score"] is None
    assert sc["posture"] == "insufficient data"
    assert sc["total_findings"] == 0
    assert all(v == 0 for v in sc["findings_by_severity"].values())

    # No fabricated findings / recommendations.
    assert doc["findings"] == []
    assert doc["roadmap"] == {"now": [], "soon": [], "strategic": []}
    assert doc["executive_summary"]["top_findings"] == []
    assert doc["executive_summary"]["recommendation_summary"] == (
        "No action is required over this window."
    )

    # No fabricated inventory / topology / RF / clients.
    assert doc["inventory"]["devices"] == []
    assert doc["inventory"]["counts"] == {"ap": 0, "switch": 0, "gateway": 0}
    assert doc["topology"]["gateway"] is None
    assert doc["topology"]["switches"] == [] and doc["topology"]["aps"] == []
    assert doc["rf"]["utilization"] == []
    assert doc["rf"]["neighbor_density"]["total"] == 0
    assert doc["clients"]["rssi_histogram"]["total"] == 0
    assert sum(b["count"] for b in doc["clients"]["rssi_histogram"]["bins"]) == 0
    assert doc["clients"]["worst_devices"] == []

    # The static, config-derived sections are still present (not data-fabricated).
    assert doc["appendix"]["severity_rubric"]
    assert doc["scope"]["limitations"]
    json.dumps(doc)
