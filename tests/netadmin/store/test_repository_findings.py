"""Regression tests for the Phase-0 review findings on the store repository.

Covers: kind-aware rollup value (counters -> sum, gauges -> avg), source-aware
coverage, raw-tier retention pruning (poll_runs/events/state_changes), the
WindowResult.rate() helper, boundary-straddling read_window stitching, and the
same-transaction series interning with rollback-safe in-memory state.
"""

from __future__ import annotations

import pytest

from netadmin.store.repository import (
    DAY_SECONDS,
    HOUR_SECONDS,
    Repository,
    SampleReading,
    WindowResult,
)


# ---------------------------------------------------------------------------
# Finding 1: rollup `value` alias is metric-kind-aware
# ---------------------------------------------------------------------------
def test_rollup_value_is_sum_for_counters(repo: Repository, switch_entity_id: int) -> None:
    sid = repo.intern_series(switch_entity_id, "rx_bytes")  # registry: counter
    # Seed then two deltas (500, 300) inside one hour bucket.
    repo.record_samples([SampleReading(switch_entity_id, "rx_bytes", 100, 1000.0)])
    repo.record_samples([SampleReading(switch_entity_id, "rx_bytes", 200, 1500.0)])
    repo.record_samples([SampleReading(switch_entity_id, "rx_bytes", 300, 1800.0)])

    hourly = repo.read_rollup(sid, "hourly", 0, HOUR_SECONDS)
    assert hourly[0]["n"] == 2
    assert hourly[0]["sum"] == 800.0
    assert hourly[0]["avg"] == 400.0
    # The honest counter aggregate is the total accumulation, not the average.
    assert hourly[0]["value"] == 800.0


def test_rollup_value_is_avg_for_gauges(repo: Repository, switch_entity_id: int) -> None:
    sid = repo.intern_series(switch_entity_id, "rssi")  # gauge
    repo.record_samples(
        [
            SampleReading(switch_entity_id, "rssi", 100, -50.0),
            SampleReading(switch_entity_id, "rssi", 200, -70.0),
        ]
    )
    hourly = repo.read_rollup(sid, "hourly", 0, HOUR_SECONDS)
    assert hourly[0]["value"] == hourly[0]["avg"] == -60.0


# ---------------------------------------------------------------------------
# Finding 3: coverage distinguishes live from backfill polls
# ---------------------------------------------------------------------------
def test_expected_coverage_excludes_backfill_by_default(repo: Repository) -> None:
    # 10 expected polls in the window; 5 live-ok, 3 backfill-ok.
    for ts in (0, 120, 240, 360, 480):
        repo.record_poll_run(job="device", ok=True, ts=ts, source="live")
    for ts in (60, 180, 300):
        repo.record_poll_run(job="device", ok=True, ts=ts, source="backfill")

    # Default is live-only: backfilled polls are partial evidence, not live cover.
    assert repo.expected_coverage("device", 0, 600, interval_s=60) == 0.5
    # source=None counts everything.
    assert repo.expected_coverage("device", 0, 600, interval_s=60, source=None) == 0.8

    breakdown = repo.coverage_breakdown("device", 0, 600, interval_s=60)
    assert breakdown == {"live": 0.5, "backfill": 0.3, "total": 0.8}


# ---------------------------------------------------------------------------
# Finding 2: retention prunes the raw-tier logs too
# ---------------------------------------------------------------------------
def test_prune_covers_poll_runs_events_and_state_changes(
    repo: Repository, switch_entity_id: int
) -> None:
    now = 2_000_000_000
    old = now - 60 * DAY_SECONDS  # past the 30 d raw window
    recent = now - DAY_SECONDS

    repo.record_poll_run(job="device", ok=True, ts=old)
    repo.record_poll_run(job="device", ok=True, ts=recent)
    repo.record_event(ts=old, key="EVT_OLD", native_id="e-old")
    repo.record_event(ts=recent, key="EVT_NEW", native_id="e-new")

    eid = switch_entity_id
    # firmware set once long ago and never changed: its single old row must survive
    # (it is the current value), so it must NOT be counted/pruned.
    repo.record_state_change(eid, "firmware", "v1", ts=old)
    # speed changed: the old superseded row is prunable, the recent one is kept.
    repo.record_state_change(eid, "speed", "100", ts=old)
    repo.record_state_change(eid, "speed", "1000", ts=recent)

    deleted = repo.prune(now=now)

    assert deleted["poll_runs"] == 1
    assert deleted["events"] == 1
    assert deleted["state_changes"] == 1  # only the superseded old speed row

    assert len(repo.read_poll_runs("device", 0, now + 1)) == 1
    assert len(repo.read_events(0, now + 1)) == 1
    # Current state survives the prune despite being older than the window.
    assert repo.current_state(eid, "firmware") == "v1"
    assert repo.current_state(eid, "speed") == "1000"


