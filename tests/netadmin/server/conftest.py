"""Fixtures for the server test suite: a seeded store, an app, and fakes.

The router tests drive the ASGI app over ``httpx.ASGITransport`` (no network, no
uvicorn) against a real migrated SQLite store seeded with a couple of issues and
their event trails. The lifespan tests inject fake subsystems to assert
start/shutdown ordering without a live controller.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from netadmin.config import Settings
from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.server.main import DaemonComponents, create_app
from netadmin.store.repository import Repository, SampleReading

BASE_TS = 1_700_000_000


@pytest.fixture
def settings(tmp_db_path: Path) -> Settings:
    return Settings(_env_file=None, db_path=tmp_db_path, site_id="default")


@pytest.fixture
def seeded_store(settings: Settings) -> Repository:
    """A migrated store with two entities and two issues (one with a trail)."""
    store = Repository.open(settings.db_path, site_id=settings.site_id)

    ap = store.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id="aa:bb:cc:00:00:01", name="ap-office"),
        ts=BASE_TS,
    )
    store.upsert_entity(
        Entity(entity_type=EntityType.SWITCH, native_id="aa:bb:cc:00:00:02", name="sw-core"),
        ts=BASE_TS,
    )

    issue_id = store.insert_issue(
        fingerprint="fp-active",
        detector_key="wired.bad_cable",
        severity="p2",
        state="active",
        first_seen_ts=BASE_TS,
        last_seen_ts=BASE_TS + 600,
        title="rx_errors climbing on port 5",
        entity_id=ap,
        evidence={"rx_errors_per_min": 42},
    )
    store.record_issue_event(issue_id, "detected", ts=BASE_TS, detail={"m": 1})
    store.record_issue_event(issue_id, "escalated", ts=BASE_TS + 600, detail={"m": 3})

    store.insert_issue(
        fingerprint="fp-pending",
        detector_key="wifi.sticky_client",
        severity="p3",
        state="pending",
        first_seen_ts=BASE_TS + 100,
        last_seen_ts=BASE_TS + 100,
        title="sticky client on ap-office",
        entity_id=ap,
    )

    yield store
    store.close()


@pytest.fixture
def app(settings: Settings, seeded_store: Repository) -> Any:
    """An app with the store injected and no ingest subsystems (empty bundle)."""
    return create_app(settings=settings, store=seeded_store, components=DaemonComponents())


@pytest.fixture
def rich_store(settings: Settings) -> Repository:
    """A store seeded for the inventory / metrics / events / changes surfaces.

    Topology: one AP (with two radio children), one switch (with one port child),
    one gateway, and two clients (one parented to the AP). Plus a metric series on
    the AP, firmware/state history, a roam event, a change-ledger row, and issues
    (active + resolved on the AP, one on a client).
    """
    store = Repository.open(settings.db_path, site_id=settings.site_id)

    ap = store.upsert_entity(
        Entity(
            entity_type=EntityType.AP,
            native_id="aa:bb:cc:00:00:01",
            name="ap-office",
            model="U6-Pro",
            meta={"is_wired": True},
        ),
        ts=BASE_TS,
    )
    radio_ng = store.upsert_entity(
        Entity(entity_type=EntityType.RADIO, native_id="aa:bb:cc:00:00:01:ng", parent_id=ap),
        ts=BASE_TS,
    )
    radio_na = store.upsert_entity(
        Entity(entity_type=EntityType.RADIO, native_id="aa:bb:cc:00:00:01:na", parent_id=ap),
        ts=BASE_TS,
    )
    sw = store.upsert_entity(
        Entity(entity_type=EntityType.SWITCH, native_id="aa:bb:cc:00:00:02", name="sw-core"),
        ts=BASE_TS,
    )
    store.upsert_entity(
        Entity(entity_type=EntityType.PORT, native_id="aa:bb:cc:00:00:02:5", parent_id=sw),
        ts=BASE_TS,
    )
    store.upsert_entity(
        Entity(entity_type=EntityType.GATEWAY, native_id="aa:bb:cc:00:00:03", name="gw"),
        ts=BASE_TS,
    )
    client_wifi = store.upsert_entity(
        Entity(
            entity_type=EntityType.CLIENT,
            native_id="11:22:33:44:55:01",
            name="iPhone",
            parent_id=ap,
        ),
        ts=BASE_TS,
    )
    store.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id="11:22:33:44:55:04", name="Desktop"),
        ts=BASE_TS,
    )

    # A gauge metric on the AP, one reading per minute (enough to downsample).
    store.record_samples(
        SampleReading(entity_id=ap, metric="cpu", ts=BASE_TS + 60 * i, value=float(10 + i))
        for i in range(12)
    )
    # Current radio state so the child rollup has something to show.
    store.record_state_change(radio_ng, "channel", "6", ts=BASE_TS)
    store.record_state_change(radio_na, "channel", "36", ts=BASE_TS)

    # Discrete state history on the AP (firmware upgrade, up/down flap).
    store.record_state_change(ap, "firmware", "6.6.55", ts=BASE_TS)
    store.record_state_change(ap, "firmware", "6.6.77", ts=BASE_TS + 300)
    store.record_state_change(ap, "state", "up", ts=BASE_TS)

    # A roam event: iPhone roamed away from ap-office.
    store.record_event(
        ts=BASE_TS + 120,
        key="EVT_WU_Roam",
        entity_id=client_wifi,
        related_entity_id=ap,
        native_id="evt-roam-1",
        msg="iPhone roamed",
        data={"from": "ap-office"},
    )

    # A config change on the AP (applied, not reverted).
    store.insert_change(
        action="set_channel",
        before={"channel": 6},
        after={"channel": 11},
        status="applied",
        ts=BASE_TS + 200,
        entity_id=ap,
    )

    # Issues: active (with confounders) + resolved on the AP, one on a client.
    active = store.insert_issue(
        fingerprint="fp-active",
        detector_key="wired.bad_cable",
        severity="p2",
        state="active",
        first_seen_ts=BASE_TS,
        last_seen_ts=BASE_TS + 600,
        title="rx_errors climbing on port 5",
        entity_id=ap,
        evidence={"rx_errors_per_min": 42, "confounders_checked": ["poe_flap", "duplex_mismatch"]},
    )
    store.record_issue_event(active, "detected", ts=BASE_TS, detail={"m": 1})
    store.record_issue_event(active, "escalated", ts=BASE_TS + 600, detail={"m": 3})
    store.insert_issue(
        fingerprint="fp-resolved",
        detector_key="wifi.dfs_radar",
        severity="p3",
        state="resolved",
        first_seen_ts=BASE_TS - 3600,
        last_seen_ts=BASE_TS - 1800,
        title="DFS radar hit",
        entity_id=ap,
        resolved_ts=BASE_TS - 1800,
    )
    store.insert_issue(
        fingerprint="fp-client",
        detector_key="client.flaky",
        severity="p3",
        state="active",
        first_seen_ts=BASE_TS,
        last_seen_ts=BASE_TS + 100,
        title="flaky client",
        entity_id=client_wifi,
    )

    store.ap_id = ap  # type: ignore[attr-defined]
    store.client_id = client_wifi  # type: ignore[attr-defined]
    store.switch_id = sw  # type: ignore[attr-defined]
    yield store
    store.close()


@pytest.fixture
def rich_app(settings: Settings, rich_store: Repository) -> Any:
    """An app over :func:`rich_store`, no ingest subsystems."""
    return create_app(settings=settings, store=rich_store, components=DaemonComponents())


class FakeScheduler:
    """Stands in for the APScheduler AsyncIOScheduler in lifespan tests."""

    def __init__(self) -> None:
        self.started = False
        self.shutdown_called = False
        self.shutdown_wait: Any = None

    def start(self) -> None:
        self.started = True

    def shutdown(self, wait: bool = False) -> None:
        self.shutdown_called = True
        self.shutdown_wait = wait

    def get_jobs(self) -> list[Any]:
        return []


class FakeSupervisor:
    """Async start/stop subsystem (WS supervisor / probes) with a state attr."""

    def __init__(self, state: str = "connected") -> None:
        self.started = False
        self.stopped = False
        self.state = state

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def now() -> int:
    return int(time.time())
