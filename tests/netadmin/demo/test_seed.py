"""Tests for the deterministic demo-dataset generator (``netadmin demo-seed``).

The generator must produce a schema-valid database the repository can open and the
API can serve, populated with the fictional network the public demo shows, and --
non-negotiably -- must leak **none** of the owner's real data: no ``192.168.x``
addresses, no real MACs (everything is locally-administered ``02:...``), no real
hostnames or place names. These tests assert the structural counts (entities,
issues, SLE), the PII firewall, and that the result actually serves over the API.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from netadmin.config import HaConfig, Settings
from netadmin.demo.seed import DEFAULT_NOW, DemoStats, seed_demo
from netadmin.server.main import DaemonComponents, create_app
from netadmin.sle.scores import sle_scores
from netadmin.store.repository import Repository

DAY = 86_400

# Substrings that would betray a private network or the owner's identity. The
# generic network ranges are inline; owner-specific name terms are loaded from an
# optional, gitignored local file (`.pii_denylist` beside this test, one term per
# line) so they never enter the public repo. Public CI runs the generic checks;
# the owner's local run also asserts their real names are absent from the demo.
_FORBIDDEN_GENERIC = (
    "192.168.",
    "10.0.0",
    "172.16.",
    "172.17.",
    "172.18.",
)


def _load_local_denylist() -> tuple[str, ...]:
    path = Path(__file__).with_name(".pii_denylist")
    if not path.exists():
        return ()
    return tuple(
        line.strip().lower()
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    )


_FORBIDDEN = _FORBIDDEN_GENERIC + _load_local_denylist()
_MAC_RE = re.compile(r"\b([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})\b")
_IP_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
_ALLOWED_IP_PREFIXES = ("192.0.2.", "198.51.100.")

_TABLES = (
    "entities",
    "state_changes",
    "series",
    "samples",
    "samples_hourly",
    "samples_daily",
    "events",
    "poll_runs",
    "issues",
    "issue_events",
    "sle_minutes",
    "changes",
    "baselines",
    "investigations",
)


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, DemoStats]:
    """Generate the demo DB once (default now/seed) for the whole module."""
    path = tmp_path_factory.mktemp("demo") / "netadmin-demo.db"
    stats = seed_demo(path)
    return path, stats


def _all_text_values(conn: sqlite3.Connection):
    """Yield every TEXT value stored in every column of every table."""
    conn.row_factory = sqlite3.Row
    for table in _TABLES:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        for row in conn.execute(f"SELECT * FROM {table}"):
            for col in cols:
                value = row[col]
                if isinstance(value, str):
                    yield table, col, value


# --------------------------------------------------------------------------- #
# structural counts
# --------------------------------------------------------------------------- #
def test_entity_counts(demo_db) -> None:
    _path, stats = demo_db
    assert stats.entities_total == 94
    assert stats.entities_by_type == {
        "ap": 8,
        "switch": 3,
        "gateway": 1,
        "client": 48,
        "port": 18,
        "radio": 16,
    }


def test_issue_counts_by_state_and_severity(demo_db) -> None:
    _path, stats = demo_db
    # 11 baseline + the Back Porch mesh cluster (mesh_uplink root + coverage_hole
    # + airtime + client.flaky) + a standalone neighbor_density = 16.
    assert stats.issues_total == 16
    assert stats.issues_by_state == {"active": 13, "resolving": 1, "resolved": 2}
    assert stats.issues_by_severity == {"p1": 1, "p2": 9, "p3": 6}


def test_supporting_data_populated(demo_db) -> None:
    _path, stats = demo_db
    # Time series + rollups + events + accounting are all present and non-trivial.
    assert stats.series > 100
    assert stats.samples > 50_000
    assert stats.events > 100
    # 14 jobs at their own cadence across the full (>=8 day) history window, so
    # this is a settled, fully-observed week -- not the handful of thousand rows
    # a 30 h/5-job slice would produce.
    assert stats.poll_runs > 90_000
    assert stats.baselines > 0
    # The issue surface: full trails, one fix in the changes ledger, one LLM thread.
    assert stats.issue_events >= 25
    assert stats.changes == 1
    assert stats.investigations == 1


def test_sle_minutes_and_headline(demo_db) -> None:
    _path, stats = demo_db
    # SLE minutes are generated across the full (>=8 day) history window, not
    # just a recent 36 h slice, so a settled week is ~5x the old row count.
    assert stats.sle_minutes > 100_000
    # A realistic, believable headline (not a suspicious 100 %). A settled week
    # dilutes the recent incidents against mostly-healthy history, so this sits
    # a bit higher than a "last 36 h only" dataset would.
    assert stats.sle_headline is not None
    assert 0.82 <= stats.sle_headline <= 0.92


# --------------------------------------------------------------------------- #
# the PII firewall
# --------------------------------------------------------------------------- #
def test_no_real_pii_or_private_ips(demo_db) -> None:
    path, _stats = demo_db
    conn = Repository.open(path).connection
    macs: set[str] = set()
    ips: set[str] = set()
    for table, col, value in _all_text_values(conn):
        low = value.lower()
        for token in _FORBIDDEN:
            assert token not in low, f"forbidden token {token!r} leaked in {table}.{col}: {value!r}"
        macs.update(m.lower() for m in _MAC_RE.findall(value))
        ips.update(_IP_RE.findall(value))

    assert macs, "expected fabricated MACs in the demo data"
    for mac in macs:
        assert mac.startswith("02:"), f"non-documentation MAC leaked: {mac}"

    assert ips, "expected fabricated IPs in the demo data"
    for ip in ips:
        assert ip.startswith(_ALLOWED_IP_PREFIXES), f"non-RFC5737 IP leaked: {ip}"


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #
def _content_signature(path: Path) -> str:
    conn = Repository.open(path).connection
    digest = hashlib.sha256()
    for table in _TABLES:
        for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2"):
            digest.update(repr(tuple(row)).encode("utf-8"))
    return digest.hexdigest()


def test_regeneration_is_deterministic(tmp_path: Path) -> None:
    a = tmp_path / "a.db"
    b = tmp_path / "b.db"
    seed_demo(a)
    seed_demo(b)
    assert _content_signature(a) == _content_signature(b)


# --------------------------------------------------------------------------- #
# issue lifecycle spread + honest evidence
# --------------------------------------------------------------------------- #
def test_issue_spread_and_lifecycle(demo_db) -> None:
    path, _stats = demo_db
    repo = Repository.open(path)
    try:
        by_key = {r["detector_key"]: r for r in repo.list_issues()}
        # The showcase spread is all present.
        for key in (
            "wired.bad_cable",
            "wifi.sticky_client",
            "wifi.pingpong_roamer",
            "wifi.airtime_saturation",
            "wan.isp_degraded",
            "net.firmware_regression",
            "wired.port_flapping",
            "wired.duplex_mismatch",
            "wan.dns_slow",
            "wifi.channel_plan",
            "wifi.min_rssi_misconfig",
            # The correlation showcase: the mesh cluster + the RF environment.
            "wifi.mesh_uplink",
            "net.coverage_hole",
            "client.flaky",
            "wifi.neighbor_density",
        ):
            assert key in by_key, f"missing seeded issue {key}"

        # The genuine bad cable: gigabit-capable port negotiated at 100 with errors.
        cable = repo.get_issue(by_key["wired.bad_cable"]["id"])
        import json

        ev = json.loads(cable["evidence"])
        assert ev["negotiated_speed"] == 100
        assert ev["port_capable_speed"] == 1000
        assert set(ev["signals"]) == {"error_rate", "speed_downshift"}
        assert "port_gigabit_capable" in ev["confounders_checked"]

        # The RESOLVED and RESOLVING issues carry confounders + a full event trail.
        for key, state in (
            ("wired.duplex_mismatch", "resolved"),
            ("wan.dns_slow", "resolving"),
        ):
            row = repo.get_issue(by_key[key]["id"])
            assert row["state"] == state
            assert json.loads(row["evidence"])["confounders_checked"]
            trail = repo.list_issue_events(row["id"])
            kinds = [e["kind"] for e in trail]
            assert "detected" in kinds and "escalated" in kinds

        # The fix loop: proposed -> applied -> verified, with a changes-ledger row.
        chan = repo.get_issue(by_key["wifi.channel_plan"]["id"])
        assert chan["fix_state"] == "verified"
        kinds = [e["kind"] for e in repo.list_issue_events(chan["id"])]
        assert {"fix_proposed", "fix_applied", "fix_verified", "resolved"} <= set(kinds)
        changes = repo.list_changes(issue_id=chan["id"])
        assert len(changes) == 1
        assert changes[0]["action"] == "set_radio_channel"
        assert changes[0]["status"] == "applied"
    finally:
        repo.close()


def test_correlation_incidents_grouped(demo_db) -> None:
    """The demo ships with real incidents: the mesh cluster grouped, the rest
    standing alone as incidents-of-one (section 17)."""
    path, _stats = demo_db
    repo = Repository.open(path)
    try:
        incidents = repo.list_incidents(open_only=True)
        assert incidents, "the demo must ship with computed incidents"
        by_root_key = {}
        for inc in incidents:
            root = repo.get_issue(int(inc["root_issue_id"]))
            by_root_key[root["detector_key"]] = inc

        # The Back Porch mesh incident groups the root + its three symptoms.
        mesh = by_root_key["wifi.mesh_uplink"]
        members = repo.list_incident_members(int(mesh["id"]))
        roles = [m["role"] for m in members]
        assert roles.count("root") == 1
        assert roles.count("symptom") == 3
        symptom_keys = {
            repo.get_issue(int(m["issue_id"]))["detector_key"]
            for m in members
            if m["role"] == "symptom"
        }
        assert symptom_keys == {"net.coverage_hole", "wifi.airtime_saturation", "client.flaky"}
        # Every symptom link carries a recorded rationale (conservatism: never a
        # link without a why).
        for m in members:
            if m["role"] == "symptom":
                assert m["rationale"].strip()

        # Neighbour density could not be attributed to any root -> incident-of-one.
        density = by_root_key["wifi.neighbor_density"]
        assert len(repo.list_incident_members(int(density["id"]))) == 1
    finally:
        repo.close()


def test_sle_offenders_point_at_fictional_infra(demo_db) -> None:
    path, _stats = demo_db
    repo = Repository.open(path)
    try:
        report = sle_scores(repo, DEFAULT_NOW - DAY, DEFAULT_NOW)
        assert report.headline is not None
        # Every SLE that had data resolves to real per-SLE scores.
        assert report.sles["coverage"].score is not None
        # Top offenders are real entity ids that resolve to fabricated AP/radio names.
        offender_ids = [
            off["attributed_entity_id"]
            for s in report.sles.values()
            for off in s.top_offenders
            if off.get("attributed_entity_id") is not None
        ]
        assert offender_ids
        names = repo.entities_by_ids(offender_ids)
        assert names, "offenders must resolve to demo entities"
    finally:
        repo.close()


# --------------------------------------------------------------------------- #
# the repository can open it and the API can serve it
# --------------------------------------------------------------------------- #
def test_api_serves_demo_db(demo_db) -> None:
    path, _stats = demo_db
    settings = Settings(
        _env_file=None,
        db_path=path,
        netadmin_api_token="demo-test-token",
        ha=HaConfig(enabled=False),  # never publish to MQTT
        web_dist_path="/nonexistent-demo-spa",
    )
    assert settings.ha.enabled is False
    # store not injected -> the lifespan opens it on the event-loop thread; empty
    # components -> no ingest subsystem starts, so nothing touches a controller.
    app = create_app(settings=settings, components=DaemonComponents())
    auth = {"Authorization": "Bearer demo-test-token"}
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["entities"]["total"] == 94

        issues = client.get("/api/issues", headers=auth)
        assert issues.status_code == 200
        assert issues.json()["count"] == 16

        # The correlation surface serves the grouped Back Porch incident.
        incidents = client.get("/api/incidents", headers=auth)
        assert incidents.status_code == 200
        body = incidents.json()
        assert body["count"] >= 1
        mesh = next(
            (i for i in body["incidents"] if i["root"]["detector_key"] == "wifi.mesh_uplink"),
            None,
        )
        assert mesh is not None, "the mesh incident must be grouped"
        assert mesh["member_count"] == 4  # root + 3 symptoms
        assert mesh["symptom_count"] == 3

        # ARCHITECTURE.md 18.1: reads are open once configured, so an
        # unauthenticated GET read still serves; only a state-changing request
        # requires the token (a tokenless mutation is refused).
        assert client.get("/api/issues").status_code == 200
        assert client.post("/api/issues/1/fix/apply", json={}).status_code == 401

        sle = client.get(f"/api/sle?start={DEFAULT_NOW - DAY}&end={DEFAULT_NOW}", headers=auth)
        assert sle.status_code == 200
        assert 0.82 <= sle.json()["headline"] <= 0.92


# --------------------------------------------------------------------------- #
# safety rails
# --------------------------------------------------------------------------- #
def test_refuses_protected_db_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="protected"):
        seed_demo(tmp_path / "netadmin.db")


def test_now_must_align_to_bucket_grid(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="300"):
        seed_demo(tmp_path / "demo.db", now=DEFAULT_NOW + 1)
