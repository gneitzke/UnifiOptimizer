"""DetectorContext: window/events/entities/coverage/threshold + the factory."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from netadmin.detect.context import DetectorContext
from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.store.repository import Repository, SampleReading
from tests.netadmin.detect.support import FakeBaselines

NOW = 2_000_000


def _ctx(repo: Repository, *, settings=None, now: int = NOW) -> DetectorContext:
    return DetectorContext(
        repo=repo,
        baselines=FakeBaselines(),
        now_ts=now,
        site_id="default",
        settings=settings,
    )


# ---------------------------------------------------------------------- #
# window
# ---------------------------------------------------------------------- #
def test_window_none_for_unknown_series(repo: Repository, ap_entity_id: int) -> None:
    assert _ctx(repo).window(ap_entity_id, "never_recorded", 3600) is None


def test_window_none_for_nonpositive_seconds(repo: Repository, ap_entity_id: int) -> None:
    repo.record_samples([SampleReading(ap_entity_id, "rssi", NOW - 60, -60.0)])
    assert _ctx(repo).window(ap_entity_id, "rssi", 0) is None


def test_window_reads_recent_samples(repo: Repository, ap_entity_id: int) -> None:
    points = [(NOW - 180, -61.0), (NOW - 120, -62.0), (NOW - 60, -63.0)]
    repo.record_samples(SampleReading(ap_entity_id, "rssi", ts, v) for ts, v in points)
    result = _ctx(repo).window(ap_entity_id, "rssi", 600)
    assert result is not None
    values = [row["value"] for row in result.rows]
    assert values == [-61.0, -62.0, -63.0]


# ---------------------------------------------------------------------- #
# events
# ---------------------------------------------------------------------- #
def _seed_event(repo: Repository, *, ts: int, key: str, entity_id: int) -> None:
    repo.record_event(ts=ts, key=key, entity_id=entity_id)


def test_events_filters_by_key_and_since(repo: Repository, ap_entity_id: int) -> None:
    _seed_event(repo, ts=NOW - 5000, key="EVT_AP_Lost_Contact", entity_id=ap_entity_id)  # too old
    _seed_event(repo, ts=NOW - 100, key="EVT_AP_Lost_Contact", entity_id=ap_entity_id)
    _seed_event(repo, ts=NOW - 50, key="EVT_AP_Connected", entity_id=ap_entity_id)

    ctx = _ctx(repo)
    lost = ctx.events(entity_id=ap_entity_id, keys=["EVT_AP_Lost_Contact"], since_ts=NOW - 3600)
    assert [r["ts"] for r in lost] == [NOW - 100]

    everything = ctx.events(entity_id=ap_entity_id, since_ts=NOW - 3600)
    assert [r["key"] for r in everything] == ["EVT_AP_Lost_Contact", "EVT_AP_Connected"]


def test_events_includes_event_at_now(repo: Repository, ap_entity_id: int) -> None:
    _seed_event(repo, ts=NOW, key="EVT_AP_Lost_Contact", entity_id=ap_entity_id)
    assert len(_ctx(repo).events(entity_id=ap_entity_id)) == 1


# ---------------------------------------------------------------------- #
# entities
# ---------------------------------------------------------------------- #
def test_entities_decodes_type_and_meta(repo: Repository) -> None:
    repo.upsert_entity(
        Entity(
            entity_type=EntityType.SWITCH,
            native_id="aa:bb:cc:dd:ee:10",
            site_id="default",
            name="sw-1",
            meta={"is_uplink": True},
        ),
        ts=NOW,
    )
    switches = _ctx(repo).entities(EntityType.SWITCH)
    assert len(switches) == 1
    assert isinstance(switches[0], Entity)
    assert switches[0].entity_type is EntityType.SWITCH
    assert switches[0].meta == {"is_uplink": True}


def test_entities_filters_by_type(repo: Repository, ap_entity_id: int) -> None:
    repo.upsert_entity(
        Entity(entity_type=EntityType.SWITCH, native_id="sw", site_id="default"), ts=NOW
    )
    assert [e.entity_type for e in _ctx(repo).entities(EntityType.AP)] == [EntityType.AP]
    assert _ctx(repo).entities(EntityType.CLIENT) == []


# ---------------------------------------------------------------------- #
# coverage
# ---------------------------------------------------------------------- #
def test_coverage_full_when_every_poll_succeeded(repo: Repository) -> None:
    # 10 polls across [NOW-600, NOW); expected = 600/60 = 10 -> full coverage.
    for k in range(10):
        repo.record_poll_run(job="fast_device", ok=True, ts=NOW - 600 + k * 60)
    assert _ctx(repo).coverage(600, "fast_device") == pytest.approx(1.0)


def test_coverage_half_when_half_the_polls_succeeded(repo: Repository) -> None:
    for k in range(5):
        repo.record_poll_run(job="fast_device", ok=True, ts=NOW - 600 + k * 60)
    assert _ctx(repo).coverage(600, "fast_device") == pytest.approx(0.5)


def test_coverage_excludes_backfill_source(repo: Repository) -> None:
    for k in range(10):
        repo.record_poll_run(
            job="reports_5min", ok=True, ts=NOW - 600 + (k + 1) * 60, source="backfill"
        )
    # live coverage only: backfilled polls are partial evidence, not live.
    assert _ctx(repo).coverage(600, "reports_5min") == pytest.approx(0.0)


def test_coverage_zero_for_nonpositive_window(repo: Repository) -> None:
    assert _ctx(repo).coverage(0, "fast_device") == 0.0


def test_coverage_uses_settings_poll_interval(repo: Repository) -> None:
    # device_s=30 halves the cadence, doubling the expected count -> halves coverage.
    settings = SimpleNamespace(poll=SimpleNamespace(device_s=30), thresholds={})
    for k in range(10):
        repo.record_poll_run(job="fast_device", ok=True, ts=NOW - 600 + k * 60)
    assert _ctx(repo, settings=settings).coverage(600, "fast_device") == pytest.approx(0.5)


# ---------------------------------------------------------------------- #
# threshold
# ---------------------------------------------------------------------- #
def test_threshold_returns_default_when_unset(repo: Repository) -> None:
    assert _ctx(repo).threshold("wired.bad_cable", "rate_per_min", 10) == 10


def test_threshold_reads_override_section(repo: Repository) -> None:
    settings = SimpleNamespace(thresholds={"wired.bad_cable": {"rate_per_min": 25}}, poll=None)
    assert _ctx(repo, settings=settings).threshold("wired.bad_cable", "rate_per_min", 10) == 25


def test_threshold_ignores_non_dict_section(repo: Repository) -> None:
    settings = SimpleNamespace(thresholds={"wired.bad_cable": "oops"}, poll=None)
    assert _ctx(repo, settings=settings).threshold("wired.bad_cable", "x", 7) == 7


# ---------------------------------------------------------------------- #
# factory
# ---------------------------------------------------------------------- #
def test_for_repository_builds_real_baselines(repo: Repository) -> None:
    ctx = DetectorContext.for_repository(repo, NOW)
    assert ctx.now_ts == NOW
    assert ctx.site_id == "default"
    assert hasattr(ctx.baselines, "band")  # real Baselines from the seam


def test_for_repository_accepts_injected_baselines(repo: Repository) -> None:
    fake = FakeBaselines()
    ctx = DetectorContext.for_repository(repo, NOW, baselines=fake, site_id="s1")
    assert ctx.baselines is fake
    assert ctx.site_id == "s1"
