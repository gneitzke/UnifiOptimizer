"""Tests for the repository: interning, deltas, rollups, retention, coverage, CRUD."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.sle.classifiers import ALL_SLES
from netadmin.store.metrics import MetricKind
from netadmin.store.repository import (
    DAY_SECONDS,
    HOUR_SECONDS,
    SLE_CLIENT_AXIS_SLES,
    SLE_DEVICE_AXIS_SLES,
    Repository,
    SampleReading,
)

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


def test_delete_issue_discards_the_incident_it_roots(
    repo: Repository, switch_entity_id: int
) -> None:
    """The GitHub #34 regression: ``correlate`` roots an incident on an issue while
    it is still pending, so when that issue clears unconfirmed the discard delete
    used to hit ``FOREIGN KEY constraint failed`` and crash the whole detect pass.

    Deleting the root must succeed, take its incident (and the membership rows)
    with it, and leave the symptom issue -- which is not being discarded -- intact.
    """
    root = repo.insert_issue(
        fingerprint="fp-root",
        detector_key="wifi.tx_power_loud",
        severity="p3",
        state="pending",
        first_seen_ts=100,
        last_seen_ts=100,
        title="loud",
        entity_id=switch_entity_id,
    )
    symptom = repo.insert_issue(
        fingerprint="fp-sym",
        detector_key="wifi.coverage_hole",
        severity="p3",
        state="pending",
        first_seen_ts=100,
        last_seen_ts=100,
        title="hole",
        entity_id=switch_entity_id,
    )
    inc = repo.insert_incident(
        fingerprint="inc-1",
        root_issue_id=root,
        severity="p3",
        state="open",
        first_seen_ts=100,
        last_seen_ts=100,
        title="incident",
    )
    repo.replace_incident_members(
        inc,
        [
            {"issue_id": root, "role": "root", "rule": "r", "rationale": "why"},
            {"issue_id": symptom, "role": "symptom", "rule": "r", "rationale": "why"},
        ],
    )

    repo.delete_issue(root)  # must not raise

    assert repo.get_issue(root) is None
    assert repo.get_incident(inc) is None  # the incident it anchored is gone
    assert repo.list_incident_members(inc) == []  # membership rows gone with it
    assert repo.get_issue(symptom) is not None  # a symptom issue is freed, not deleted


def test_delete_issue_clears_every_child_reference(repo: Repository, switch_entity_id: int) -> None:
    """Every table with a FK to ``issues(id)`` is handled by the cascade, with the
    semantics each relationship calls for: membership in another incident is
    dropped, an investigation is deleted, an applied change is *detached* (its audit
    row survives with a NULL issue_id), and a later issue's ``reopened_from`` pointer
    back to this one is nulled rather than left dangling.
    """
    target = repo.insert_issue(
        fingerprint="fp-target",
        detector_key="wired.bad_cable",
        severity="p2",
        state="pending",
        first_seen_ts=100,
        last_seen_ts=100,
        title="t",
        entity_id=switch_entity_id,
    )
    other_root = repo.insert_issue(
        fingerprint="fp-otherroot",
        detector_key="wifi.airtime_saturation",
        severity="p2",
        state="active",
        first_seen_ts=100,
        last_seen_ts=100,
        title="o",
        entity_id=switch_entity_id,
    )
    # target is a *symptom* member of an incident rooted elsewhere; that incident
    # must survive, minus target's membership row.
    inc = repo.insert_incident(
        fingerprint="inc-other",
        root_issue_id=other_root,
        severity="p2",
        state="open",
        first_seen_ts=100,
        last_seen_ts=100,
        title="i",
    )
    repo.replace_incident_members(
        inc,
        [
            {"issue_id": other_root, "role": "root", "rule": "r", "rationale": "w"},
            {"issue_id": target, "role": "symptom", "rule": "r", "rationale": "w"},
        ],
    )
    repo.insert_investigation(issue_id=target, provider="manual", dossier_md="d")
    change_id = repo.insert_change(
        action="set_power",
        before={"tx": "high"},
        after={"tx": "auto"},
        status="applied",
        issue_id=target,
        entity_id=switch_entity_id,
    )
    later = repo.insert_issue(
        fingerprint="fp-later",
        detector_key="wired.bad_cable",
        severity="p2",
        state="pending",
        first_seen_ts=200,
        last_seen_ts=200,
        title="l",
        entity_id=switch_entity_id,
        reopened_from=target,
    )

    repo.delete_issue(target)  # must not raise

    assert repo.get_issue(target) is None
    assert repo.get_incident(inc) is not None  # rooted elsewhere -> survives
    assert all(m["issue_id"] != target for m in repo.list_incident_members(inc))
    assert repo.get_change(change_id)["issue_id"] is None  # detached, not deleted
    assert repo.get_issue(later)["reopened_from"] is None  # back-reference nulled


# ---------------------------------------------------------------------------
# Clear-streak resets — the count behind the "Recurring" label (Gitea #39)
# ---------------------------------------------------------------------------


def _issue(repo: Repository, fingerprint: str, entity_id: int) -> int:
    return repo.insert_issue(
        fingerprint=fingerprint,
        detector_key="wifi.sticky_client",
        severity="p2",
        state="active",
        first_seen_ts=1_000,
        last_seen_ts=2_000,
        title="sticky client",
        entity_id=entity_id,
    )


def test_streak_resets_count_only_refires_and_reopens(
    repo: Repository, switch_entity_id: int
) -> None:
    iid = _issue(repo, "fp-flap", switch_entity_id)
    # Counted: a refire that killed the clear streak, and a reopen.
    repo.record_issue_event(
        iid, "escalated", ts=1_100, detail={"reason": "refire_during_resolving"}
    )
    repo.record_issue_event(iid, "reopened", ts=1_200, detail={"reopened_from": iid})
    # Not counted: the confirm escalation, the streak's own events, everything else.
    repo.record_issue_event(iid, "escalated", ts=1_050, detail={"reason": "m_reached", "m": 3})
    repo.record_issue_event(iid, "detected", ts=1_000, detail={"severity": "p2"})
    repo.record_issue_event(iid, "resolving", ts=1_150, detail={"clear_streak": 1, "k": 6})
    repo.record_issue_event(iid, "resolved", ts=1_180, detail={"clear_streak": 6})
    repo.record_issue_event(
        iid, "fix_failed", ts=1_190, detail={"reason": "refire_during_resolving"}
    )

    assert repo.issue_streak_reset_counts([iid], since_ts=0) == {iid: 2}


def test_streak_resets_respect_the_window(repo: Repository, switch_entity_id: int) -> None:
    iid = _issue(repo, "fp-old", switch_entity_id)
    repo.record_issue_event(
        iid, "escalated", ts=1_000, detail={"reason": "refire_during_resolving"}
    )
    repo.record_issue_event(
        iid, "escalated", ts=5_000, detail={"reason": "refire_during_resolving"}
    )

    assert repo.issue_streak_reset_counts([iid], since_ts=0) == {iid: 2}
    # Inclusive floor: an event exactly on the boundary is inside the window.
    assert repo.issue_streak_reset_counts([iid], since_ts=5_000) == {iid: 1}
    assert repo.issue_streak_reset_counts([iid], since_ts=5_001) == {}


def test_streak_resets_batch_and_default_to_absent(repo: Repository, switch_entity_id: int) -> None:
    flapping = _issue(repo, "fp-a", switch_entity_id)
    steady = _issue(repo, "fp-b", switch_entity_id)
    repo.record_issue_event(
        flapping, "escalated", ts=1_100, detail={"reason": "refire_during_resolving"}
    )
    repo.record_issue_event(steady, "escalated", ts=1_100, detail={"reason": "m_reached"})

    counts = repo.issue_streak_reset_counts([flapping, steady], since_ts=0)
    # An issue with no resets is ABSENT, not zero -- callers default it.
    assert counts == {flapping: 1}
    assert repo.issue_streak_reset_counts([], since_ts=0) == {}


def test_streak_resets_survive_a_malformed_detail_blob(
    repo: Repository, switch_entity_id: int
) -> None:
    """One unreadable detail costs its own row, never the whole issues list."""
    iid = _issue(repo, "fp-junk", switch_entity_id)
    repo.record_issue_event(
        iid, "escalated", ts=1_100, detail={"reason": "refire_during_resolving"}
    )
    with repo._write() as conn:  # noqa: SLF001 - forcing a shape no writer produces
        conn.execute(
            "INSERT INTO issue_events (issue_id, ts, kind, detail) VALUES (?,?,?,?)",
            (iid, 1_200, "escalated", "not json at all"),
        )

    assert repo.issue_streak_reset_counts([iid], since_ts=0) == {iid: 1}


def test_streak_resets_ignore_occurrences(repo: Repository, switch_entity_id: int) -> None:
    """The signal is event rows, not the fire counter (Gitea #39).

    A steadily-burning issue racks up occurrences without ever flapping; the
    reset count is what says "this keeps coming back".
    """
    burning = _issue(repo, "fp-burning", switch_entity_id)
    repo.update_issue(burning, occurrences=98)

    assert repo.issue_streak_reset_counts([burning], since_ts=0) == {}


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


def test_delete_sle_minutes_clears_only_its_bucket(repo: Repository, switch_entity_id: int) -> None:
    repo.upsert_sle_minute(
        bucket_ts=0, sle="coverage", classifier="ok", entity_id=switch_entity_id, minutes=4.0
    )
    repo.upsert_sle_minute(
        bucket_ts=0,
        sle="coverage",
        classifier="weak_signal",
        entity_id=switch_entity_id,
        minutes=1.0,
    )
    repo.upsert_sle_minute(
        bucket_ts=300, sle="coverage", classifier="ok", entity_id=switch_entity_id, minutes=5.0
    )

    deleted = repo.delete_sle_minutes(0)

    assert deleted == 2
    # an ungrouped query always returns one row; SUM() over no matching rows is
    # NULL, not an empty result set
    assert repo.query_sle_minutes(0, 300, group_by=()) == [{"minutes": None}]
    # the other bucket is untouched
    other = repo.query_sle_minutes(300, 600, group_by=())
    assert other[0]["minutes"] == 5.0


def test_delete_sle_minutes_empty_bucket_is_a_noop(repo: Repository) -> None:
    assert repo.delete_sle_minutes(12345) == 0


# ---------------------------------------------------------------------------
# SLE axes (Gitea #36): client-minutes and device down-minutes never merge
# ---------------------------------------------------------------------------


def test_the_two_sle_axes_partition_every_sle() -> None:
    """A new SLE cannot quietly land on neither axis, or on both.

    The store mirrors the SLE names rather than importing them (it is the layer
    below the engine), so this is the guard against the two lists drifting.
    """
    client_axis = set(SLE_CLIENT_AXIS_SLES)
    device_axis = set(SLE_DEVICE_AXIS_SLES)
    assert client_axis | device_axis == set(ALL_SLES)
    assert client_axis & device_axis == set()


def _seed_two_axes(repo: Repository) -> tuple[int, int]:
    """One AP down 10 minutes, with one client losing 4 minutes pinned on it."""
    ap = repo.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id="aa:bb:cc:00:00:0a", name="ap"), ts=0
    )
    client = repo.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id="11:22:33:44:55:0a", name="phone"), ts=0
    )
    repo.upsert_sle_minute(
        bucket_ts=0,
        sle="coverage",
        classifier="weak_signal",
        entity_id=client,
        minutes=4.0,
        attributed_entity_id=ap,
    )
    repo.upsert_sle_minute(
        bucket_ts=0,
        sle="infra",
        classifier="ap_down",
        entity_id=ap,
        minutes=10.0,
        attributed_entity_id=ap,
    )
    return ap, client


def test_fail_minutes_by_attributed_can_be_read_per_axis(repo: Repository) -> None:
    ap, _ = _seed_two_axes(repo)
    client_axis = repo.sle_fail_minutes_by_attributed(0, 300, sles=SLE_CLIENT_AXIS_SLES)
    device_axis = repo.sle_fail_minutes_by_attributed(0, 300, sles=SLE_DEVICE_AXIS_SLES)
    assert client_axis[ap] == 4.0
    assert device_axis[ap] == 10.0
    # The default still sums both — the legacy ranking input, documented as
    # unsafe to show as "minutes clients lost".
    assert repo.sle_fail_minutes_by_attributed(0, 300)[ap] == 14.0
    # An empty axis means no axis, never "everything".
    assert repo.sle_fail_minutes_by_attributed(0, 300, sles=()) == {}


def test_axis_spans_report_the_two_axes_independently(repo: Repository) -> None:
    _seed_two_axes(repo)
    assert repo.sle_minutes_axis_spans(0, 300) == {"client": (0, 0), "infra": (0, 0)}
    # A window the engine never judged has no span on either axis, which is how
    # "not measured" stays distinct from "measured, nothing failed".
    assert repo.sle_minutes_axis_spans(600, 900) == {"client": None, "infra": None}


def test_axis_span_is_absent_for_an_axis_with_no_rows(repo: Repository) -> None:
    ap = repo.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id="aa:bb:cc:00:00:0b", name="ap"), ts=0
    )
    repo.upsert_sle_minute(
        bucket_ts=0, sle="infra", classifier="ap_down", entity_id=ap, minutes=5.0
    )
    assert repo.sle_minutes_axis_spans(0, 300) == {"client": None, "infra": (0, 0)}


def test_measured_client_count_is_the_denominator(repo: Repository) -> None:
    """Counts clients the engine judged, pass or fail — never the devices."""
    _seed_two_axes(repo)
    assert repo.sle_measured_client_count(0, 300) == 1
    assert repo.sle_measured_client_count(600, 900) == 0


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


# ---------------------------------------------------------------------------
# B4: observed_event_coverage -- the honest event-feed gap signal
#
# POSITIVE-LIVENESS redesign: coverage is credited only across spans carrying WS
# liveness HEARTBEATS (record_ws_heartbeat), never through end_ts on a still-open
# 'connected' row. Beats no more than _WS_HEARTBEAT_MAX_GAP_S apart chain into a
# continuous covered run; coverage ends at the LAST beat.
# ---------------------------------------------------------------------------
def _seed_heartbeats(repo: Repository, start: int, end: int, *, step: int = 60) -> None:
    """Seed WS liveness heartbeats across ``[start, end]`` at ``step``.

    Both endpoints are guaranteed to carry a beat regardless of alignment, so the
    covered run reaches exactly ``[start, end]``.
    """
    ts = start
    while ts < end:
        repo.record_ws_heartbeat(ts=ts)
        ts += step
    repo.record_ws_heartbeat(ts=end)


def test_observed_event_coverage_credits_healthy_connected_ws(repo: Repository) -> None:
    """B4(c): a healthy WS-only deployment that is CONNECTED and draining emits a
    steady stream of liveness heartbeats, so it reads as (effectively) fully
    covered even with NO history-catchup rows -- 'no catch-up rows yet' must not
    mean 'frozen forever'. Coverage is bounded by the LAST heartbeat (never
    credited past it), so it is one heartbeat cadence short of the window edge,
    which is still far above the 0.5 sufficiency floor."""
    now = 2_000_000
    start = now - 3600
    # A prior beat just before the window bridges the leading edge; beats then run
    # every 60 s right up to the window end.
    _seed_heartbeats(repo, start - 60, now - 1, step=60)
    cov = repo.observed_event_coverage(start, now)
    assert cov >= 0.99
    assert cov <= 1.0


def test_observed_event_coverage_does_not_bridge_a_real_outage(repo: Repository) -> None:
    """B4(f) (verifier round 4): heartbeats that resume after an outage must NOT
    bridge the gap. A feed connected only ~30 s out of every 180 s (down or
    reconnecting the rest of the time) must read as mostly UNCOVERED -- otherwise
    a real event-based issue false-resolves across the outage. The old 150 s
    bridge spanned these gaps and reported ~97% coverage; the tightened bridge
    (~2.5x the 30 s beat cadence) leaves the outages as real holes."""
    now = 2_000_000
    start = now - 3600
    # Two beats 30 s apart, then a 150 s silent gap (5 missed beats), repeating.
    t = start
    while t < now:
        repo.record_ws_heartbeat(ts=t)
        repo.record_ws_heartbeat(ts=t + 30)
        t += 180
    cov = repo.observed_event_coverage(start, now)
    # ~30 covered out of every 180 -> well under the 0.9 event-gap sufficiency
    # floor, so event-based verdicts correctly freeze to UNKNOWN.
    assert cov < 0.3


def test_observed_event_coverage_no_heartbeats_credits_nothing(
    repo: Repository,
) -> None:
    """B4(a): a socket that emitted no liveness heartbeats credits ZERO coverage --
    even if 'started'/'connected' transition rows exist. Those transitions drive
    only the health string; a feed with no positive liveness was not observed.
    Under the old close-event design a dangling 'connected' read as 100% and
    false-cleared real event issues."""
    now = 2_000_000
    start = now - 3600
    # Transition rows present, but not one heartbeat: no positive evidence.
    repo.record_poll_run(job="ws", ok=True, ts=start - 100, error="started", source="live")
    repo.record_poll_run(job="ws", ok=True, ts=start - 50, error="connected", source="live")
    assert repo.observed_event_coverage(start, now) == 0.0


def test_observed_event_coverage_ws_heartbeats_inside_window(repo: Repository) -> None:
    """A feed that only began heartbeating partway through the window covers only
    from the first beat onward -- the earlier, beat-less span credits nothing."""
    now = 2_000_000
    start = now - 3600
    # Heartbeats only across the second half [start+1800 .. now].
    _seed_heartbeats(repo, start + 1800, now - 1, step=60)
    cov = repo.observed_event_coverage(start, now)
    # ~0.5 (bounded above by the last beat), comfortably under full.
    assert cov == pytest.approx(0.5, abs=0.02)
    assert cov < 0.6


def test_observed_event_coverage_heartbeats_stop_ends_interval(
    repo: Repository,
) -> None:
    """B4(b): heartbeats that stop (a drop, a shutdown, or a stalled feed) end
    coverage at the LAST beat, not through end_ts. Beats run for the first 600 s
    of the hour then cease; coverage is ~0.167, below the sufficiency floor ->
    UNKNOWN. The old code left the interval open through end_ts (a false 100%)."""
    now = 2_000_000
    start = now - 3600
    # A prior beat bridges the leading edge; beats run start..start+600 then stop.
    _seed_heartbeats(repo, start - 60, start + 600, step=60)
    cov = repo.observed_event_coverage(start, now)
    assert cov == pytest.approx(600 / 3600, abs=0.02)
    assert cov < 0.9


def test_observed_event_coverage_large_gap_reads_as_uncovered(repo: Repository) -> None:
    """B4(c): a real large gap still freezes. Two short bursts of heartbeats with a
    gap far larger than _WS_HEARTBEAT_MAX_GAP_S between them do NOT chain: the
    empty middle is a genuine hole, so coverage stays well below full -> UNKNOWN."""
    now = 2_000_000
    start = now - 3600
    _seed_heartbeats(repo, start, start + 300, step=60)      # early burst
    _seed_heartbeats(repo, now - 360, now - 1, step=60)      # late burst
    cov = repo.observed_event_coverage(start, now)
    # ~ (300 + ~360) / 3600 -- the big middle gap is uncovered.
    assert cov < 0.3


def test_observed_event_coverage_unions_ws_and_history(repo: Repository) -> None:
    """WS heartbeat intervals and completed history reads are merged, not double
    counted: heartbeats cover the first 600 s, a catch-up row the last 600 s ->
    ~0.33."""
    now = 2_000_000
    start = now - 3600
    _seed_heartbeats(repo, start, start + 600, step=60)
    repo.record_ingest_coverage(
        kind="event_history", scope="site", interval="retained",
        start_ts=now - 600, end_ts=now, status="complete",
    )
    assert repo.observed_event_coverage(start, now) == pytest.approx(1200 / 3600, abs=0.02)


def test_observed_event_coverage_stale_disconnect_write_cannot_overcredit(
    repo: Repository,
) -> None:
    """B4: a MISSING or failed 'disconnected' close row can no longer over-credit.
    Coverage is bounded by the last heartbeat regardless of any close row: here a
    dangling 'connected' with NO closing 'disconnected' and beats only over the
    first 600 s reads ~0.167, not 100%."""
    now = 2_000_000
    start = now - 3600
    repo.record_poll_run(job="ws", ok=True, ts=start - 100, error="connected", source="live")
    _seed_heartbeats(repo, start - 60, start + 600, step=60)
    # No 'disconnected' row was ever written (the failed-close path).
    cov = repo.observed_event_coverage(start, now)
    assert cov == pytest.approx(600 / 3600, abs=0.02)
    assert cov < 0.9


# ---------------------------------------------------------------------------
# New-bug: no DDL on the read path; a missing coverage table reads as unknown
# ---------------------------------------------------------------------------
def test_observed_event_coverage_missing_table_returns_unknown(repo: Repository) -> None:
    """The reader must tolerate an absent ingest_coverage table without raising and
    without issuing DDL (which a read-only connection would reject)."""
    with repo._write() as conn:
        conn.execute("DROP TABLE IF EXISTS ingest_coverage")
    assert not repo._table_exists("ingest_coverage")
    assert repo.observed_event_coverage(1000, 2000) == 0.0
    # The read issued no CREATE TABLE: the table is still absent.
    assert not repo._table_exists("ingest_coverage")
    # The other ledger readers degrade the same way rather than raising.
    assert repo.failed_ingest_coverage(kind="event_history", scope="site") == []
    assert repo.latest_ingest_coverage_end(kind="event_history", scope="site") is None


def test_observed_event_coverage_read_only_missing_table(tmp_db_path: Path) -> None:
    """The exact new-bug repro: on a migrated database whose coverage table is
    absent, a READ-ONLY connection reads unknown coverage instead of raising
    OperationalError from a lazily-issued CREATE TABLE."""
    rw = Repository.open(tmp_db_path)
    with rw._write() as conn:
        conn.execute("DROP TABLE ingest_coverage")
    rw.close()
    ro = Repository.open(tmp_db_path, read_only=True, migrate=True)
    try:
        assert ro.observed_event_coverage(1000, 2000) == 0.0
    finally:
        ro.close()


# ---------------------------------------------------------------------------
# C7: reconciliation must not let unrepairable AP events starve repairable ones
# ---------------------------------------------------------------------------
def test_unresolved_events_ap_flood_does_not_starve_repairable_client(repo: Repository) -> None:
    """C7: ordinary AP events legitimately have NO related entity, so their
    related_entity_id is permanently NULL. A flood of them (oldest) must not fill
    the LIMIT window and starve a later, genuinely-repairable client event whose
    primary entity has not resolved yet."""
    ap = repo.upsert_entity(Entity(entity_type=EntityType.AP, native_id="ap:mac"), ts=1000)
    # 600 AP events: primary resolved to the AP, related permanently NULL.
    for i in range(600):
        repo.record_event(
            ts=1000 + i, key="EVT_AP_Lost_Contact", entity_id=ap,
            related_entity_id=None, native_id=f"apev-{i}", data={"ap": "ap:mac"},
        )
    # A repairable client event arriving later: its client is named in the payload
    # (so it can resolve) but is not yet in inventory.
    client_ev = repo.record_event(
        ts=9000, key="EVT_WU_Disconnected", entity_id=None,
        related_entity_id=None, native_id="cliev", data={"user": "cli:mac"},
    )

    rows = repo.unresolved_events(limit=500)
    returned = {int(r["id"]) for r in rows}
    # The unrepairable AP flood is excluded; the repairable client event is present.
    assert client_ev in returned
    assert len(rows) == 1


def test_unresolved_events_keeps_client_with_pending_related(repo: Repository) -> None:
    """A client event whose primary (client) resolved but whose from-AP is still
    pending (related NULL) IS repairable and must still be selected."""
    client = repo.upsert_entity(Entity(entity_type=EntityType.CLIENT, native_id="cli:mac"), ts=1000)
    # The from-AP is named in the payload but not yet in inventory (still pending):
    # its identity exists, so the row is genuinely repairable and must be selected.
    ev = repo.record_event(
        ts=2000, key="EVT_WU_Roam", entity_id=client,
        related_entity_id=None, native_id="roamev", data={"ap_from": "apx:mac"},
    )
    returned = {int(r["id"]) for r in repo.unresolved_events(limit=500)}
    assert ev in returned


def test_w15a4_bool_port_event_not_falsely_resolvable_against_int_port(
    repo: Repository,
) -> None:
    """#w15a-4: a bool-port STP row must not be falsely 'resolvable' against a real
    integer port, or it floats to the head of the reconcile window forever and
    starves newer repairable rows.

    The normalizer stores a bool ``port: true`` STP event's native_id as
    ``"<sw>:True"`` (pre-fix) while the resolvability SQL coerced the same JSON
    boolean to ``"<sw>:1"``. With a real integer port ``"<sw>:1"`` in inventory the
    SQL called the row resolvable, but reconcile could NEVER fill it (the
    normalizer's ``"<sw>:True"`` matches no entity) -- the row was retained even
    after its attempts were exhausted, filling the LIMIT window and starving a
    newer, genuinely-repairable row. After the fix, both sides treat a bool port as
    NOT a port index (routed to the switch); with the switch absent the bool row is
    correctly unresolvable and, once parked, never starves the newer row.
    """
    from netadmin.ingest.events import EventNormalizer
    from netadmin.store.repository import _EVENT_RECONCILE_MAX_ATTEMPTS

    unknown_sw = "02:00:de:ad:be:ef"  # switch deliberately NOT in inventory
    # A real INTEGER port "<sw>:1" exists -- the entity the buggy SQL matched.
    repo.upsert_entity(
        Entity(entity_type=EntityType.PORT, native_id=f"{unknown_sw}:1"), ts=500
    )
    bool_ev = repo.record_event(
        ts=1000, key="EVT_SW_StpPortBlocking", entity_id=None, related_entity_id=None,
        native_id="stp-bool",
        data={"key": "EVT_SW_StpPortBlocking", "time": 1000 * 1000,
              "sw": unknown_sw, "port": True},
    )
    assert bool_ev is not None
    # Park it: exhaust its attempts so it is retained ONLY if (falsely) resolvable.
    for _ in range(_EVENT_RECONCILE_MAX_ATTEMPTS):
        repo.bump_event_reconcile_attempts([bool_ev])

    # A newer, genuinely repairable STP row: its INTEGER port IS in inventory.
    good_sw = "02:00:11:22:33:aa"
    good_port = repo.upsert_entity(
        Entity(entity_type=EntityType.PORT, native_id=f"{good_sw}:2"), ts=8000
    )
    newer = repo.record_event(
        ts=9000, key="EVT_SW_StpPortBlocking", entity_id=None, related_entity_id=None,
        native_id="stp-good",
        data={"key": "EVT_SW_StpPortBlocking", "time": 9000 * 1000,
              "sw": good_sw, "port": 2},
    )
    assert newer is not None

    # The bool row is NOT falsely resolvable, so the parked row does not starve the
    # window: with limit=1, only the genuinely-resolvable newer row is selected.
    # (Pre-fix, the bool row is falsely resolvable, older by ts, and wins the slot.)
    selected = {int(r["id"]) for r in repo.unresolved_events(limit=1)}
    assert selected == {newer}

    # End-to-end: reconcile fills the newer row's real port; the bool row is never
    # falsely repaired and stays NULL.
    repaired = EventNormalizer(repo).reconcile_unresolved(limit=500)
    assert repaired == 1
    assert repo._conn.execute(
        "SELECT entity_id FROM events WHERE id=?", (newer,)
    ).fetchone()["entity_id"] == good_port
    assert repo._conn.execute(
        "SELECT entity_id FROM events WHERE id=?", (bool_ev,)
    ).fetchone()["entity_id"] is None


def test_reconcile_parks_unresolvable_and_reaches_newer_repairable(repo: Repository) -> None:
    """P2: 500 OLDER client events with a resolved client but NO from-AP in the
    payload can NEVER resolve their related reference.  Being oldest, the prior
    filter re-selected them every pass, filled the LIMIT window, and starved a
    newer, genuinely-repairable client event -- while dishonestly reporting 500
    repairs each pass.  The fix parks rows with no resolvable identity, so the
    newer event is reached and enriched, and the reported count reflects only the
    single real enrichment (not 500)."""
    from netadmin.ingest.events import EventNormalizer

    old_client = repo.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id="cli-old:mac"), ts=500
    )
    # 500 older client events: client resolved, related NULL, and the payload
    # names NO AP/switch -- there is nothing to resolve the from-AP from, ever.
    for i in range(500):
        repo.record_event(
            ts=1000 + i, key="EVT_WU_Disconnected", entity_id=old_client,
            related_entity_id=None, native_id=f"oldev-{i}",
            data={"key": "EVT_WU_Disconnected", "time": (1000 + i) * 1000,
                  "user": "cli-old:mac"},
        )
    # A newer, genuinely repairable client event: its from-AP IS in inventory now.
    new_client = repo.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id="cli-new:mac"), ts=8000
    )
    new_ap = repo.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id="ap-new:mac"), ts=8000
    )
    newer_ev = repo.record_event(
        ts=9000, key="EVT_WU_Connected", entity_id=new_client,
        related_entity_id=None, native_id="newev",
        data={"key": "EVT_WU_Connected", "time": 9000 * 1000,
              "user": "cli-new:mac", "ap": "ap-new:mac"},
    )

    # Selection makes fair progress: the 500 unresolvable rows are parked, so the
    # newer repairable row (which the old LIMIT-500 window would have starved) is
    # the only thing returned.
    selected = {int(r["id"]) for r in repo.unresolved_events(limit=500)}
    assert selected == {newer_ev}

    # End-to-end reconcile: the reported repair count is the ONE real enrichment,
    # not 500, and the newer event's related reference is actually filled.
    repaired = EventNormalizer(repo).reconcile_unresolved(limit=500)
    assert repaired == 1

    row = repo._conn.execute(
        "SELECT related_entity_id FROM events WHERE id=?", (newer_ev,)
    ).fetchone()
    assert row["related_entity_id"] == new_ap
    # The parked 500 remain untouched (still NULL) -- never falsely counted.
    still_null = repo._conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE related_entity_id IS NULL "
        "AND entity_id=?", (old_client,)
    ).fetchone()["n"]
    assert still_null == 500


def test_reconcile_reaches_newer_resolvable_behind_pending_flood(repo: Repository) -> None:
    """P2 (residual): the prior fix parked rows that name NO candidate MAC, but a
    row that DOES name a from-AP simply NOT YET in inventory is a legitimate
    pending row -- it must keep being retried in case its AP appears. A flood of
    500 such pending rows (oldest) still filled the oldest-first LIMIT window on
    every pass and starved a newer row whose AP already IS in inventory: three
    passes each selected the same 500, returned 0 repairs, and left the newer
    ref NULL. Fair progress orders currently-resolvable rows first, so the newer
    resolvable row is reached and enriched no matter how many not-yet-resolvable
    older rows precede it, and the pending flood reports no false repairs."""
    from netadmin.ingest.events import EventNormalizer

    old_client = repo.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id="cli-old:mac"), ts=500
    )
    # 500 older client roam events: client resolved, related NULL, and the payload
    # NAMES a from-AP that is simply NOT (yet) in inventory -- genuinely pending,
    # not junk. The prior fix keeps selecting these (right), but they must not
    # starve a newer row that CAN resolve now.
    for i in range(500):
        repo.record_event(
            ts=1000 + i, key="EVT_WU_Roam", entity_id=old_client,
            related_entity_id=None, native_id=f"pendev-{i}",
            data={"key": "EVT_WU_Roam", "time": (1000 + i) * 1000,
                  "user": "cli-old:mac", "ap_from": "absent-ap:mac"},
        )
    # A newer roam event whose from-AP IS already in inventory -> resolvable now.
    new_client = repo.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id="cli-new:mac"), ts=8000
    )
    new_ap = repo.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id="ap-new:mac"), ts=8000
    )
    newer_ev = repo.record_event(
        ts=9000, key="EVT_WU_Roam", entity_id=new_client,
        related_entity_id=None, native_id="newev",
        data={"key": "EVT_WU_Roam", "time": 9000 * 1000,
              "user": "cli-new:mac", "ap_from": "ap-new:mac"},
    )

    # Resolvability preference floats the newer resolvable row to the FRONT of the
    # oldest-first window, so it is selected even behind 500 older pending rows.
    selected = repo.unresolved_events(limit=500)
    assert int(selected[0]["id"]) == newer_ev
    assert newer_ev in {int(r["id"]) for r in selected}

    # End-to-end reconcile: exactly ONE real enrichment (the newer row), and the
    # 500 pending rows are neither filled nor falsely counted.
    repaired = EventNormalizer(repo).reconcile_unresolved(limit=500)
    assert repaired == 1
    row = repo._conn.execute(
        "SELECT related_entity_id FROM events WHERE id=?", (newer_ev,)
    ).fetchone()
    assert row["related_entity_id"] == new_ap
    still_null = repo._conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE related_entity_id IS NULL "
        "AND entity_id=?", (old_client,)
    ).fetchone()["n"]
    assert still_null == 500


def test_pending_row_parks_after_cap_then_resolves_when_ap_appears(
    repo: Repository,
) -> None:
    """A pending from-AP that NEVER appears must eventually stop consuming the
    LIMIT window (bounded retry), yet a pending row whose AP DOES later appear
    must still resolve. Both are the same row over time: it is retried, parked
    once its attempt budget is spent, and un-parked the instant its AP shows up."""
    from netadmin.ingest.events import EventNormalizer
    from netadmin.store.repository import _EVENT_RECONCILE_MAX_ATTEMPTS

    client = repo.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id="cli:mac"), ts=500
    )
    ev = repo.record_event(
        ts=1000, key="EVT_WU_Roam", entity_id=client, related_entity_id=None,
        native_id="pending", data={"key": "EVT_WU_Roam", "time": 1000 * 1000,
                                    "user": "cli:mac", "ap_from": "late-ap:mac"},
    )

    # While its AP is absent the row is still selected (it may yet resolve) and a
    # reconcile makes no false repair.
    assert ev in {int(r["id"]) for r in repo.unresolved_events(limit=500)}
    assert EventNormalizer(repo).reconcile_unresolved(limit=500) == 0

    # Simulate the reconcile caller recording each fruitless pass (the caller bumps
    # the rows it selected but could not fill). Once the attempt budget is spent
    # the still-unresolvable row is parked out of the window.
    for _ in range(_EVENT_RECONCILE_MAX_ATTEMPTS):
        repo.bump_event_reconcile_attempts([ev])
    assert ev not in {int(r["id"]) for r in repo.unresolved_events(limit=500)}

    # Its AP finally appears: the row is resolvable again and re-admitted despite
    # its spent attempt budget, then repaired on the next pass (requirement 2).
    late_ap = repo.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id="late-ap:mac"), ts=9000
    )
    assert ev in {int(r["id"]) for r in repo.unresolved_events(limit=500)}
    assert EventNormalizer(repo).reconcile_unresolved(limit=500) == 1
    row = repo._conn.execute(
        "SELECT related_entity_id FROM events WHERE id=?", (ev,)
    ).fetchone()
    assert row["related_entity_id"] == late_ap


def test_unresolved_events_degrades_when_attempt_column_absent(
    tmp_db_path: Path,
) -> None:
    """The bounded-retry column (0013) is optional: on a database migrated only to
    0012 the counter reads as a constant 0 and the resolvability preference alone
    still prevents starvation -- and the read issues no DDL."""
    rw = Repository.open(tmp_db_path)
    with rw._write() as conn:
        conn.execute("ALTER TABLE events DROP COLUMN reconcile_attempts")
    assert not rw._column_exists("events", "reconcile_attempts")

    client = rw.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id="cli:mac"), ts=500
    )
    ap = rw.upsert_entity(Entity(entity_type=EntityType.AP, native_id="ap:mac"), ts=500)
    for i in range(3):
        rw.record_event(
            ts=1000 + i, key="EVT_WU_Roam", entity_id=client, related_entity_id=None,
            native_id=f"pend-{i}", data={"key": "EVT_WU_Roam", "time": (1000 + i) * 1000,
                                         "user": "cli:mac", "ap_from": "absent:mac"},
        )
    resolvable_ev = rw.record_event(
        ts=5000, key="EVT_WU_Roam", entity_id=client, related_entity_id=None,
        native_id="ok", data={"key": "EVT_WU_Roam", "time": 5000 * 1000,
                               "user": "cli:mac", "ap_from": "ap:mac"},
    )
    # No column, no raise -- and the resolvable row is still ranked first.
    selected = rw.unresolved_events(limit=500)
    assert int(selected[0]["id"]) == resolvable_ev
    # bump is a safe no-op when the column is absent.
    assert rw.bump_event_reconcile_attempts([resolvable_ev]) == 0
    rw.close()


def test_wrong_precedence_mac_is_not_resolvable_and_does_not_starve(
    repo: Repository,
) -> None:
    """Finding #9: the SQL ``resolvable`` predicate must mirror the NORMALIZER's
    per-column precedence, not merely "some candidate MAC exists".

    500 older non-roam client events each name an ``ap`` that is NOT in inventory
    and an ``sw`` that IS. The normalizer routes a non-roam client event's related
    reference to the AP when ``ap`` is present -- it never falls through to the
    switch -- so these rows can NEVER resolve. The old predicate flagged them
    resolvable because the switch existed, so (being ordered resolvable-first)
    they filled the LIMIT-500 window ahead of a newer, genuinely-resolvable row
    whose AP *is* in inventory, starving it: 0 real repairs, newer ref left NULL.
    With the precedence-faithful predicate the 500 are correctly NOT resolvable,
    the newer row floats to the front and is the one that resolves."""
    from netadmin.ingest.events import EventNormalizer

    old_client = repo.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id="cli-old:mac"), ts=500
    )
    # The switch these older events name IS in inventory -- but it is the WRONG
    # MAC: the normalizer would use the (absent) ap, never this switch.
    repo.upsert_entity(Entity(entity_type=EntityType.SWITCH, native_id="sw:mac"), ts=500)
    for i in range(500):
        repo.record_event(
            ts=1000 + i, key="EVT_WU_Disconnected", entity_id=old_client,
            related_entity_id=None, native_id=f"wrongprec-{i}",
            data={"key": "EVT_WU_Disconnected", "time": (1000 + i) * 1000,
                  "user": "cli-old:mac", "ap": "absent-ap:mac", "sw": "sw:mac"},
        )
    # A newer, genuinely repairable non-roam client event: its AP IS in inventory.
    new_client = repo.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id="cli-new:mac"), ts=8000
    )
    new_ap = repo.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id="ap-new:mac"), ts=8000
    )
    newer_ev = repo.record_event(
        ts=9000, key="EVT_WU_Connected", entity_id=new_client,
        related_entity_id=None, native_id="newev",
        data={"key": "EVT_WU_Connected", "time": 9000 * 1000,
              "user": "cli-new:mac", "ap": "ap-new:mac"},
    )

    # LIMIT exactly the flood size: under the old (wrong) predicate all 501 rows
    # were "resolvable" and ordered by ts, so the newest (ts=9000) fell off the
    # 500-row window and was starved. The precedence-faithful predicate marks the
    # 500 not-resolvable, so the one truly-resolvable row leads the window.
    selected = repo.unresolved_events(limit=500)
    assert int(selected[0]["id"]) == newer_ev

    # End-to-end: exactly ONE real enrichment (the newer row); the 500 wrong-MAC
    # rows are neither filled nor falsely counted.
    repaired = EventNormalizer(repo).reconcile_unresolved(limit=500)
    assert repaired == 1
    row = repo._conn.execute(
        "SELECT related_entity_id FROM events WHERE id=?", (newer_ev,)
    ).fetchone()
    assert row["related_entity_id"] == new_ap
    still_null = repo._conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE related_entity_id IS NULL "
        "AND entity_id=?", (old_client,)
    ).fetchone()["n"]
    assert still_null == 500


def test_stp_absent_port_does_not_starve_and_resolves_when_port_appears(
    repo: Repository,
) -> None:
    """Finding #4 (STP routing): a port-scoped switch event (EVT_SW_StpPortBlocking)
    is attributed by the normalizer to the PORT entity (native_id "<sw>:<port>"),
    NOT the switch. The old predicate flagged such a row resolvable merely because
    the SWITCH exists, so 500 STP events for an ABSENT port floated to the head of
    the LIMIT window (resolvable-first) and starved a newer, genuinely-repairable
    client event: it got ZERO repairs and its ref stayed NULL. The precedence-
    faithful predicate consults the PORT, so a port-not-yet-present STP row is
    correctly NOT-yet-resolvable (parked, not starving) -- and becomes resolvable
    exactly when its port entity appears, repairing all 500."""
    from netadmin.ingest.events import EventNormalizer

    # The switch IS in inventory (so each STP row's related=switch is already set at
    # ingest, exactly as normalize() would). Its PORT is NOT yet present, so the
    # primary entity (the port) cannot resolve.
    sw = repo.upsert_entity(
        Entity(entity_type=EntityType.SWITCH, native_id="sw:mac"), ts=500
    )
    for i in range(500):
        repo.record_event(
            ts=1000 + i, key="EVT_SW_StpPortBlocking", entity_id=None,
            related_entity_id=sw, native_id=f"stp-{i}",
            data={"key": "EVT_SW_StpPortBlocking", "time": (1000 + i) * 1000,
                  "sw": "sw:mac", "port": 7},
        )
    # A newer, genuinely repairable client event whose AP IS in inventory.
    new_client = repo.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id="cli-new:mac"), ts=8000
    )
    new_ap = repo.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id="ap-new:mac"), ts=8000
    )
    newer_ev = repo.record_event(
        ts=9000, key="EVT_WU_Connected", entity_id=new_client,
        related_entity_id=None, native_id="newev",
        data={"key": "EVT_WU_Connected", "time": 9000 * 1000,
              "user": "cli-new:mac", "ap": "ap-new:mac"},
    )

    # (a) LIMIT exactly the flood size: under the old predicate all 501 rows were
    # "resolvable" (switch exists) and ordered by ts, so the newest (ts=9000) fell
    # off the 500-row window and was starved. Now the 500 STP rows are NOT
    # resolvable, so the one truly-resolvable row leads the window.
    selected = repo.unresolved_events(limit=500)
    assert int(selected[0]["id"]) == newer_ev

    repaired = EventNormalizer(repo).reconcile_unresolved(limit=500)
    assert repaired == 1
    assert repo._conn.execute(
        "SELECT related_entity_id FROM events WHERE id=?", (newer_ev,)
    ).fetchone()["related_entity_id"] == new_ap
    # The STP rows' primary entity (the port) is still unresolved -- parked, not
    # falsely counted as repaired.
    stp_null = repo._conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE entity_id IS NULL "
        "AND native_id LIKE 'stp-%'"
    ).fetchone()["n"]
    assert stp_null == 500

    # (b) The missing PORT finally appears (native_id "<sw_mac>:<port_idx>", exactly
    # how ingest/mapping.py keys ports). Every STP row is now resolvable and repairs
    # to the port on the next pass.
    port = repo.upsert_entity(
        Entity(entity_type=EntityType.PORT, native_id="sw:mac:7"), ts=9500
    )
    repaired_now = EventNormalizer(repo).reconcile_unresolved(limit=500)
    assert repaired_now == 500
    filled = repo._conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE entity_id=? AND native_id LIKE 'stp-%'",
        (port,),
    ).fetchone()["n"]
    assert filled == 500


def test_switch_scoped_event_still_resolves_on_the_switch(repo: Repository) -> None:
    """Finding #4 guard: a switch-scoped event that merely CARRIES a port field
    (EVT_SW_PoeOverload) is NOT in the port-scoped set, so the normalizer routes it
    to the SWITCH. The predicate must key off the event KEY, not the presence of a
    ``port`` field -- so this row resolves on the switch (not a phantom port)."""
    from netadmin.ingest.events import EventNormalizer

    sw = repo.upsert_entity(
        Entity(entity_type=EntityType.SWITCH, native_id="sw:mac"), ts=500
    )
    ev = repo.record_event(
        ts=1000, key="EVT_SW_PoeOverload", entity_id=None, related_entity_id=None,
        native_id="poe", data={"key": "EVT_SW_PoeOverload", "time": 1000 * 1000,
                               "sw": "sw:mac", "port": 3},
    )
    # Resolvable now (the switch exists), so it is selected and repaired to the switch.
    assert ev in {int(r["id"]) for r in repo.unresolved_events(limit=500)}
    assert EventNormalizer(repo).reconcile_unresolved(limit=500) == 1
    assert repo._conn.execute(
        "SELECT entity_id FROM events WHERE id=?", (ev,)
    ).fetchone()["entity_id"] == sw


def test_unresolved_events_degrades_when_table_absent(tmp_db_path: Path) -> None:
    """Finding #9: on a database whose ``events`` table is absent (dropped, or a
    query-only replica that never provisioned it) the reconcile read must degrade
    to empty rather than raising OperationalError ("no such table"). The guard is
    on the missing TABLE, not just a missing column."""
    rw = Repository.open(tmp_db_path)
    with rw._write() as conn:
        conn.execute("DROP TABLE events")
    assert not rw._table_exists("events")
    # Must not raise -- returns nothing, the read path degrades safely.
    assert rw.unresolved_events(limit=500) == []
    rw.close()


# ---------------------------------------------------------------------------
# D3: empty-string precedence must match the normalizer's TRUTHINESS, not the
# SQL IS NOT NULL presence test. The normalizer treats an empty MAC string as
# ABSENT (``if ap_mac:`` / ``if not mac`` -- "" is falsy), skips it, and falls
# through to the next source. A predicate that reads json_extract(...)=="" as
# PRESENT picks a never-resolvable branch and permanently parks a row the
# normalizer would have repaired via its next source. Every candidate MAC is
# read through NULLIF(x,'') so "" folds to absent exactly as Python sees it
# (and, matching the normalizer, whitespace-only is NOT stripped -> stays
# present on both sides).
# ---------------------------------------------------------------------------
def test_reconcile_empty_ap_resolves_via_switch_not_parked(repo: Repository) -> None:
    """D3: a client event carrying ap="" (empty string) and a real ``sw`` must be
    treated as resolvable-via-SWITCH -- the normalizer skips the empty ap and uses
    the switch. The prior predicate read the empty ap as PRESENT, marked the row
    not-resolvable, and after the attempt budget was spent only ``resolvable=1``
    could re-admit it -- which the empty-ap predicate never yielded, so the row
    stayed parked forever even once the switch appeared: 0 repairs, ref NULL.
    The fix folds "" to absent, so the switch resolves the row and it is repaired
    (not permanently parked)."""
    from netadmin.ingest.events import EventNormalizer
    from netadmin.store.repository import _EVENT_RECONCILE_MAX_ATTEMPTS

    client = repo.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id="cli:mac"), ts=500
    )
    # ap is present-but-EMPTY; sw names a real switch not yet in inventory.
    ev = repo.record_event(
        ts=1000, key="EVT_WU_Disconnected", entity_id=client, related_entity_id=None,
        native_id="emptyap", data={"key": "EVT_WU_Disconnected", "time": 1000 * 1000,
                                   "user": "cli:mac", "ap": "", "sw": "sw:mac"},
    )

    # While the switch is absent the row is still a candidate (it may yet resolve),
    # and a reconcile makes no false repair.
    assert ev in {int(r["id"]) for r in repo.unresolved_events(limit=500)}
    assert EventNormalizer(repo).reconcile_unresolved(limit=500) == 0

    # The attempt budget is spent while the switch is still absent -> parked out of
    # the oldest-first window. Only ``resolvable=1`` can re-admit it after this.
    for _ in range(_EVENT_RECONCILE_MAX_ATTEMPTS):
        repo.bump_event_reconcile_attempts([ev])
    assert ev not in {int(r["id"]) for r in repo.unresolved_events(limit=500)}

    # The switch appears. The empty ap must NOT block resolution: the row becomes
    # resolvable-via-switch, is re-admitted despite the spent budget, and repaired.
    sw = repo.upsert_entity(
        Entity(entity_type=EntityType.SWITCH, native_id="sw:mac"), ts=9000
    )
    assert ev in {int(r["id"]) for r in repo.unresolved_events(limit=500)}
    assert EventNormalizer(repo).reconcile_unresolved(limit=500) == 1
    row = repo._conn.execute(
        "SELECT related_entity_id FROM events WHERE id=?", (ev,)
    ).fetchone()
    assert row["related_entity_id"] == sw


def test_reconcile_precedence_present_ap_beats_switch(repo: Repository) -> None:
    """D3 (precedence intact): a NON-empty ap still wins over sw, exactly as the
    normalizer's ``if ap_mac: ... elif sw_mac:`` ordering. The related reference
    must resolve to the AP, never the switch, when both are present and in
    inventory. NULLIF only collapses the empty string; it must not disturb the
    single-winner precedence for a genuinely-present ap."""
    from netadmin.ingest.events import EventNormalizer

    client = repo.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id="cli:mac"), ts=500
    )
    ap = repo.upsert_entity(Entity(entity_type=EntityType.AP, native_id="ap:mac"), ts=500)
    sw = repo.upsert_entity(
        Entity(entity_type=EntityType.SWITCH, native_id="sw:mac"), ts=500
    )
    ev = repo.record_event(
        ts=1000, key="EVT_WU_Disconnected", entity_id=client, related_entity_id=None,
        native_id="presentap", data={"key": "EVT_WU_Disconnected", "time": 1000 * 1000,
                                     "user": "cli:mac", "ap": "ap:mac", "sw": "sw:mac"},
    )

    assert ev in {int(r["id"]) for r in repo.unresolved_events(limit=500)}
    assert EventNormalizer(repo).reconcile_unresolved(limit=500) == 1
    row = repo._conn.execute(
        "SELECT related_entity_id FROM events WHERE id=?", (ev,)
    ).fetchone()
    # AP wins; the switch is never consulted for the related reference.
    assert row["related_entity_id"] == ap
    assert row["related_entity_id"] != sw


def test_reconcile_all_empty_macs_is_not_a_candidate(repo: Repository) -> None:
    """D3 (all-empty parks): a row whose every candidate MAC is an EMPTY STRING
    names nothing the normalizer could resolve (all sources are falsy/skipped), so
    it must NOT be selected at all -- empty folds to absent identically to a
    missing key. Before the fix the IS NOT NULL predicate read the empty strings as
    present and admitted a row that can never resolve; the fix excludes it, and a
    reconcile repairs nothing."""
    from netadmin.ingest.events import EventNormalizer

    client = repo.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id="cli:mac"), ts=500
    )
    # Related-scoped all-empty: client resolved, related NULL, ap/sw both empty.
    rel_ev = repo.record_event(
        ts=1000, key="EVT_WU_Disconnected", entity_id=client, related_entity_id=None,
        native_id="allempty-rel", data={"key": "EVT_WU_Disconnected", "time": 1000 * 1000,
                                        "user": "cli:mac", "ap": "", "sw": ""},
    )
    # Primary-scoped all-empty: entity NULL, every primary MAC empty.
    prim_ev = repo.record_event(
        ts=1100, key="EVT_WU_Disconnected", entity_id=None, related_entity_id=None,
        native_id="allempty-prim", data={"key": "EVT_WU_Disconnected", "time": 1100 * 1000,
                                         "user": "", "ap": "", "sw": "", "gw": ""},
    )

    selected = {int(r["id"]) for r in repo.unresolved_events(limit=500)}
    assert rel_ev not in selected
    assert prim_ev not in selected
    assert EventNormalizer(repo).reconcile_unresolved(limit=500) == 0
