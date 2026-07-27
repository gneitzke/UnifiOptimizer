"""The issues list's impact block: what an issue cost, on two axes that never merge.

Two distinctions are defended here, and every case exists to protect one of them.

**Axis** (Gitea #36). ``sle_minutes`` holds two different units: coverage,
roaming, capacity, connect and wan rows are minutes a real *client* spent
degraded, while ``infra`` rows are minutes a *device* was down. Summing them
produced a figure that credited a switch outage with client-minutes nobody
experienced. The payload keeps them in separate blocks with no combined field,
so the sum cannot be written back in by accident.

**Measured** — a **null** figure ("not measured") is not a **zero** one
("measured, nothing failed"), and that holds per axis. The list column reads
directly off this block, so collapsing either distinction would let an
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
    """An AP with a radio, a switch with a port, two clients — and one issue each.

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
    laptop = store.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id="11:22:33:44:55:02", name="laptop"),
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
    issue("fp-switch", sw)
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

    # Client-axis minutes: two buckets blamed on the AP (one inside the fresh
    # issue's life, one two hours before it opened), across two clients.
    store.upsert_sle_minute(
        bucket_ts=now - 2 * 3600,
        sle="coverage",
        classifier="weak_signal",
        entity_id=client,
        minutes=4.0,
        attributed_entity_id=ap,
    )
    store.upsert_sle_minute(
        bucket_ts=now - BUCKET_S,
        sle="capacity",
        classifier="wifi_interference",
        entity_id=laptop,
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
    # Device-axis minutes: the switch was down. These are the minutes that used
    # to be added into the client total. Nobody lived through them as a client.
    store.upsert_sle_minute(
        bucket_ts=now - 2 * 3600,
        sle="infra",
        classifier="switch_down",
        entity_id=sw,
        minutes=42.0,
        attributed_entity_id=sw,
    )
    # The AP stayed up the whole window: an ok infra row, so the infra axis is
    # measured for the AP and honestly reports zero downtime.
    store.upsert_sle_minute(
        bucket_ts=now - 2 * 3600,
        sle="infra",
        classifier="ok",
        entity_id=ap,
        minutes=300.0,
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


# --------------------------------------------------------------------------- #
# The axis split (Gitea #36)
# --------------------------------------------------------------------------- #


async def test_device_downtime_is_never_added_to_client_minutes(impact_app: object) -> None:
    """The bug itself: a switch down 42 minutes is not 42 client-minutes.

    The switch owns 42 infra down-minutes and no client has any minutes pinned
    on it, so the client axis reports a measured zero across zero clients while
    the infra axis reports the downtime. There is no field that adds them.
    """
    impact = (await _by_fingerprint(impact_app))["fp-switch"]["impact"]
    assert impact["measured"] is True
    assert impact["infra"] == {"measured": True, "down_minutes": 42.0, "entity_type": "switch"}
    assert impact["client"]["measured"] is True
    assert impact["client"]["clients"] == 0
    assert impact["client"]["fail_minutes"] == 0.0
    assert "fail_minutes" not in impact  # no combined total to reach for


async def test_infrastructure_issue_reports_attributed_client_minutes(impact_app: object) -> None:
    issues = await _by_fingerprint(impact_app)
    impact = issues["fp-ap"]["impact"]
    assert impact["measured"] is True
    assert impact["basis"] == "attributed"
    # 4.0 + 2.5 failed across two clients; the 5.0 ok minutes are not grief.
    assert impact["client"]["fail_minutes"] == 6.5
    assert impact["client"]["clients"] == 2
    assert impact["window_s"] == 86_400


async def test_client_count_is_the_headline_not_the_minute_total(impact_app: object) -> None:
    """Harm is "how many clients, for how long" — the count must be published."""
    client = (await _by_fingerprint(impact_app))["fp-ap"]["impact"]["client"]
    # Two clients had failed minutes; both are among the clients the engine
    # judged in the window, which is the denominator the figure is quoted against.
    assert client["clients"] == 2
    assert client["clients_in_window"] == 2


async def test_ap_that_stayed_up_reports_a_measured_zero_downtime(impact_app: object) -> None:
    """A real zero on the infra axis is a zero, not a dash — it was judged."""
    impact = (await _by_fingerprint(impact_app))["fp-ap"]["impact"]
    assert impact["infra"] == {"measured": True, "down_minutes": 0.0, "entity_type": "ap"}


async def test_client_issue_has_no_infra_axis_at_all(impact_app: object) -> None:
    """The infra SLE never walks a client's state timeline, so there is nothing
    to report — and "down 0 min" would be a claim nobody measured."""
    impact = (await _by_fingerprint(impact_app))["fp-client"]["impact"]
    assert impact["basis"] == "own"
    assert impact["infra"] == {"measured": False, "down_minutes": None, "entity_type": None}


async def test_client_issue_reports_its_own_failed_minutes(impact_app: object) -> None:
    issues = await _by_fingerprint(impact_app)
    impact = issues["fp-client"]["impact"]
    assert impact["basis"] == "own"
    assert impact["measured"] is True
    # Only this client's own rows — the laptop's 2.5 belong to the laptop.
    assert impact["client"]["fail_minutes"] == 4.0
    assert impact["client"]["clients"] == 1


async def test_impact_is_clipped_to_the_issues_own_life(impact_app: object) -> None:
    issues = await _by_fingerprint(impact_app)
    # The AP cost clients 6.5 minutes today, but only 2.5 of them after this
    # issue opened. An issue is charged with what happened while it was open.
    client = issues["fp-fresh"]["impact"]["client"]
    assert client["fail_minutes"] == 2.5
    assert client["clients"] == 1


# --------------------------------------------------------------------------- #
# Measured vs. not measured, on both axes
# --------------------------------------------------------------------------- #


async def test_port_issue_is_not_measured_rather_than_zero(impact_app: object) -> None:
    issues = await _by_fingerprint(impact_app)
    impact = issues["fp-port"]["impact"]
    assert impact["basis"] is None
    assert impact["measured"] is False
    assert impact["client"]["fail_minutes"] is None
    assert impact["client"]["clients"] is None
    assert impact["infra"]["down_minutes"] is None


async def test_sitewide_issue_has_no_entity_to_charge(impact_app: object) -> None:
    issues = await _by_fingerprint(impact_app)
    impact = issues["fp-sitewide"]["impact"]
    assert impact["basis"] is None
    assert impact["measured"] is False
    assert impact["client"]["fail_minutes"] is None


async def test_issue_resolved_before_the_window_is_not_measured(impact_app: object) -> None:
    issues = await _by_fingerprint(impact_app)
    impact = issues["fp-old"]["impact"]
    # Its entity is scoreable, so the basis stands — but none of its life is in
    # the window, so there is nothing to report on either axis and neither must
    # read as zero.
    assert impact["basis"] == "attributed"
    assert impact["measured"] is False
    assert impact["client"]["measured"] is False
    assert impact["client"]["fail_minutes"] is None
    assert impact["infra"]["measured"] is False
    assert impact["infra"]["down_minutes"] is None


async def test_no_sle_minutes_at_all_is_not_measured(app: object) -> None:
    """The default seeded store has issues but the SLE engine never ran."""
    async with await _client(app) as c:
        body = (await c.get("/api/issues")).json()
    for issue in body["issues"]:
        impact = issue["impact"]
        assert impact["measured"] is False
        assert impact["client"]["fail_minutes"] is None
        assert impact["client"]["clients"] is None
        assert impact["infra"]["down_minutes"] is None


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
        classifier="weak_signal",
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
    assert impact["client"]["measured"] is True
    assert impact["client"]["fail_minutes"] == 0.0
    assert impact["client"]["clients"] == 0
    # One client was judged in the window, so the zero is quoted out of one.
    assert impact["client"]["clients_in_window"] == 1
    # The engine wrote no infra rows at all, so that axis is unmeasured — a dash,
    # not a zero, even though the AP was never observed down.
    assert impact["infra"]["measured"] is False
    assert impact["infra"]["down_minutes"] is None
