"""The issues list's impact figure: what an issue cost, or why that is unknown.

Every case here exists to defend one distinction: a **null** ``fail_minutes``
("not measured") is not a **zero** one ("measured, nothing failed"). The issue
list column reads directly off this block, so collapsing the two would let an
unmeasured outage render as harmless.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest

from netadmin.config import Settings
from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.server.main import DaemonComponents, create_app
from netadmin.store.repository import Repository

pytestmark = pytest.mark.asyncio

BUCKET_S = 300


async def _client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def now() -> int:
    """A bucket-aligned "now" so seeded buckets land inside the 24 h window."""
    return (int(time.time()) // BUCKET_S) * BUCKET_S


@pytest.fixture
def impact_store(tmp_db_path: Path, now: int) -> Iterator[Repository]:
    """An AP with a radio, a switch with a port, a client — and one issue each.

    Recent enough to sit inside the impact window: the store is seeded relative
    to ``now`` rather than a fixed epoch, because the whole point of the figure
    is that it looks at the last 24 hours.
    """
    store = Repository.open(tmp_db_path, site_id="default")
    started = now - 3 * 3600

    ap = store.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id="aa:bb:cc:00:00:01", name="ap-office"),
        ts=started,
    )
    sw = store.upsert_entity(
        Entity(entity_type=EntityType.SWITCH, native_id="aa:bb:cc:00:00:02", name="sw-core"),
        ts=started,
    )
    port = store.upsert_entity(
        Entity(entity_type=EntityType.PORT, native_id="aa:bb:cc:00:00:02:5", parent_id=sw),
        ts=started,
    )
    client = store.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id="11:22:33:44:55:01", name="iPhone"),
        ts=started,
    )

    def issue(fp: str, entity_id: int | None, **kw: Any) -> int:
        return store.insert_issue(
            fingerprint=fp,
            detector_key="wifi.coverage_hole",
            severity="p2",
            state=kw.pop("state", "active"),
            first_seen_ts=kw.pop("first_seen_ts", started),
            last_seen_ts=now,
            title=f"issue {fp}",
            entity_id=entity_id,
            **kw,
        )

    issue("fp-ap", ap)
    issue("fp-port", port)
    issue("fp-client", client)
    issue("fp-sitewide", None)
    # Opened, and closed again, more than a day ago: nothing of its life is
    # inside the impact window.
    issue(
        "fp-old",
        ap,
        state="resolved",
        first_seen_ts=now - 5 * 86_400,
        resolved_ts=now - 4 * 86_400,
    )
    # Opened five minutes ago, on an AP that has been failing all day — the
    # clipping case: it must not inherit the AP's earlier grief.
    issue("fp-fresh", ap, first_seen_ts=now - BUCKET_S)

    # Failed minutes: two buckets blamed on the AP (one inside the fresh issue's
    # life, one two hours before it opened), and one the client owns itself.
    store.upsert_sle_minute(
        bucket_ts=now - 2 * 3600,
        sle="coverage",
        classifier="weak_rssi",
        entity_id=client,
        minutes=4.0,
        attributed_entity_id=ap,
    )
    store.upsert_sle_minute(
        bucket_ts=now - BUCKET_S,
        sle="capacity",
        classifier="airtime",
        entity_id=client,
        minutes=2.5,
        attributed_entity_id=ap,
    )
    # An ok bucket, to prove only failed minutes are counted.
    store.upsert_sle_minute(
        bucket_ts=now - BUCKET_S,
        sle="coverage",
        classifier="ok",
        entity_id=client,
        minutes=5.0,
        attributed_entity_id=ap,
    )
    yield store
    store.close()


@pytest.fixture
def impact_app(tmp_db_path: Path, impact_store: Repository) -> Any:
    settings = Settings(_env_file=None, db_path=tmp_db_path, site_id="default")
    return create_app(settings=settings, store=impact_store, components=DaemonComponents())


async def _by_fingerprint(app: object) -> dict[str, dict[str, Any]]:
    async with await _client(app) as c:
        body = (await c.get("/api/issues")).json()
    return {i["fingerprint"]: i for i in body["issues"]}


async def test_infrastructure_issue_reports_attributed_minutes(impact_app: object) -> None:
    issues = await _by_fingerprint(impact_app)
    impact = issues["fp-ap"]["impact"]
    assert impact["measured"] is True
    assert impact["basis"] == "attributed"
    # 4.0 + 2.5 failed; the 5.0 ok minutes are not grief.
    assert impact["fail_minutes"] == 6.5
    assert impact["window_s"] == 86_400


async def test_client_issue_reports_its_own_failed_minutes(impact_app: object) -> None:
    issues = await _by_fingerprint(impact_app)
    impact = issues["fp-client"]["impact"]
    assert impact["basis"] == "own"
    assert impact["measured"] is True
    assert impact["fail_minutes"] == 6.5


async def test_impact_is_clipped_to_the_issues_own_life(impact_app: object) -> None:
    issues = await _by_fingerprint(impact_app)
    # The AP failed 6.5 minutes today, but only 2.5 of them after this issue
    # opened. An issue is charged with what happened while it was open.
    assert issues["fp-fresh"]["impact"]["fail_minutes"] == 2.5


async def test_port_issue_is_not_measured_rather_than_zero(impact_app: object) -> None:
    issues = await _by_fingerprint(impact_app)
    impact = issues["fp-port"]["impact"]
    assert impact["basis"] is None
    assert impact["measured"] is False
    assert impact["fail_minutes"] is None


async def test_sitewide_issue_has_no_entity_to_charge(impact_app: object) -> None:
    issues = await _by_fingerprint(impact_app)
    impact = issues["fp-sitewide"]["impact"]
    assert impact["basis"] is None
    assert impact["fail_minutes"] is None


async def test_issue_resolved_before_the_window_is_not_measured(impact_app: object) -> None:
    issues = await _by_fingerprint(impact_app)
    impact = issues["fp-old"]["impact"]
    # Its entity is scoreable, so the basis stands — but none of its life is in
    # the window, so there is nothing to report and it must not read as zero.
    assert impact["basis"] == "attributed"
    assert impact["measured"] is False
    assert impact["fail_minutes"] is None


async def test_no_sle_minutes_at_all_is_not_measured(app: object) -> None:
    """The default seeded store has issues but the SLE engine never ran."""
    async with await _client(app) as c:
        body = (await c.get("/api/issues")).json()
    for issue in body["issues"]:
        assert issue["impact"]["measured"] is False
        assert issue["impact"]["fail_minutes"] is None


async def test_detail_carries_the_same_impact_block(impact_app: object) -> None:
    issues = await _by_fingerprint(impact_app)
    issue_id = issues["fp-ap"]["id"]
    async with await _client(impact_app) as c:
        detail = (await c.get(f"/api/issues/{issue_id}")).json()
    assert detail["issue"]["impact"] == issues["fp-ap"]["impact"]


async def test_measured_zero_is_distinct_from_unmeasured(tmp_db_path: Path, now: int) -> None:
    """An entity the engine scored, but pinned nothing on, reports a real zero."""
    store = Repository.open(tmp_db_path, site_id="default")
    ap = store.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id="aa:bb:cc:00:00:01", name="ap-office"),
        ts=now - 3600,
    )
    quiet_ap = store.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id="aa:bb:cc:00:00:09", name="ap-spare"),
        ts=now - 3600,
    )
    client = store.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id="11:22:33:44:55:01", name="iPhone"),
        ts=now - 3600,
    )
    store.insert_issue(
        fingerprint="fp-quiet",
        detector_key="wifi.coverage_hole",
        severity="p3",
        state="active",
        first_seen_ts=now - 3600,
        last_seen_ts=now,
        title="issue on a blameless AP",
        entity_id=quiet_ap,
    )
    store.upsert_sle_minute(
        bucket_ts=now - BUCKET_S,
        sle="coverage",
        classifier="weak_rssi",
        entity_id=client,
        minutes=3.0,
        attributed_entity_id=ap,
    )
    settings = Settings(_env_file=None, db_path=tmp_db_path, site_id="default")
    app = create_app(settings=settings, store=store, components=DaemonComponents())
    try:
        async with await _client(app) as c:
            body = (await c.get("/api/issues")).json()
    finally:
        store.close()
    impact = body["issues"][0]["impact"]
    assert impact["measured"] is True
    assert impact["fail_minutes"] == 0.0
