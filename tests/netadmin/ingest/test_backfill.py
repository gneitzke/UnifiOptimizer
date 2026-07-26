"""Backfill: gap math, chunking, verbatim insertion, and source accounting."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest

from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.ingest.backfill import (
    DEFAULT_CHUNK_SECONDS,
    DEFAULT_FIVEMIN_RETENTION_S,
    DEFAULT_HOURLY_RETENTION_S,
    FIVEMIN,
    HOURLY,
    INTERVAL_SECONDS,
    Backfiller,
    chunk_window,
    job_name,
    plan_report_windows,
)
from netadmin.ingest.unifi.models import ReportRow
from netadmin.store.metrics import MetricKind, metric_kind
from netadmin.store.repository import Repository

NOW = 2_000_000_000
FIVEMIN_FLOOR = NOW - DEFAULT_FIVEMIN_RETENTION_S
HOURLY_FLOOR = NOW - DEFAULT_HOURLY_RETENTION_S
# The tier seam is snapped down to an hour so no hourly bucket straddles it. NOW
# is deliberately NOT hour-aligned (NOW % 3600 == 2000), so BOUNDARY < FIVEMIN_FLOOR
# and these cases actually exercise the snap.
BOUNDARY = FIVEMIN_FLOOR - (FIVEMIN_FLOOR % INTERVAL_SECONDS[HOURLY])
assert FIVEMIN_FLOOR % INTERVAL_SECONDS[HOURLY] != 0  # guard: the fix is under test


# --------------------------------------------------------------------------- #
# Fake endpoints
# --------------------------------------------------------------------------- #
class FakeEndpoints:
    """Records report requests and replays canned rows per (interval, scope)."""

    def __init__(self, rows_by_key: Optional[dict[tuple[str, str], list[dict]]] = None) -> None:
        self._rows = rows_by_key or {}
        self.calls: list[dict] = []

    async def stat_report(self, interval, scope, *, start_ms, end_ms, attrs):
        self.calls.append(
            {
                "interval": interval,
                "scope": scope,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "attrs": list(attrs),
            }
        )
        rows = self._rows.get((interval, scope), [])
        out = []
        for r in rows:
            if start_ms <= int(r["time"]) < end_ms:
                out.append(ReportRow.model_validate(r))
        return out


@pytest.fixture
def repo(tmp_db_path: Path) -> Repository:
    r = Repository.open(tmp_db_path)
    yield r
    r.close()


def _ap(repo: Repository, native_id: str = "aa:bb:cc:00:00:01") -> int:
    return repo.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id=native_id, name="ap-1"), ts=NOW - 10
    )


# --------------------------------------------------------------------------- #
# Gap math
# --------------------------------------------------------------------------- #
def test_no_gap_when_last_ts_recent():
    # last poll 100 s ago: below one 5-min interval -> nothing to backfill.
    plan = plan_report_windows(NOW - 100, NOW)
    assert plan[FIVEMIN] is None
    assert plan[HOURLY] is None


def test_partial_gap_uses_only_five_minute_tier():
    # 1 h gap, well inside 5-min retention -> 5-min fills it, hourly idle.
    plan = plan_report_windows(NOW - 3600, NOW)
    assert plan[FIVEMIN] == (NOW - 3600, NOW)
    assert plan[HOURLY] is None


def test_fresh_install_pulls_full_retention_both_tiers():
    plan = plan_report_windows(None, NOW)
    assert plan[FIVEMIN] == (BOUNDARY, NOW)
    assert plan[HOURLY] == (HOURLY_FLOOR, BOUNDARY)


def test_tiers_are_disjoint():
    # For any old gap the 5-min lower bound meets the hourly upper bound exactly:
    # no wall-clock second is covered by both tiers (no double counting).
    plan = plan_report_windows(None, NOW)
    assert plan[FIVEMIN][0] == plan[HOURLY][1] == BOUNDARY


def test_tier_boundary_is_hour_aligned_no_straddle():
    # The seam must sit on an hour boundary so no full-hour bucket straddles it
    # (the boundary-hour double-count bug). BOUNDARY < FIVEMIN_FLOOR here.
    plan = plan_report_windows(None, NOW)
    boundary = plan[HOURLY][1]
    assert boundary % INTERVAL_SECONDS[HOURLY] == 0
    assert boundary == plan[FIVEMIN][0]
    assert boundary < FIVEMIN_FLOOR  # snapped strictly down for this NOW


def test_beyond_retention_clamps_to_controller_floor():
    # Last data 30 days ago -> older than hourly retention. Hourly can only reach
    # back to its retention floor; the pre-floor gap is unrecoverable, not faked.
    last = NOW - 30 * 86400
    plan = plan_report_windows(last, NOW)
    assert plan[HOURLY] == (HOURLY_FLOOR, BOUNDARY)
    assert plan[HOURLY][0] > last  # clamped forward, history not fabricated
    assert plan[FIVEMIN] == (BOUNDARY, NOW)


def test_last_ts_inside_hourly_tier_clamps_hourly_start():
    # Gap of 3 days: 5-min fills last day, hourly fills day-1 back to day-3.
    last = NOW - 3 * 86400
    plan = plan_report_windows(last, NOW)
    assert plan[FIVEMIN] == (BOUNDARY, NOW)
    assert plan[HOURLY] == (last, BOUNDARY)


# --------------------------------------------------------------------------- #
# Chunking
# --------------------------------------------------------------------------- #
def test_chunk_window_splits_evenly():
    chunks = chunk_window(0, 300, 100)
    assert chunks == [(0, 100), (100, 200), (200, 300)]


def test_chunk_window_trailing_partial():
    chunks = chunk_window(0, 250, 100)
    assert chunks == [(0, 100), (100, 200), (200, 250)]


def test_chunk_window_single_when_within_chunk():
    assert chunk_window(0, 50, 100) == [(0, 50)]


def test_chunk_window_rejects_nonpositive_chunk():
    with pytest.raises(ValueError):
        chunk_window(0, 100, 0)


# --------------------------------------------------------------------------- #
# Backfiller end to end
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_backfill_inserts_verbatim_with_source_flag(repo: Repository):
    ap_id = _ap(repo)
    oid = "aa:bb:cc:00:00:01"
    ts1, ts2 = NOW - 1800, NOW - 1500  # same hour bucket, inside a 1 h gap
    rows = {
        (FIVEMIN, "ap"): [
            {"time": ts1 * 1000, "oid": oid, "rx_bytes": 1000.0, "num_sta": 3},
            # Decreasing rx_bytes proves values are stored verbatim, NOT diffed:
            # a counter-diff would drop this row as a reset instead of storing it.
            {"time": ts2 * 1000, "oid": oid, "rx_bytes": 500.0, "num_sta": 2},
        ]
    }
    ep = FakeEndpoints(rows)
    bf = Backfiller(ep, repo, scopes=("ap",))

    result = await bf.run({"ap": NOW - 3600}, now=NOW)

    # Only the 5-minute tier fired (1 h gap), one chunk -> one request.
    assert len(ep.calls) == 1
    assert ep.calls[0]["interval"] == FIVEMIN
    assert result.rows_inserted == 4  # 2 buckets x 2 metrics

    series = repo.get_series(ap_id, "rx_bytes")
    raw = repo.read_raw(series, NOW - 3600, NOW)
    assert [r["value"] for r in raw] == [1000.0, 500.0]  # both kept, verbatim

    # Counter series rolls up as a sum, so the hour holds the true total.
    hour = (ts1 // 3600) * 3600
    rollup = repo.read_rollup(series, "hourly", hour, hour + 3600)
    assert rollup[0]["n"] == 2
    assert rollup[0]["value"] == 1500.0

    # Backfilled polls are marked, distinct from live collection.
    runs = repo.read_poll_runs(job_name(FIVEMIN, "ap"), NOW - 3600, NOW)
    assert len(runs) == 2
    assert all(r["source"] == "backfill" and r["ok"] == 1 for r in runs)


@pytest.mark.asyncio
async def test_backfill_chunks_wide_windows(repo: Repository):
    _ap(repo)
    ep = FakeEndpoints()  # no rows: we only count requests
    bf = Backfiller(ep, repo, scopes=("ap",))

    # Fresh install -> full retention on both tiers.
    result = await bf.run({"ap": None}, now=NOW)

    five = [c for c in ep.calls if c["interval"] == FIVEMIN]
    hourly = [c for c in ep.calls if c["interval"] == HOURLY]
    # Wide windows are chunked at their per-tier chunk width; derive the expected
    # counts from the actual (hour-snapped) plan rather than hard-coding, so the
    # test tracks the seam instead of a magic number.
    plan = plan_report_windows(None, NOW)
    exp_five = len(chunk_window(*plan[FIVEMIN], DEFAULT_CHUNK_SECONDS[FIVEMIN]))
    exp_hourly = len(chunk_window(*plan[HOURLY], DEFAULT_CHUNK_SECONDS[HOURLY]))
    assert len(five) == exp_five
    assert len(hourly) == exp_hourly
    assert result.windows == exp_five + exp_hourly
    # Chunks tile the window with no gaps or overlaps.
    five_sorted = sorted((c["start_ms"], c["end_ms"]) for c in five)
    for (_, prev_end), (next_start, _) in zip(five_sorted, five_sorted[1:]):
        assert prev_end == next_start


@pytest.mark.asyncio
async def test_backfill_skips_unresolved_entities(repo: Repository):
    # AP exists but the report row is for a different, undiscovered oid.
    _ap(repo, native_id="aa:bb:cc:00:00:01")
    ts = NOW - 1800
    rows = {
        (FIVEMIN, "ap"): [
            {"time": ts * 1000, "oid": "ff:ff:ff:ff:ff:ff", "rx_bytes": 42.0},
        ]
    }
    bf = Backfiller(FakeEndpoints(rows), repo, scopes=("ap",))
    result = await bf.run({"ap": NOW - 3600}, now=NOW)

    assert result.rows_inserted == 0
    assert result.scopes["ap"].skipped_unresolved == 1


@pytest.mark.asyncio
async def test_backfill_records_failure_poll_run_on_error(repo: Repository):
    _ap(repo)

    class Boom(FakeEndpoints):
        async def stat_report(self, *a, **k):
            raise RuntimeError("mongo timeout")

    bf = Backfiller(Boom(), repo, scopes=("ap",))
    result = await bf.run({"ap": NOW - 3600}, now=NOW)

    assert result.errors == 1
    runs = repo.read_poll_runs(job_name(FIVEMIN, "ap"), NOW - 3600, NOW + 1)
    assert len(runs) == 1
    assert runs[0]["ok"] == 0
    assert runs[0]["source"] == "backfill"


def test_user_signal_maps_to_collector_rssi_metric():
    # Report "signal" (dBm) must land on the collector's canonical "rssi" series
    # (mapping.py stores Client.signal as "rssi"), never a divergent "signal".
    from netadmin.ingest.backfill import REPORT_METRICS

    user_metrics = {attr: metric for attr, metric, _ in REPORT_METRICS["user"]}
    assert user_metrics["signal"] == "rssi"


def test_wan_counter_metrics_registered():
    # Gateway/site report metrics the live collector may not know are registered
    # as counters so their rollups aggregate as a sum, not an average.
    for m in ("wan_rx_bytes", "wan_tx_bytes", "lan_rx_bytes", "lan_tx_bytes"):
        assert metric_kind(m) is MetricKind.COUNTER
