"""Store-level tests for the correlation surface (migration 0004 + repo methods).

Covers the incident/incident_members CRUD, the two reads the engine depends on
(``list_correlatable_issues`` state filtering, ``entity_topology`` shape), the
open-fingerprint uniqueness the partial index enforces, and one end-to-end run of
the real :class:`CorrelationEngine` against real SQLite through
:class:`StoreCorrelationRepository`.
"""

from __future__ import annotations

import pytest

from netadmin.correlate.engine import CorrelationEngine, incident_fingerprint
from netadmin.correlate.models import IncidentState
from netadmin.correlate.store_repository import StoreCorrelationRepository
from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.store.repository import Repository

TS = 1_700_000_000


def _ap(repo: Repository, native: str, name: str) -> int:
    return repo.upsert_entity(Entity(entity_type=EntityType.AP, native_id=native, name=name), ts=TS)


def _client(repo: Repository, native: str, name: str, parent_id: int) -> int:
    return repo.upsert_entity(
        Entity(
            entity_type=EntityType.CLIENT,
            native_id=native,
            name=name,
            parent_id=parent_id,
        ),
        ts=TS,
    )


def _issue(
    repo: Repository,
    *,
    fp: str,
    key: str,
    entity_id: int,
    state: str,
    sev: str = "p3",
    ts: int = TS,
) -> int:
    return repo.insert_issue(
        fingerprint=fp,
        detector_key=key,
        entity_id=entity_id,
        severity=sev,
        state=state,
        first_seen_ts=ts,
        last_seen_ts=ts,
        title=f"{key} on {entity_id}",
    )


# --------------------------------------------------------------------------- #
# Incident CRUD + open-fingerprint uniqueness.
# --------------------------------------------------------------------------- #
def test_incident_insert_get_update(repo: Repository) -> None:
    ap = _ap(repo, "aa:bb:cc:00:00:01", "AP-1")
    root = _issue(repo, fp="root-fp", key="wifi.mesh_uplink", entity_id=ap, state="active")

    inc_id = repo.insert_incident(
        fingerprint="inc-fp",
        root_issue_id=root,
        severity="p2",
        state=IncidentState.OPEN,
        first_seen_ts=TS,
        last_seen_ts=TS,
        title="Weak mesh backhaul on AP-1",
        summary="causing 1 coverage hole",
    )
    row = repo.get_incident(inc_id)
    assert row["title"] == "Weak mesh backhaul on AP-1"
    assert row["state"] == IncidentState.OPEN

    assert repo.get_open_incident("inc-fp")["id"] == inc_id

    repo.update_incident(inc_id, state=IncidentState.RESOLVED, resolved_ts=TS + 100)
    assert repo.get_incident(inc_id)["state"] == IncidentState.RESOLVED
    assert repo.get_open_incident("inc-fp") is None  # no longer open


def test_update_incident_rejects_unknown_column(repo: Repository) -> None:
    ap = _ap(repo, "aa:bb:cc:00:00:01", "AP-1")
    root = _issue(repo, fp="root-fp", key="wifi.mesh_uplink", entity_id=ap, state="active")
    inc_id = repo.insert_incident(
        fingerprint="inc-fp",
        root_issue_id=root,
        severity="p2",
        state=IncidentState.OPEN,
        first_seen_ts=TS,
        last_seen_ts=TS,
        title="t",
    )
    with pytest.raises(ValueError, match="unknown incident column"):
        repo.update_incident(inc_id, bogus="x")


def test_open_fingerprint_unique_index(repo: Repository) -> None:
    ap = _ap(repo, "aa:bb:cc:00:00:01", "AP-1")
    root = _issue(repo, fp="root-fp", key="wifi.mesh_uplink", entity_id=ap, state="active")
    repo.insert_incident(
        fingerprint="dupe",
        root_issue_id=root,
        severity="p2",
        state=IncidentState.OPEN,
        first_seen_ts=TS,
        last_seen_ts=TS,
        title="first",
    )
    # A second *open* incident with the same fingerprint violates idx_incidents_open_fp.
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_incident(
            fingerprint="dupe",
            root_issue_id=root,
            severity="p2",
            state=IncidentState.OPEN,
            first_seen_ts=TS,
            last_seen_ts=TS,
            title="second",
        )


