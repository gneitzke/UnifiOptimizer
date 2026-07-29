"""ASGI-transport tests for the incidents surface (section 17).

Builds a store with a real correlatable cluster (a mesh_uplink root + a
coverage_hole symptom on the same AP), runs the actual correlation engine to
group them into an incident, then drives ``GET /api/incidents`` / ``/{id}`` and
the incident-aware issue read model over ``httpx.ASGITransport``.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import httpx
import pytest

from netadmin.analytics.offenders import rank_offenders
from netadmin.correlate.engine import CorrelationEngine
from netadmin.correlate.store_repository import StoreCorrelationRepository
from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.server.main import DaemonComponents, create_app
from netadmin.sle.scores import sle_scores
from netadmin.store.repository import Repository

pytestmark = pytest.mark.asyncio

BASE = 1_700_000_000
_WIN = (BASE - 60, BASE + 3600)
_DEVICE_TYPES = (EntityType.AP.value, EntityType.SWITCH.value, EntityType.GATEWAY.value)


def _seed_incident_store(settings, tmp_db_path) -> Repository:
    """A store with a mesh_uplink root + coverage_hole symptom on one AP, plus an
    unrelated standalone issue — then correlated into incidents."""
    store = Repository.open(tmp_db_path, site_id=settings.site_id)
    ap = store.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id="02:00:00:00:00:01", name="Back Porch"),
        ts=BASE,
    )
    gw = store.upsert_entity(
        Entity(entity_type=EntityType.GATEWAY, native_id="02:00:00:00:00:09", name="Gateway"),
        ts=BASE,
    )

    root = store.insert_issue(
        fingerprint="mesh-root",
        detector_key="wifi.mesh_uplink",
        severity="p2",
        state="active",
        first_seen_ts=BASE,
        last_seen_ts=BASE + 600,
        title="Weak mesh backhaul on Back Porch",
        entity_id=ap,
        evidence={"uplink_rssi_dbm": -78},
    )
    symptom = store.insert_issue(
        fingerprint="cov-hole",
        detector_key="net.coverage_hole",
        severity="p2",
        state="active",
        first_seen_ts=BASE + 300,  # after the root -> temporal guard admits
        last_seen_ts=BASE + 600,
        title="Coverage hole on Back Porch",
        entity_id=ap,
    )
    standalone = store.insert_issue(
        fingerprint="isp",
        detector_key="wan.isp_degraded",
        severity="p2",
        state="active",
        first_seen_ts=BASE,
        last_seen_ts=BASE + 600,
        title="Degraded ISP uplink",
        entity_id=gw,
    )
    store.record_issue_event(root, "detected", ts=BASE)

    CorrelationEngine(StoreCorrelationRepository(store)).run(BASE + 900)
    store.root_id = root  # type: ignore[attr-defined]
    store.symptom_id = symptom  # type: ignore[attr-defined]
    store.standalone_id = standalone  # type: ignore[attr-defined]
    return store


@pytest.fixture
def incident_store(settings, tmp_db_path) -> Repository:
    store = _seed_incident_store(settings, tmp_db_path)
    yield store
    store.close()


@pytest.fixture
def incident_app(settings, incident_store) -> Any:
    return create_app(settings=settings, store=incident_store, components=DaemonComponents())


async def _client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_list_incidents_groups_and_ranks(incident_app) -> None:
    async with await _client(incident_app) as c:
        resp = await c.get("/api/incidents")
    assert resp.status_code == 200
    body = resp.json()
    # Genuine groups only by default (Gitea #21): the mesh cluster (2 members),
    # not the standalone ISP issue (an incident-of-one).
    assert body["count"] == 1
    mesh = next(i for i in body["incidents"] if i["root"]["detector_key"] == "wifi.mesh_uplink")
    assert mesh["member_count"] == 2
    assert mesh["symptom_count"] == 1
    assert mesh["root"]["entity"]["name"] == "Back Porch"
    assert mesh["summary"]  # a plain-language causal line


async def test_list_incidents_include_singletons_restores_the_uniform_view(
    incident_app,
) -> None:
    async with await _client(incident_app) as c:
        resp = await c.get("/api/incidents?include_singletons=true")
    assert resp.status_code == 200
    body = resp.json()
    # The mesh cluster (2 members) + the standalone ISP issue (incident-of-one).
    assert body["count"] == 2
    standalone = next(
        i for i in body["incidents"] if i["root"]["detector_key"] == "wan.isp_degraded"
    )
    assert standalone["member_count"] == 1
    assert standalone["symptom_count"] == 0


async def test_incident_detail_root_symptoms_and_hooks(incident_app, incident_store) -> None:
    async with await _client(incident_app) as c:
        listing = (await c.get("/api/incidents")).json()
        mesh_id = next(
            i["id"] for i in listing["incidents"] if i["root"]["detector_key"] == "wifi.mesh_uplink"
        )
        resp = await c.get(f"/api/incidents/{mesh_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["root"]["issue"]["detector_key"] == "wifi.mesh_uplink"
    assert body["root"]["role"] == "root"
    assert len(body["symptoms"]) == 1
    sym = body["symptoms"][0]
    assert sym["issue"]["detector_key"] == "net.coverage_hole"
    assert sym["role"] == "symptom"
    assert sym["rationale"]  # every link records a why (conservatism)
    assert sym["rule"].startswith("mesh_uplink->coverage_hole")
    # The single recommended fix + the investigation hook both point at the root.
    assert body["recommended_fix"]["issue_id"] == incident_store.root_id
    assert body["investigation"]["issue_id"] == incident_store.root_id


async def test_incident_detail_404(incident_app) -> None:
    async with await _client(incident_app) as c:
        resp = await c.get("/api/incidents/99999")
    assert resp.status_code == 404


async def test_issue_read_model_carries_incident(incident_app, incident_store) -> None:
    async with await _client(incident_app) as c:
        listing = (await c.get("/api/issues")).json()
        by_id = {i["id"]: i for i in listing["issues"]}
        # The symptom carries incident_id + role=symptom, and a genuine
        # incident_brief (Gitea #21: the mesh group has 2 members) so the
        # Issues list can group it inline with no second fetch.
        sym = by_id[incident_store.symptom_id]
        assert sym["incident_id"] is not None
        assert sym["incident_role"] == "symptom"
        assert sym["incident_brief"] is not None
        assert sym["incident_brief"]["symptom_count"] == 1
        # The root carries role=root and the same brief.
        root_row = by_id[incident_store.root_id]
        assert root_row["incident_role"] == "root"
        assert root_row["incident_brief"]["id"] == sym["incident_brief"]["id"]
        # The standalone issue is in an incident-of-one: no genuine brief.
        assert by_id[incident_store.standalone_id]["incident_id"] is not None
        assert by_id[incident_store.standalone_id]["incident_brief"] is None

        # The detail view exposes the "Part of: <incident>" object.
        detail = (await c.get(f"/api/issues/{incident_store.symptom_id}")).json()
        assert detail["incident"] is not None
        assert detail["incident"]["role"] == "symptom"
        assert detail["incident"]["title"]
        assert detail["incident"]["symptom_count"] == 1
        assert detail["issue"]["incident_id"] == detail["incident"]["id"]

        # The standalone issue's incident is a genuine incident-of-one: it has
        # an incident (the engine's bookkeeping row) but symptom_count == 0, so
        # the client renders no "Part of" line for it.
        solo_detail = (await c.get(f"/api/issues/{incident_store.standalone_id}")).json()
        assert solo_detail["incident"] is not None
        assert solo_detail["incident"]["symptom_count"] == 0


def _suppress(store: Repository, *issue_ids: int) -> None:
    from netadmin.issues.engine import IssueEngine
    from netadmin.issues.store_repository import StoreIssueRepository

    engine = IssueEngine(StoreIssueRepository(store))
    import time as _time

    for iid in issue_ids:
        engine.suppress(int(iid), int(_time.time()))


async def test_fully_suppressed_singleton_drops_out_and_is_disclosed(
    incident_app, incident_store
) -> None:
    """The standalone issue's only member suppressed -> its incident-of-one leaves
    the "Needs attention" view, and the drop is disclosed, never silent."""
    _suppress(incident_store, incident_store.standalone_id)
    async with await _client(incident_app) as c:
        body = (await c.get("/api/incidents?include_singletons=true")).json()
    detectors = {i["root"]["detector_key"] for i in body["incidents"]}
    assert "wan.isp_degraded" not in detectors  # fully suppressed -> dropped
    assert "wifi.mesh_uplink" in detectors  # the live cluster stays
    assert body["suppressed_excluded"] == 1


async def test_partially_suppressed_incident_stays(incident_app, incident_store) -> None:
    """A suppressed root with a live symptom keeps the incident: the symptom is an
    unanswered ask (correlation rule)."""
    _suppress(incident_store, incident_store.root_id)  # root only, symptom live
    async with await _client(incident_app) as c:
        body = (await c.get("/api/incidents")).json()
    assert body["count"] == 1  # still surfaced
    assert body["suppressed_excluded"] == 0


async def test_incident_drops_only_when_all_members_suppressed(
    incident_app, incident_store
) -> None:
    _suppress(incident_store, incident_store.root_id, incident_store.symptom_id)
    async with await _client(incident_app) as c:
        body = (await c.get("/api/incidents")).json()
    assert body["count"] == 0
    assert body["suppressed_excluded"] == 1


# --- bulk incident suppress / unsuppress (Gitea #50) ------------------------- #


async def _mesh_incident_id(c: httpx.AsyncClient) -> int:
    listing = (await c.get("/api/incidents")).json()
    return next(
        i["id"] for i in listing["incidents"] if i["root"]["detector_key"] == "wifi.mesh_uplink"
    )


async def test_bulk_suppress_suppresses_every_member_with_incident_source(
    incident_app, incident_store
) -> None:
    """One POST parks the whole incident: the root and the symptom both gain a
    ``suppressed`` event stamped ``source="incident"`` (Gitea #50)."""
    async with await _client(incident_app) as c:
        mesh_id = await _mesh_incident_id(c)
        resp = await c.post(f"/api/incidents/{mesh_id}/suppress", json={})
        assert resp.status_code == 200
        assert resp.json() == {"incident_id": mesh_id, "count": 2}  # root + symptom

        for iid in (incident_store.root_id, incident_store.symptom_id):
            detail = (await c.get(f"/api/issues/{iid}")).json()
            assert detail["issue"]["suppressed_ts"] is not None
            suppressed = [e for e in detail["events"] if e["kind"] == "suppressed"]
            assert suppressed and suppressed[-1]["detail"]["source"] == "incident"


async def test_bulk_suppress_honors_until_ts(incident_app, incident_store) -> None:
    until = BASE + 100_000
    async with await _client(incident_app) as c:
        mesh_id = await _mesh_incident_id(c)
        await c.post(f"/api/incidents/{mesh_id}/suppress", json={"until_ts": until})
        for iid in (incident_store.root_id, incident_store.symptom_id):
            detail = (await c.get(f"/api/issues/{iid}")).json()
            assert detail["issue"]["suppress_until_ts"] == until


async def test_bulk_unsuppress_lifts_every_member(incident_app, incident_store) -> None:
    async with await _client(incident_app) as c:
        mesh_id = await _mesh_incident_id(c)
        await c.post(f"/api/incidents/{mesh_id}/suppress", json={})
        resp = await c.post(f"/api/incidents/{mesh_id}/unsuppress")
        assert resp.status_code == 200
        assert resp.json() == {"incident_id": mesh_id, "count": 2}
        for iid in (incident_store.root_id, incident_store.symptom_id):
            detail = (await c.get(f"/api/issues/{iid}")).json()
            assert detail["issue"]["suppressed_ts"] is None
            assert "unsuppressed" in [e["kind"] for e in detail["events"]]


async def test_bulk_suppress_unknown_incident_404(incident_app) -> None:
    async with await _client(incident_app) as c:
        suppress = await c.post("/api/incidents/999999/suppress", json={})
        unsuppress = await c.post("/api/incidents/999999/unsuppress")
    assert suppress.status_code == 404
    assert unsuppress.status_code == 404


def _measured(store: Repository) -> tuple[Any, list[Any]]:
    """Every measured surface for the incident's AP, as comparable plain data."""
    report = sle_scores(store, *_WIN)
    offenders = rank_offenders(store, _DEVICE_TYPES, *_WIN)
    return dataclasses.asdict(report), [dataclasses.asdict(o) for o in offenders]


async def test_bulk_suppress_moves_no_measured_number(incident_app, incident_store) -> None:
    """The invariant, for the bulk path (Gitea #50): suppressing a whole incident
    parks attention only. The health score, per-SLE scores, and the offenders
    burden — burden's severity-weighted open-issue channel included — must not
    move, because suppression does not un-suffer the client-minutes the incident
    cost. Sibling of tests/netadmin/sle/test_suppression_invariant.py, exercising
    the HTTP bulk route rather than a single engine.suppress call."""
    # Seed measured grief attributed to the incident's AP so the offenders burden
    # (and its open-issue channel, which the two members feed) has a value to pin.
    ap_id = int(incident_store.get_issue(incident_store.root_id)["entity_id"])
    phone = incident_store.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id="11:22:33:44:55:99", name="phone"), ts=BASE
    )
    incident_store.upsert_sle_minute(
        bucket_ts=BASE,
        sle="coverage",
        classifier="weak_signal",
        entity_id=phone,
        attributed_entity_id=ap_id,
        minutes=500.0,
    )
    incident_store.upsert_sle_minute(
        bucket_ts=BASE,
        sle="coverage",
        classifier="ok",
        entity_id=phone,
        attributed_entity_id=ap_id,
        minutes=100.0,
    )

    before_report, before_offenders = _measured(incident_store)
    # Guard against a vacuous test: the AP must actually carry the open issues that
    # feed the burden channel, so suppressing them is a real chance to move it.
    ap_offender = next(o for o in before_offenders if o["entity_id"] == ap_id)
    assert ap_offender["issue_counts"]["total"] == 2  # root + symptom

    async with await _client(incident_app) as c:
        mesh_id = await _mesh_incident_id(c)
        resp = await c.post(f"/api/incidents/{mesh_id}/suppress", json={})
        assert resp.json()["count"] == 2
        # The suppression took: both members read as suppressed now.
        for iid in (incident_store.root_id, incident_store.symptom_id):
            assert (await c.get(f"/api/issues/{iid}")).json()["issue"]["suppressed_ts"] is not None

    after_report, after_offenders = _measured(incident_store)

    # Byte-identical: nothing measured moved.
    assert after_report == before_report
    assert after_offenders == before_offenders
