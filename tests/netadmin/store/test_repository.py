"""Tests for the repository: interning, deltas, rollups, retention, coverage, CRUD."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.store.metrics import MetricKind
from netadmin.store.repository import DAY_SECONDS, HOUR_SECONDS, Repository, SampleReading

# ---------------------------------------------------------------------------
# Entities + discrete state history
# ---------------------------------------------------------------------------


def test_upsert_entity_insert_then_update(repo: Repository) -> None:
    e = Entity(entity_type=EntityType.AP, native_id="aa:bb:cc:00:00:01", name="ap1")
    eid = repo.upsert_entity(e, ts=1000)
    assert eid == e.entity_id

    # Same natural key -> same row, first_seen preserved, last_seen advanced.
    again = Entity(entity_type=EntityType.AP, native_id="aa:bb:cc:00:00:01", name="ap1-renamed")
    eid2 = repo.upsert_entity(again, ts=2000)
    assert eid2 == eid
    row = repo.get_entity(eid)
    assert row["name"] == "ap1-renamed"
    assert row["first_seen_ts"] == 1000
    assert row["last_seen_ts"] == 2000
    assert repo.list_entities(EntityType.AP).__len__() == 1


def test_state_change_only_on_actual_change(repo: Repository, switch_entity_id: int) -> None:
    eid = switch_entity_id
    assert repo.record_state_change(eid, "firmware", "6.5.0", ts=100) is True  # first sighting
    assert repo.record_state_change(eid, "firmware", "6.5.0", ts=200) is False  # unchanged
    assert repo.record_state_change(eid, "firmware", "6.6.0", ts=300) is True  # changed

    hist = repo.state_history(eid, "firmware")
    assert [(h["old_value"], h["new_value"]) for h in hist] == [
        ("6.5.0", "6.6.0"),
        (None, "6.5.0"),
    ]
    assert repo.current_state(eid, "firmware") == "6.6.0"


def test_sync_entity_state_reports_changed_attrs(repo: Repository, switch_entity_id: int) -> None:
    changed = repo.sync_entity_state(switch_entity_id, {"state": "up", "link_speed": 1000}, ts=10)
    assert set(changed) == {"state", "link_speed"}
    # link_speed unchanged (int coerced to str), state flips
    changed2 = repo.sync_entity_state(
        switch_entity_id, {"state": "down", "link_speed": 1000}, ts=20
    )
    assert changed2 == ["state"]


# ---------------------------------------------------------------------------
# Series interning
# ---------------------------------------------------------------------------


def test_intern_series_stable_and_cached(repo: Repository, switch_entity_id: int) -> None:
    a = repo.intern_series(switch_entity_id, "rx_errors", unit="count")
    b = repo.intern_series(switch_entity_id, "rx_errors")
    assert a == b
    # only one row in series despite repeated interning
    rows = repo.connection.execute(
        "SELECT COUNT(*) FROM series WHERE entity_id=? AND metric='rx_errors'",
        (switch_entity_id,),
    ).fetchone()[0]
    assert rows == 1
    # distinct metric -> distinct id
    c = repo.intern_series(switch_entity_id, "tx_errors")
    assert c != a


def test_intern_series_cache_hit_avoids_sql(repo: Repository, switch_entity_id: int) -> None:
    sid = repo.intern_series(switch_entity_id, "rssi")
    # Poison the DB out from under the cache; a cache hit must not query it.
    repo.connection.execute("DELETE FROM series")
    assert repo.intern_series(switch_entity_id, "rssi") == sid


def test_get_series_does_not_create(repo: Repository, switch_entity_id: int) -> None:
    assert repo.get_series(switch_entity_id, "never_seen") is None


# ---------------------------------------------------------------------------
# Samples: gauge storage, counter deltas, counter reset
# ---------------------------------------------------------------------------


def test_gauge_stored_verbatim(repo: Repository, switch_entity_id: int) -> None:
    sid = repo.intern_series(switch_entity_id, "rssi")
    written = repo.record_samples(
        [
            SampleReading(switch_entity_id, "rssi", 10, -55.0),
            SampleReading(switch_entity_id, "rssi", 20, -60.0),
        ]
    )
    assert written == 2
    rows = repo.read_raw(sid, 0, 100)
    assert [r["value"] for r in rows] == [-55.0, -60.0]


def test_counter_delta_math(repo: Repository, switch_entity_id: int) -> None:
    sid = repo.intern_series(switch_entity_id, "rx_bytes")
    # First reading seeds the baseline (no row); then deltas.
    repo.record_samples([SampleReading(switch_entity_id, "rx_bytes", 10, 1000.0)])
    repo.record_samples([SampleReading(switch_entity_id, "rx_bytes", 20, 1500.0)])
    repo.record_samples([SampleReading(switch_entity_id, "rx_bytes", 30, 1800.0)])
    rows = repo.read_raw(sid, 0, 100)
    # 1000 seeds, 1500-1000=500, 1800-1500=300
    assert [(r["ts"], r["value"]) for r in rows] == [(20, 500.0), (30, 300.0)]


def test_counter_reset_treated_as_new_value(repo: Repository, switch_entity_id: int) -> None:
    sid = repo.intern_series(switch_entity_id, "tx_bytes")
    for ts, val in [(10, 1000.0), (20, 1500.0), (30, 200.0), (40, 350.0)]:
        repo.record_samples([SampleReading(switch_entity_id, "tx_bytes", ts, val)])
    rows = repo.read_raw(sid, 0, 100)
    # 1000 seeds; 1500-1000=500; reset at 200 (delta<0) -> stored as 200;
    # then 350-200=150
    assert [(r["ts"], r["value"]) for r in rows] == [
        (20, 500.0),
        (30, 200.0),
        (40, 150.0),
    ]


def test_counter_gap_reseeds_instead_of_gap_spanning_delta(
    repo: Repository, switch_entity_id: int
) -> None:
    # A device is briefly unreachable (no reboot; the counter keeps climbing).
    # On resume the delta from the pre-gap reading would be enormous and would
    # poison the hour/day rollups; the series must re-seed instead.
    sid = repo.intern_series(switch_entity_id, "rx_errors")
    repo.record_samples([SampleReading(switch_entity_id, "rx_errors", 0, 1000.0)])  # seed
    repo.record_samples([SampleReading(switch_entity_id, "rx_errors", 60, 1010.0)])  # +10
    # 10-minute gap (600 s >> 150 s default): counter climbed 1010 -> 13000.
    repo.record_samples(
        [SampleReading(switch_entity_id, "rx_errors", 660, 13000.0)]
    )  # re-seed, NO gap-spanning row
    repo.record_samples([SampleReading(switch_entity_id, "rx_errors", 720, 13050.0)])  # +50

    rows = repo.read_raw(sid, 0, 1000)
    assert [(r["ts"], r["value"]) for r in rows] == [(60, 10.0), (720, 50.0)]
    # The poisoned 11990 sample never reached the hourly rollup either.
    hourly = repo.read_rollup(sid, "hourly", 0, HOUR_SECONDS)
    assert hourly[0]["max"] == 50.0


def test_counter_within_gap_limit_still_emits_delta(
    repo: Repository, switch_entity_id: int
) -> None:
    # A single coalesced/late poll inside the gap limit is a real delta, not a gap.
    sid = repo.intern_series(switch_entity_id, "rx_packets")
    repo.record_samples([SampleReading(switch_entity_id, "rx_packets", 0, 100.0)])  # seed
    repo.record_samples(
        [SampleReading(switch_entity_id, "rx_packets", 120, 175.0)]
    )  # 120 s <= 150 s -> +75
    rows = repo.read_raw(sid, 0, 1000)
    assert [(r["ts"], r["value"]) for r in rows] == [(120, 75.0)]


def test_counter_max_gap_s_override_per_call(repo: Repository, switch_entity_id: int) -> None:
    # A collector on a coarser counter cadence widens the gap tolerance per call.
    sid = repo.intern_series(switch_entity_id, "tx_bytes")
    repo.record_samples(
        [SampleReading(switch_entity_id, "tx_bytes", 0, 1000.0)], max_gap_s=1200
    )  # seed
    repo.record_samples(
        [SampleReading(switch_entity_id, "tx_bytes", 600, 1500.0)], max_gap_s=1200
    )  # 600 s <= 1200 s -> +500
    rows = repo.read_raw(sid, 0, 1000)
    assert [(r["ts"], r["value"]) for r in rows] == [(600, 500.0)]


def test_explicit_kind_override(repo: Repository, switch_entity_id: int) -> None:
    sid = repo.intern_series(switch_entity_id, "custom_metric")
    # Force counter semantics on a metric the registry treats as a gauge.
    repo.record_samples(
        [SampleReading(switch_entity_id, "custom_metric", 10, 100.0, kind=MetricKind.COUNTER)]
    )
    repo.record_samples(
        [SampleReading(switch_entity_id, "custom_metric", 20, 130.0, kind=MetricKind.COUNTER)]
    )
    rows = repo.read_raw(sid, 0, 100)
    assert [(r["ts"], r["value"]) for r in rows] == [(20, 30.0)]


def test_duplicate_ts_ignored_no_double_rollup(repo: Repository, switch_entity_id: int) -> None:
    sid = repo.intern_series(switch_entity_id, "rssi")
    repo.record_samples([SampleReading(switch_entity_id, "rssi", 10, -50.0)])
    # Same (series, ts) again -> ignored, rollup not double-counted.
    written = repo.record_samples([SampleReading(switch_entity_id, "rssi", 10, -99.0)])
    assert written == 0
    hourly = repo.read_rollup(sid, "hourly", 0, HOUR_SECONDS)
    assert hourly[0]["n"] == 1
    assert hourly[0]["value"] == -50.0


# ---------------------------------------------------------------------------
# Rollups across bucket boundaries
# ---------------------------------------------------------------------------


def test_rollup_correctness_within_and_across_buckets(
    repo: Repository, switch_entity_id: int
) -> None:
    sid = repo.intern_series(switch_entity_id, "rssi")
    # Three samples in hour-bucket 0, two in hour-bucket 1.
    readings = [
        SampleReading(switch_entity_id, "rssi", 100, 10.0),
        SampleReading(switch_entity_id, "rssi", 200, 20.0),
        SampleReading(switch_entity_id, "rssi", 300, 30.0),
        SampleReading(switch_entity_id, "rssi", HOUR_SECONDS + 50, 40.0),
        SampleReading(switch_entity_id, "rssi", HOUR_SECONDS + 60, 50.0),
    ]
    repo.record_samples(readings)

    hourly = repo.read_rollup(sid, "hourly", 0, 2 * HOUR_SECONDS)
    assert len(hourly) == 2

    b0 = hourly[0]
    assert b0["ts"] == 0
    assert b0["n"] == 3
    assert b0["min"] == 10.0 and b0["max"] == 30.0
    assert b0["sum"] == 60.0
    assert b0["avg"] == 20.0
    assert b0["last"] == 30.0

    b1 = hourly[1]
    assert b1["ts"] == HOUR_SECONDS
    assert b1["n"] == 2
    assert b1["sum"] == 90.0
    assert b1["avg"] == 45.0
    assert b1["last"] == 50.0

    # All five land in the same UTC day bucket.
    daily = repo.read_rollup(sid, "daily", 0, DAY_SECONDS)
    assert len(daily) == 1
    assert daily[0]["ts"] == 0
    assert daily[0]["n"] == 5
    assert daily[0]["sum"] == 150.0
    assert daily[0]["avg"] == 30.0
    assert daily[0]["min"] == 10.0 and daily[0]["max"] == 50.0
    assert daily[0]["last"] == 50.0


def test_daily_bucket_crosses_utc_midnight(repo: Repository, switch_entity_id: int) -> None:
    sid = repo.intern_series(switch_entity_id, "rssi")
    # One sample just before UTC midnight, one just after -> two daily buckets.
    repo.record_samples(
        [
            SampleReading(switch_entity_id, "rssi", DAY_SECONDS - 10, 1.0),
            SampleReading(switch_entity_id, "rssi", DAY_SECONDS + 10, 2.0),
        ]
    )
    daily = repo.read_rollup(sid, "daily", 0, 3 * DAY_SECONDS)
    assert [d["ts"] for d in daily] == [0, DAY_SECONDS]
    assert [d["n"] for d in daily] == [1, 1]


# ---------------------------------------------------------------------------
# Windowed reads: raw + rollup fallback
# ---------------------------------------------------------------------------


def test_read_window_tier_selection(repo: Repository, switch_entity_id: int) -> None:
    sid = repo.intern_series(switch_entity_id, "rssi")
    now = 1_000_000_000
    day = DAY_SECONDS

    # Recent window -> raw tier.
    recent = repo.read_window(sid, now - 3600, now, now=now)
    assert recent.tier == "raw"

    # ~2 months back (past 30 d raw, within 18 mo) -> hourly.
    two_months = repo.read_window(sid, now - 60 * day, now - 59 * day, now=now)
    assert two_months.tier == "hourly"

    # ~2 years back (past 18 mo) -> daily.
    two_years = repo.read_window(sid, now - 730 * day, now - 729 * day, now=now)
    assert two_years.tier == "daily"


def test_read_window_serves_rollup_after_raw_pruned(
    repo: Repository, switch_entity_id: int
) -> None:
    sid = repo.intern_series(switch_entity_id, "rssi")
    now = 1_000_000_000
    old_ts = now - 60 * DAY_SECONDS  # older than 30 d raw retention
    repo.record_samples([SampleReading(switch_entity_id, "rssi", old_ts, 42.0)])
    # Simulate the nightly prune having removed the raw row.
    repo.prune(now=now)
    assert repo.read_raw(sid, old_ts - 1, old_ts + 1) == []
    # The window read still resolves via the hourly rollup (kept 18 mo). The
    # window must span the floored hour bucket that holds old_ts.
    result = repo.read_window(sid, old_ts - HOUR_SECONDS, old_ts + HOUR_SECONDS, now=now)
    assert result.tier == "hourly"
    assert result.rows and result.rows[0]["value"] == 42.0


# ---------------------------------------------------------------------------
# Events dedupe
# ---------------------------------------------------------------------------


def test_event_dedupe_on_native_id(repo: Repository, switch_entity_id: int) -> None:
    first = repo.record_event(ts=10, key="EVT_SW_Lost_Contact", native_id="evt-1")
    dup = repo.record_event(ts=11, key="EVT_SW_Lost_Contact", native_id="evt-1")
    assert first is not None
    assert dup is None
    # Events without native_id are always inserted.
    a = repo.record_event(ts=12, key="EVT_WU_Roam")
    b = repo.record_event(ts=13, key="EVT_WU_Roam")
    assert a is not None and b is not None and a != b
    assert len(repo.read_events(0, 100)) == 3


def test_record_events_batch_counts_inserts(repo: Repository) -> None:
    inserted = repo.record_events(
        [
            {"ts": 1, "key": "EVT_A", "native_id": "x"},
            {"ts": 2, "key": "EVT_A", "native_id": "x"},  # dup
            {"ts": 3, "key": "EVT_B", "native_id": "y"},
        ]
    )
    assert inserted == 2


# ---------------------------------------------------------------------------
# Poll runs + expected coverage
# ---------------------------------------------------------------------------


def test_expected_coverage(repo: Repository) -> None:
    # Window of 600 s, interval 60 s -> 10 expected polls.
    for ts in range(0, 600, 60):
        repo.record_poll_run(job="device", ok=(ts % 120 == 0), ts=ts)
    # ok at ts 0,120,240,360,480 -> 5 successes of 10 expected -> 0.5
    cov = repo.expected_coverage("device", 0, 600, interval_s=60)
    assert cov == 0.5


def test_expected_coverage_full_and_clamped(repo: Repository) -> None:
    for ts in range(0, 300, 60):
        repo.record_poll_run(job="health", ok=True, ts=ts)
    # 5 successes, 5 expected -> 1.0
    assert repo.expected_coverage("health", 0, 300, interval_s=60) == 1.0
    # Degenerate windows return 0.0, never divide-by-zero.
    assert repo.expected_coverage("health", 0, 0, interval_s=60) == 0.0
    assert repo.expected_coverage("health", 0, 300, interval_s=0) == 0.0


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def test_prune_raw_and_hourly_keeps_daily(repo: Repository, switch_entity_id: int) -> None:
    sid = repo.intern_series(switch_entity_id, "rssi")
    now = 1_000_000_000
    old = now - 400 * DAY_SECONDS  # older than 30 d raw AND 18 mo? 400d < 548d, so hourly kept
    older = now - 600 * DAY_SECONDS  # older than 18 mo hourly retention
    recent = now - DAY_SECONDS
    repo.record_samples(
        [
            SampleReading(switch_entity_id, "rssi", recent, 1.0),
            SampleReading(switch_entity_id, "rssi", old, 2.0),
            SampleReading(switch_entity_id, "rssi", older, 3.0),
        ]
    )
    deleted = repo.prune(now=now)
    # raw: old + older are past 30 d -> 2 deleted; recent kept.
    assert deleted["raw"] == 2
    # hourly: only the 600-day bucket is past 18 mo -> 1 deleted.
    assert deleted["hourly"] == 1
    assert len(repo.read_raw(sid, 0, now + 1)) == 1  # only recent raw remains
    # daily kept forever: all three daily buckets survive.
    assert len(repo.read_rollup(sid, "daily", 0, now + 1)) == 3


# ---------------------------------------------------------------------------
# Issues + issue_events CRUD
# ---------------------------------------------------------------------------


def test_issue_crud_and_open_lookup(repo: Repository, switch_entity_id: int) -> None:
    iid = repo.insert_issue(
        fingerprint="fp-1",
        detector_key="wired.bad_cable",
        severity="p2",
        state="pending",
        first_seen_ts=100,
        last_seen_ts=100,
        title="rx_errors climbing",
        entity_id=switch_entity_id,
        evidence={"rate": 42},
    )
    assert repo.get_open_issue("fp-1")["id"] == iid

    repo.update_issue(iid, state="active", occurrences=3, evidence={"rate": 99})
    row = repo.get_issue(iid)
    assert row["state"] == "active"
    assert row["occurrences"] == 3
    assert '"rate": 99' in row["evidence"]

    repo.record_issue_event(iid, "detected", ts=100, detail={"n": 1})
    repo.record_issue_event(iid, "escalated", ts=200)
    trail = repo.list_issue_events(iid)
    assert [e["kind"] for e in trail] == ["detected", "escalated"]

    # Resolving frees the fingerprint for the open-lookup.
    repo.update_issue(iid, state="resolved", resolved_ts=300)
    assert repo.get_open_issue("fp-1") is None
    assert len(repo.list_issues(entity_id=switch_entity_id)) == 1


def test_get_recent_resolved_issue_indexed_lookup(repo: Repository, switch_entity_id: int) -> None:
    # Two resolved rows for the same fingerprint at different resolved_ts, plus a
    # decoy fingerprint. The lookup must return the newest within the floor, in
    # SQL, without scanning every resolved row in Python.
    def _resolved(fp: str, resolved_ts: int) -> int:
        iid = repo.insert_issue(
            fingerprint=fp,
            detector_key="wired.bad_cable",
            severity="p2",
            state="resolved",
            first_seen_ts=resolved_ts - 50,
            last_seen_ts=resolved_ts,
            resolved_ts=resolved_ts,
            title="t",
            entity_id=switch_entity_id,
        )
        return iid

    _resolved("fp-r", 1000)
    newest = _resolved("fp-r", 5000)
    _resolved("fp-other", 6000)  # different fingerprint, must be ignored

    got = repo.get_recent_resolved_issue("fp-r", 0)
    assert got is not None and got["id"] == newest and got["resolved_ts"] == 5000

    # Floor above the newest resolution -> outside the reopen window -> None.
    assert repo.get_recent_resolved_issue("fp-r", 5001) is None
    # Unknown fingerprint -> None (never matches the decoy).
    assert repo.get_recent_resolved_issue("fp-missing", 0) is None
    # An open (non-resolved) issue is never returned by this resolved-only lookup.
    repo.insert_issue(
        fingerprint="fp-open",
        detector_key="wired.bad_cable",
        severity="p2",
        state="active",
        first_seen_ts=10,
        last_seen_ts=10,
        title="t",
        entity_id=switch_entity_id,
    )
    assert repo.get_recent_resolved_issue("fp-open", 0) is None


def test_delete_issue_removes_row_and_trail(repo: Repository, switch_entity_id: int) -> None:
    iid = repo.insert_issue(
        fingerprint="fp-del",
        detector_key="wired.bad_cable",
        severity="p2",
        state="pending",
        first_seen_ts=100,
        last_seen_ts=100,
        title="blip",
        entity_id=switch_entity_id,
    )
    repo.record_issue_event(iid, "detected", ts=100)
    assert repo.get_open_issue("fp-del") is not None

    repo.delete_issue(iid)

    assert repo.get_issue(iid) is None
    assert repo.get_open_issue("fp-del") is None
    assert repo.list_issue_events(iid) == []


def test_update_issue_rejects_unknown_column(repo: Repository) -> None:
    iid = repo.insert_issue(
        fingerprint="fp",
        detector_key="k",
        severity="p3",
        state="pending",
        first_seen_ts=1,
        last_seen_ts=1,
        title="t",
    )
    try:
        repo.update_issue(iid, bogus_column=1)
    except ValueError as exc:
        assert "bogus_column" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


# ---------------------------------------------------------------------------
# App metadata (generic key/value cache; migration 0007)
# ---------------------------------------------------------------------------


def test_app_meta_missing_key_is_none(repo: Repository) -> None:
    assert repo.get_app_meta("nope") is None


def test_app_meta_set_then_get_round_trips(repo: Repository) -> None:
    repo.set_app_meta("update.latest_version", "1.2.3")
    assert repo.get_app_meta("update.latest_version") == "1.2.3"


def test_app_meta_set_overwrites_in_place(repo: Repository) -> None:
    repo.set_app_meta("k", "first")
    repo.set_app_meta("k", "second")
    assert repo.get_app_meta("k") == "second"


def test_app_meta_keys_are_independent(repo: Repository) -> None:
    repo.set_app_meta("update.latest_version", "1.2.3")
    repo.set_app_meta("update.checked_ts", "1000")
    assert repo.get_app_meta("update.latest_version") == "1.2.3"
    assert repo.get_app_meta("update.checked_ts") == "1000"
    assert repo.get_app_meta("some.other.key") is None


# ---------------------------------------------------------------------------
# Baselines / changes / sle_minutes / investigations CRUD
# ---------------------------------------------------------------------------


def test_baseline_upsert(repo: Repository, switch_entity_id: int) -> None:
    sid = repo.intern_series(switch_entity_id, "rssi")
    repo.upsert_baseline(sid, "all", "ewma_mean", -60.0, ts=10)
    assert repo.get_baseline(sid, "all", "ewma_mean") == -60.0
    repo.upsert_baseline(sid, "all", "ewma_mean", -62.0, ts=20)  # overwrite
    assert repo.get_baseline(sid, "all", "ewma_mean") == -62.0
    repo.upsert_baseline(sid, "h03", "p95", 5.0, ts=30)
    assert len(repo.get_baselines(sid)) == 2


def test_changes_ledger_and_revert(repo: Repository, switch_entity_id: int) -> None:
    cid = repo.insert_change(
        action="tx_power_step_down",
        before={"tx_power": "high"},
        after={"tx_power": "medium"},
        status="applied",
        entity_id=switch_entity_id,
        ts=100,
    )
    repo.update_change_status(cid, "reverted", reverted_ts=200)
    row = repo.get_change(cid)
    assert row["status"] == "reverted"
    assert row["reverted_ts"] == 200
    assert '"tx_power": "high"' in row["before_json"]
    assert len(repo.list_changes(entity_id=switch_entity_id)) == 1


def test_sle_minutes_upsert_replace_and_add(repo: Repository, switch_entity_id: int) -> None:
    repo.upsert_sle_minute(
        bucket_ts=0,
        sle="coverage",
        classifier="weak_signal",
        entity_id=switch_entity_id,
        minutes=2.0,
    )
    repo.upsert_sle_minute(
        bucket_ts=0,
        sle="coverage",
        classifier="weak_signal",
        entity_id=switch_entity_id,
        minutes=3.0,
    )  # replace
    repo.add_sle_minutes(
        bucket_ts=0, sle="coverage", classifier="ok", entity_id=switch_entity_id, minutes=1.0
    )
    repo.add_sle_minutes(
        bucket_ts=0, sle="coverage", classifier="ok", entity_id=switch_entity_id, minutes=1.5
    )  # accumulate

    by_classifier = repo.query_sle_minutes(0, 300, group_by=("classifier",))
    got = {row["classifier"]: row["minutes"] for row in by_classifier}
    assert got == {"weak_signal": 3.0, "ok": 2.5}

    total = repo.query_sle_minutes(0, 300, group_by=())
    assert total[0]["minutes"] == 5.5


def test_query_sle_minutes_rejects_bad_group(repo: Repository) -> None:
    try:
        repo.query_sle_minutes(0, 10, group_by=("evil",))
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_investigations_crud(repo: Repository) -> None:
    iid = repo.insert_issue(
        fingerprint="fp",
        detector_key="k",
        severity="p2",
        state="active",
        first_seen_ts=1,
        last_seen_ts=1,
        title="t",
    )
    inv = repo.insert_investigation(issue_id=iid, provider="manual", dossier_md="# dossier", ts=5)
    got = repo.get_investigation(inv)
    assert got["status"] == "pending"
    assert got["response_md"] is None
    repo.attach_investigation_response(inv, "root cause: bad cable")
    got2 = repo.get_investigation(inv)
    assert got2["status"] == "answered"
    assert got2["response_md"] == "root cause: bad cable"
    assert len(repo.list_investigations(iid)) == 1


# ---------------------------------------------------------------------------
# Batched rollup reads (inventory N+1 fix): parity with the per-entity forms
# ---------------------------------------------------------------------------


def test_current_states_bulk_matches_per_entity(repo: Repository) -> None:
    a = repo.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id="aa:bb:cc:00:00:0a", name="ap-a"), ts=1000
    )
    b = repo.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id="aa:bb:cc:00:00:0b", name="ap-b"), ts=1000
    )
    repo.record_state_change(a, "firmware", "6.5.0", ts=100)
    repo.record_state_change(a, "firmware", "6.6.0", ts=200)  # latest wins
    repo.record_state_change(a, "state", "up", ts=150)
    repo.record_state_change(b, "state", "down", ts=150)

    bulk = repo.current_states_bulk([a, b])
    assert bulk[a] == repo.current_states(a)
    assert bulk[b] == repo.current_states(b)
    assert bulk[a]["firmware"] == "6.6.0"  # collapsed to newest per attr


def test_latest_samples_bulk_matches_per_entity(repo: Repository) -> None:
    a = repo.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id="aa:bb:cc:00:00:1a", name="c-a"), ts=1000
    )
    b = repo.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id="aa:bb:cc:00:00:1b", name="c-b"), ts=1000
    )
    repo.record_samples([SampleReading(a, "rssi", 10, -55.0)])
    repo.record_samples([SampleReading(a, "rssi", 20, -60.0)])  # latest per series
    repo.record_samples([SampleReading(a, "noise", 20, -95.0)])
    repo.record_samples([SampleReading(b, "rssi", 20, -70.0)])

    bulk = repo.latest_samples_bulk([a, b])
    assert bulk[a] == repo.latest_samples(a)
    assert bulk[b] == repo.latest_samples(b)
    a_rssi = next(s for s in bulk[a] if s["metric"] == "rssi")
    assert a_rssi["ts"] == 20 and a_rssi["value"] == -60.0


def test_bulk_reads_handle_empty_and_unknown_ids(repo: Repository) -> None:
    assert repo.current_states_bulk([]) == {}
    assert repo.latest_samples_bulk([]) == {}
    # An entity with no history/series is simply absent from the map.
    assert repo.current_states_bulk([999_999]) == {}
    assert repo.latest_samples_bulk([999_999]) == {}


def test_list_issues_for_entities_groups_in_one_query(repo: Repository) -> None:
    # The dossier batches related-issues across an entity's children with this;
    # it must group by entity_id, preserve newest-first order, and map a childless
    # entity to an empty list (no membership check needed at the call site).
    a = repo.upsert_entity(
        Entity(entity_type=EntityType.PORT, native_id="aa:bb:cc:00:00:0a"), ts=1000
    )
    b = repo.upsert_entity(
        Entity(entity_type=EntityType.PORT, native_id="aa:bb:cc:00:00:0b"), ts=1000
    )
    c = repo.upsert_entity(
        Entity(entity_type=EntityType.PORT, native_id="aa:bb:cc:00:00:0c"), ts=1000
    )
    repo.insert_issue(
        fingerprint="fp-a1",
        detector_key="wired.bad_cable",
        severity="p2",
        state="active",
        first_seen_ts=100,
        last_seen_ts=100,
        title="a-old",
        entity_id=a,
    )
    repo.insert_issue(
        fingerprint="fp-a2",
        detector_key="wired.bad_cable",
        severity="p2",
        state="active",
        first_seen_ts=200,
        last_seen_ts=300,
        title="a-new",
        entity_id=a,
    )
    repo.insert_issue(
        fingerprint="fp-b1",
        detector_key="wired.bad_cable",
        severity="p3",
        state="active",
        first_seen_ts=150,
        last_seen_ts=150,
        title="b-only",
        entity_id=b,
    )

    grouped = repo.list_issues_for_entities([a, b, c])
    # a: both issues, newest-first (matches list_issues ordering)
    assert [r["title"] for r in grouped[a]] == ["a-new", "a-old"]
    assert [r["title"] for r in grouped[b]] == ["b-only"]
    # c has no issues -> present but empty
    assert grouped[c] == []
    # matches the per-entity query it replaces
    assert grouped[a] == repo.list_issues(entity_id=a)
    # empty input short-circuits
    assert repo.list_issues_for_entities([]) == {}


# ---------------------------------------------------------------------------
# Reads added for the MCP server (docs/MCP_SERVER.md section 2)
# ---------------------------------------------------------------------------


def test_list_issue_history_returns_the_whole_recurrence_chain(repo: Repository) -> None:
    """Resolved instances included: that is what "has this happened before" means."""
    for first_seen, resolved in ((100, 200), (300, 400), (500, None)):
        repo.insert_issue(
            fingerprint="fp-recurring",
            detector_key="wifi.high_cu",
            severity="p2",
            state="resolved" if resolved else "active",
            first_seen_ts=first_seen,
            last_seen_ts=resolved or 600,
            resolved_ts=resolved,
            title=f"occurrence at {first_seen}",
        )
    repo.insert_issue(
        fingerprint="fp-other",
        detector_key="wifi.high_cu",
        severity="p2",
        state="active",
        first_seen_ts=550,
        last_seen_ts=600,
        title="different fingerprint",
    )

    history = repo.list_issue_history("fp-recurring")
    # Newest onset first, and scoped strictly to the one fingerprint.
    assert [r["first_seen_ts"] for r in history] == [500, 300, 100]
    assert {r["fingerprint"] for r in history} == {"fp-recurring"}
    assert repo.list_issue_history("fp-never") == []


def test_list_issue_history_honours_its_limit(repo: Repository) -> None:
    for n in range(5):
        repo.insert_issue(
            fingerprint="fp-flappy",
            detector_key="wifi.high_cu",
            severity="p3",
            state="resolved",
            first_seen_ts=100 * n,
            last_seen_ts=100 * n + 10,
            resolved_ts=100 * n + 10,
            title=f"flap {n}",
        )
    assert len(repo.list_issue_history("fp-flappy", limit=2)) == 2


def test_list_state_changes_spans_the_whole_site_newest_first(repo: Repository) -> None:
    ap = repo.upsert_entity(Entity(entity_type=EntityType.AP, native_id="aa:00", name="ap"), ts=1)
    sw = repo.upsert_entity(
        Entity(entity_type=EntityType.SWITCH, native_id="bb:00", name="sw"), ts=1
    )
    repo.record_state_change(ap, "firmware", "6.0.0", ts=100)
    repo.record_state_change(ap, "firmware", "6.1.0", ts=300)
    repo.record_state_change(sw, "speed", "1000", ts=200)

    rows = repo.list_state_changes(0, 1000)
    assert [(r["ts"], r["attr"]) for r in rows] == [
        (300, "firmware"),
        (200, "speed"),
        (100, "firmware"),
    ]

    # Half-open window: start included, end excluded.
    assert [r["ts"] for r in repo.list_state_changes(200, 300)] == [200]


def test_list_state_changes_filters_by_entity_attr_and_limit(repo: Repository) -> None:
    ap = repo.upsert_entity(Entity(entity_type=EntityType.AP, native_id="aa:01", name="ap"), ts=1)
    sw = repo.upsert_entity(
        Entity(entity_type=EntityType.SWITCH, native_id="bb:01", name="sw"), ts=1
    )
    repo.record_state_change(ap, "firmware", "6.0.0", ts=100)
    repo.record_state_change(ap, "channel", "6", ts=110)
    repo.record_state_change(sw, "firmware", "5.0.0", ts=120)

    assert {r["entity_id"] for r in repo.list_state_changes(0, 1000, entity_id=ap)} == {ap}
    assert [r["attr"] for r in repo.list_state_changes(0, 1000, attr="firmware")] == [
        "firmware",
        "firmware",
    ]
    assert len(repo.list_state_changes(0, 1000, limit=1)) == 1


def test_open_read_only_never_migrates(tmp_db_path: Path) -> None:
    """A read-only repository cannot apply migrations, so it must not try.

    ``migrate=True`` is passed explicitly here: ``read_only`` has to *win*, or a
    caller that forgets to flip both flags gets a startup failure instead of a
    read-only store.
    """
    Repository.open(tmp_db_path).close()
    repo = Repository.open(tmp_db_path, read_only=True, migrate=True)
    try:
        assert repo.list_entities() == []
        with pytest.raises(sqlite3.OperationalError):
            repo.upsert_entity(Entity(entity_type=EntityType.AP, native_id="cc:00"), ts=1)
    finally:
        repo.close()