def test_replace_incident_members_and_issue_join(repo: Repository) -> None:
    ap = _ap(repo, "aa:bb:cc:00:00:01", "AP-1")
    c1 = _client(repo, "11:11:11:11:11:11", "C1", ap)
    root = _issue(repo, fp="root-fp", key="wifi.mesh_uplink", entity_id=ap, state="active")
    sym = _issue(repo, fp="sym-fp", key="client.flaky", entity_id=c1, state="active")
    inc_id = repo.insert_incident(
        fingerprint="inc-fp",
        root_issue_id=root,
        severity="p2",
        state=IncidentState.OPEN,
        first_seen_ts=TS,
        last_seen_ts=TS,
        title="t",
    )
    repo.replace_incident_members(
        inc_id,
        [
            {"issue_id": root, "role": "root", "rule": "root", "rationale": "root cause"},
            {
                "issue_id": sym,
                "role": "symptom",
                "rule": "mesh_uplink->client.flaky:parent_child",
                "rationale": "drops on AP-1",
            },
        ],
    )
    members = repo.list_incident_members(inc_id)
    assert [m["role"] for m in members] == ["root", "symptom"]  # root first
    # The issue read-model join resolves each issue to its open incident.
    assert repo.incident_id_for_issue(sym) == inc_id
    assert repo.incident_id_for_issue(root) == inc_id

    # Replacing the set is authoritative: drop the symptom.
    repo.replace_incident_members(
        inc_id,
        [{"issue_id": root, "role": "root", "rule": "root", "rationale": "root cause"}],
    )
    assert [m["issue_id"] for m in repo.list_incident_members(inc_id)] == [root]
    assert repo.incident_id_for_issue(sym) is None

    # A resolved incident no longer answers the issue->incident join.
    repo.update_incident(inc_id, state=IncidentState.RESOLVED, resolved_ts=TS + 1)
    assert repo.incident_id_for_issue(root) is None


# --------------------------------------------------------------------------- #
# The two engine-facing reads.
# --------------------------------------------------------------------------- #
def test_list_correlatable_issues_excludes_pending_and_resolved(repo: Repository) -> None:
    ap = _ap(repo, "aa:bb:cc:00:00:01", "AP-1")
    _issue(repo, fp="a", key="wifi.mesh_uplink", entity_id=ap, state="active")
    _issue(repo, fp="r", key="net.coverage_hole", entity_id=ap, state="resolving")
    _issue(repo, fp="p", key="client.flaky", entity_id=ap, state="pending")
    _issue(repo, fp="d", key="client.dhcp", entity_id=ap, state="resolved")

    keys = {r["detector_key"] for r in repo.list_correlatable_issues()}
    assert keys == {"wifi.mesh_uplink", "net.coverage_hole"}


def test_entity_topology_returns_parentage(repo: Repository) -> None:
    ap = _ap(repo, "aa:bb:cc:00:00:01", "AP-1")
    c1 = _client(repo, "11:11:11:11:11:11", "C1", ap)
    rows = {r["entity_id"]: r for r in repo.entity_topology()}
    assert rows[ap]["parent_id"] is None
    assert rows[c1]["parent_id"] == ap
    assert rows[c1]["entity_type"] == "client"
    assert rows[ap]["name"] == "AP-1"


# --------------------------------------------------------------------------- #
# End-to-end: the real engine against real SQLite.
# --------------------------------------------------------------------------- #
def test_engine_end_to_end_over_real_store(repo: Repository) -> None:
    ap = _ap(repo, "aa:bb:cc:00:00:01", "AP-Garage-Mesh")
    c1 = _client(repo, "11:11:11:11:11:11", "Thermostat", ap)
    c2 = _client(repo, "22:22:22:22:22:22", "Doorbell", ap)
    root = _issue(repo, fp="mesh", key="wifi.mesh_uplink", entity_id=ap, state="active", sev="p2")
    _issue(
        repo, fp="cov", key="net.coverage_hole", entity_id=ap, state="active", sev="p2", ts=TS + 10
    )
    _issue(repo, fp="f1", key="client.flaky", entity_id=c1, state="active", ts=TS + 20)
    _issue(repo, fp="f2", key="client.flaky", entity_id=c2, state="active", ts=TS + 30)

    engine = CorrelationEngine(StoreCorrelationRepository(repo))
    engine.run(TS + 100)

    incidents = repo.list_incidents(open_only=True)
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc["fingerprint"] == incident_fingerprint("mesh")
    assert inc["root_issue_id"] == root
    assert inc["severity"] == "p2"
    assert inc["title"] == "Weak mesh backhaul on AP-Garage-Mesh"

    members = repo.list_incident_members(inc["id"])
    assert len(members) == 4
    assert members[0]["role"] == "root"

    # Idempotent: a second pass keeps the same incident id.
    engine.run(TS + 400)
    again = repo.list_incidents(open_only=True)
    assert len(again) == 1
    assert again[0]["id"] == inc["id"]


def test_engine_resolves_incident_over_real_store(repo: Repository) -> None:
    ap = _ap(repo, "aa:bb:cc:00:00:01", "AP-Shed")
    c1 = _client(repo, "11:11:11:11:11:11", "Sensor", ap)
    flaky = _issue(repo, fp="f1", key="client.flaky", entity_id=c1, state="active")

    engine = CorrelationEngine(StoreCorrelationRepository(repo))
    engine.run(TS + 100)
    inc_id = repo.list_incidents(open_only=True)[0]["id"]

    # The issue resolves; it leaves the correlatable set.
    repo.update_issue(flaky, state="resolved", resolved_ts=TS + 200)
    engine.run(TS + 300)

    assert repo.list_incidents(open_only=True) == []
    closed = repo.get_incident(inc_id)
    assert closed["state"] == IncidentState.RESOLVED
    assert closed["resolved_ts"] == TS + 300
