"""ASGI-transport tests for the incidents surface (section 17).

Builds a store with a real correlatable cluster (a mesh_uplink root + a
coverage_hole symptom on the same AP), runs the actual correlation engine to
group them into an incident, then drives ``GET /api/incidents`` / ``/{id}`` and
the incident-aware issue read model over ``httpx.ASGITransport``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from netadmin.correlate.engine import CorrelationEngine
from netadmin.correlate.store_repository import StoreCorrelationRepository
from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.server.main import DaemonComponents, create_app
from netadmin.store.repository import Repository

pytestmark = pytest.mark.asyncio

BASE = 1_700_000_000


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
    # The mesh cluster (2 members) + the standalone ISP issue (incident-of-one).
    assert body["count"] == 2
    mesh = next(i for i in body["incidents"] if i["root"]["detector_key"] == "wifi.mesh_uplink")
    assert mesh["member_count"] == 2
    assert mesh["symptom_count"] == 1
    assert mesh["root"]["entity"]["name"] == "Back Porch"
    assert mesh["summary"]  # a plain-language causal line
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
        # The symptom carries incident_id + role=symptom.
        sym = by_id[incident_store.symptom_id]
        assert sym["incident_id"] is not None
        assert sym["incident_role"] == "symptom"
        # The root carries role=root.
        assert by_id[incident_store.root_id]["incident_role"] == "root"

        # The detail view exposes the "Part of: <incident>" object.
        detail = (await c.get(f"/api/issues/{incident_store.symptom_id}")).json()
        assert detail["incident"] is not None
        assert detail["incident"]["role"] == "symptom"
        assert detail["incident"]["title"]
        assert detail["issue"]["incident_id"] == detail["incident"]["id"]