# ---------------------------------------------------------------------------
# Finding 8: WindowResult.rate() divides deltas by actual elapsed time
# ---------------------------------------------------------------------------
def test_window_result_rate_uses_actual_elapsed() -> None:
    # Raw counter deltas at uneven spacing: 100 over 10 s, then 300 over 60 s.
    result = WindowResult(
        "raw",
        [
            {"ts": 0, "value": 999.0},  # first row: no predecessor -> omitted
            {"ts": 10, "value": 100.0},  # 100 / 10 = 10.0 /s
            {"ts": 70, "value": 300.0},  # 300 / 60 = 5.0 /s
        ],
    )
    rates = result.rate()
    assert rates == [{"ts": 10, "rate": 10.0}, {"ts": 70, "rate": 5.0}]


def test_window_result_rate_skips_nonpositive_elapsed() -> None:
    result = WindowResult("raw", [{"ts": 5, "value": 1.0}, {"ts": 5, "value": 2.0}])
    assert result.rate() == []


def test_window_result_rate_uses_sum_for_rollup_rows() -> None:
    # Rollup rows carry the bucket total in "sum"; rate() uses that, not "value".
    result = WindowResult(
        "hourly",
        [
            {"ts": 0, "sum": 10.0, "value": 10.0},
            {"ts": HOUR_SECONDS, "sum": 7200.0, "value": 7200.0},  # /3600 = 2.0 /s
        ],
    )
    assert result.rate() == [{"ts": HOUR_SECONDS, "rate": 2.0}]


def test_read_window_rate_integration(repo: Repository, switch_entity_id: int) -> None:
    sid = repo.intern_series(switch_entity_id, "rx_bytes")
    now = 2_000_000_000
    base = now - 3600
    # seed (no row), +600 @60, +600 over 120 s @180 (coalesced), +300 over 30 s @210.
    for ts, cumulative in [
        (base, 0.0),
        (base + 60, 600.0),
        (base + 180, 1200.0),
        (base + 210, 1500.0),
    ]:
        repo.record_samples([SampleReading(switch_entity_id, "rx_bytes", ts, cumulative)])

    win = repo.read_window(sid, base, now, now=now)
    assert win.tier == "raw"
    rates = win.rate()
    # Rate is computed between consecutive stored deltas by ACTUAL elapsed time:
    # 600/120 = 5/s over the coalesced gap, then 300/30 = 10/s -- the wide
    # interval is not over-counted as if it were one cadence.
    assert rates == [
        {"ts": base + 180, "rate": 5.0},
        {"ts": base + 210, "rate": 10.0},
    ]


# ---------------------------------------------------------------------------
# Finding 10: read_window stitches rollup(old) + raw(recent) across the boundary
# ---------------------------------------------------------------------------
def test_read_window_stitches_across_raw_boundary(repo: Repository, switch_entity_id: int) -> None:
    sid = repo.intern_series(switch_entity_id, "rssi")
    now = 2_000_000_000
    old_ts = now - 40 * DAY_SECONDS  # past raw retention -> only rollup remains
    recent_ts = now - DAY_SECONDS  # inside raw retention

    repo.record_samples([SampleReading(switch_entity_id, "rssi", old_ts, 10.0)])
    repo.record_samples([SampleReading(switch_entity_id, "rssi", recent_ts, 20.0)])
    # Simulate the nightly prune having removed the old raw row.
    repo.prune(now=now)
    assert repo.read_raw(sid, old_ts - 1, old_ts + 1) == []

    # Window spans from before the raw floor to now: old part from hourly rollup,
    # recent part from raw -- not the whole span served as coarse rollup.
    result = repo.read_window(sid, old_ts - HOUR_SECONDS, now, now=now)
    assert result.tier == "stitched"
    values = [r["value"] for r in result.rows]
    assert 10.0 in values  # old, from the hourly rollup
    assert 20.0 in values  # recent, from raw
    # Rows are oldest-first: ts stays monotonic across the stitch seam.
    times = [r["ts"] for r in result.rows]
    assert times == sorted(times)


def test_read_window_single_tier_labels_unchanged(repo: Repository, switch_entity_id: int) -> None:
    sid = repo.intern_series(switch_entity_id, "rssi")
    now = 2_000_000_000
    day = DAY_SECONDS
    assert repo.read_window(sid, now - 3600, now, now=now).tier == "raw"
    assert repo.read_window(sid, now - 60 * day, now - 59 * day, now=now).tier == "hourly"
    assert repo.read_window(sid, now - 730 * day, now - 729 * day, now=now).tier == "daily"


# ---------------------------------------------------------------------------
# Finding 5: series interning rides the sample transaction; rollback is clean
# ---------------------------------------------------------------------------
def test_interning_rollback_evicts_cache_and_baseline(
    repo: Repository, switch_entity_id: int
) -> None:
    eid = switch_entity_id
    # A committed counter seed so we can prove the baseline is not corrupted.
    repo.record_samples([SampleReading(eid, "rx_bytes", 0, 1000.0)])

    def boom(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("disk full mid-write")

    # Force a failure after interning, inside the write transaction.
    repo._insert_raw = boom  # type: ignore[assignment, method-assign]
    with pytest.raises(RuntimeError):
        repo.record_samples(
            [
                SampleReading(eid, "brand_new_metric", 10, 5.0),  # a never-seen series
                SampleReading(eid, "rx_bytes", 60, 1500.0),  # would advance the baseline
            ]
        )

    del repo._insert_raw  # restore the class method

    # The interned-but-rolled-back series is gone from BOTH the cache and the DB
    # (interning was inside the same transaction, so it rolled back with it).
    assert (eid, "brand_new_metric") not in repo._series_cache
    assert repo.get_series(eid, "brand_new_metric") is None
    assert (
        repo.connection.execute(
            "SELECT COUNT(*) FROM series WHERE metric='brand_new_metric'"
        ).fetchone()[0]
        == 0
    )

    # The counter baseline was rolled back to the committed seed (1000 @ 0), so a
    # fresh reading computes the delta from the seed, not the rolled-back 1500.
    sid = repo.get_series(eid, "rx_bytes")
    assert sid is not None
    repo.record_samples([SampleReading(eid, "rx_bytes", 120, 1600.0)])  # 1600-1000=600
    rows = repo.read_raw(sid, 0, 1000)
    assert rows == [{"ts": 120, "value": 600.0}]


# ---------------------------------------------------------------------------
# Finding: max_event_ts() reads the cursor without materializing every event
# ---------------------------------------------------------------------------
def test_max_event_ts_returns_newest_or_none(repo: Repository, switch_entity_id: int) -> None:
    assert repo.max_event_ts() is None
    repo.record_event(ts=1_000, key="EVT_A", native_id="a")
    repo.record_event(ts=3_000, key="EVT_B", native_id="b")
    repo.record_event(ts=2_000, key="EVT_C", native_id="c")
    assert repo.max_event_ts() == 3_000


# ---------------------------------------------------------------------------
# Finding: public cycle-spanning transaction() commits/rolls back atomically
# ---------------------------------------------------------------------------
def test_transaction_commits_all_writes_atomically(repo: Repository, switch_entity_id: int) -> None:
    from netadmin.domain.entities import Entity
    from netadmin.domain.types import EntityType

    with repo.transaction():
        eid = repo.upsert_entity(
            Entity(entity_type=EntityType.AP, native_id="aa:bb:cc:00:00:0f"), ts=10
        )
        repo.sync_entity_state(eid, {"firmware": "1.2.3"}, ts=10)
        repo.record_samples([SampleReading(eid, "cpu", 10, 7.0)])

    assert repo.find_entity(EntityType.AP, "aa:bb:cc:00:00:0f") is not None
    assert repo.current_state(eid, "firmware") == "1.2.3"
    sid = repo.get_series(eid, "cpu")
    assert sid is not None and repo.read_raw(sid, 0, 100) == [{"ts": 10, "value": 7.0}]


def test_transaction_rolls_back_inventory_when_a_later_write_fails(
    repo: Repository, switch_entity_id: int
) -> None:
    from netadmin.domain.entities import Entity
    from netadmin.domain.types import EntityType

    with pytest.raises(RuntimeError):
        with repo.transaction():
            repo.upsert_entity(
                Entity(entity_type=EntityType.AP, native_id="aa:bb:cc:00:00:bad"), ts=10
            )
            raise RuntimeError("cycle blew up after the inventory write")

    # The whole cycle rolled back: the inventory upsert did NOT survive on its own
    # (the pre-fix per-call BEGIN IMMEDIATE would have committed it independently).
    assert repo.find_entity(EntityType.AP, "aa:bb:cc:00:00:bad") is None
