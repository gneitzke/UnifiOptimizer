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
from netadmin.ingest.unifi.auth import UnifiError
from netadmin.ingest.unifi.endpoints import Endpoints, ReportUnavailable
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
async def test_backfill_tracks_min_max_ts_of_written_buckets(repo: Repository):
    # The "swept window" a downstream recompute (the SLE minutes job) uses --
    # see netadmin.ingest.factory._recompute_sle_after_backfill -- must span
    # exactly the distinct report-row timestamps actually written this run.
    _ap(repo)
    oid = "aa:bb:cc:00:00:01"
    ts1, ts2 = NOW - 1800, NOW - 300
    rows = {
        (FIVEMIN, "ap"): [
            {"time": ts1 * 1000, "oid": oid, "rx_bytes": 1000.0},
            {"time": ts2 * 1000, "oid": oid, "rx_bytes": 500.0},
        ]
    }
    bf = Backfiller(FakeEndpoints(rows), repo, scopes=("ap",))

    result = await bf.run({"ap": NOW - 3600}, now=NOW)

    scope = result.scopes["ap"]
    assert scope.min_ts == ts1
    assert scope.max_ts == ts2


@pytest.mark.asyncio
async def test_backfill_min_max_ts_absent_when_nothing_written(repo: Repository):
    _ap(repo)
    ep = FakeEndpoints()  # no rows for this window
    bf = Backfiller(ep, repo, scopes=("ap",))

    # last poll 100s ago -> no gap at all, nothing requested or written.
    result = await bf.run({"ap": NOW - 100}, now=NOW)

    scope = result.scopes["ap"]
    assert scope.min_ts is None
    assert scope.max_ts is None
    assert scope.buckets == 0


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


@pytest.mark.asyncio
async def test_c4_retries_failed_chunk_after_later_chunk_advanced_samples(repo: Repository):
    """A failed [600,1200) equivalent remains a coverage hole, not MAX(ts)."""
    _ap(repo)
    failed_start = NOW - 3600

    class FailOnce(FakeEndpoints):
        def __init__(self) -> None:
            super().__init__()
            self.fail = True

        async def stat_report(self, interval, scope, *, start_ms, end_ms, attrs):
            self.calls.append(
                {"interval": interval, "scope": scope, "start_ms": start_ms, "end_ms": end_ms}
            )
            if self.fail and interval == FIVEMIN and start_ms == failed_start * 1000:
                raise RuntimeError("first chunk unavailable")
            return []

    ep = FailOnce()
    bf = Backfiller(ep, repo, scopes=("ap",), chunk_seconds={FIVEMIN: 600})
    first = await bf.run({"ap": failed_start}, now=NOW)
    assert first.errors == 1

    # A later completed chunk may have written samples up to now in production;
    # pass that old MAX-like cursor to prove the named failure still wins.
    ep.calls.clear()
    ep.fail = False
    second = await bf.run({"ap": NOW}, now=NOW)

    assert second.errors == 0
    assert (FIVEMIN, failed_start * 1000, (failed_start + 600) * 1000) in {
        (c["interval"], c["start_ms"], c["end_ms"]) for c in ep.calls
    }


@pytest.mark.asyncio
async def test_c4_open_bucket_is_retried_in_next_sweep(repo: Repository):
    """A row published after its bucket closes is not buried by the first cursor."""
    ap_id = _ap(repo)
    oid = "aa:bb:cc:00:00:01"

    class DelayedBucket(FakeEndpoints):
        def __init__(self) -> None:
            super().__init__(
                {
                    (FIVEMIN, "ap"): [
                        {"time": 600_000, "oid": oid, "rx_bytes": 6.0},
                    ]
                }
            )

        def publish_closed_bucket(self) -> None:
            self._rows[(FIVEMIN, "ap")].extend(
                [
                    {"time": 900_000, "oid": oid, "rx_bytes": 9.0},
                    {"time": 1_200_000, "oid": oid, "rx_bytes": 12.0},
                ]
            )

    ep = DelayedBucket()
    bf = Backfiller(ep, repo, scopes=("ap",), chunk_seconds={FIVEMIN: 600})

    await bf.run({"ap": 600}, now=1_000)
    assert repo.latest_ingest_coverage_end(kind="report", scope="ap") == 900

    ep.publish_closed_bucket()
    ep.calls.clear()
    cursor = repo.latest_ingest_coverage_end(kind="report", scope="ap")
    await bf.run({"ap": cursor}, now=1_300)

    assert ep.calls[0]["start_ms"] == 900_000
    series = repo.get_series(ap_id, "rx_bytes")
    assert [row["ts"] for row in repo.read_raw(series, 0, 1_500)] == [600, 900]
    assert repo.latest_ingest_coverage_end(kind="report", scope="ap") == 1_200


@pytest.mark.asyncio
async def test_c4_unsupported_report_is_unrecoverable_not_complete(repo: Repository):
    _ap(repo)

    class UnsupportedClient:
        async def get_data(self, endpoint, params):
            raise UnifiError("404 api.err.NotFound")

    endpoints = Endpoints(UnsupportedClient())  # type: ignore[arg-type]
    result = await Backfiller(endpoints, repo, scopes=("ap",)).run({"ap": 600}, now=1_000)

    coverage = repo._conn.execute(
        "SELECT status, detail FROM ingest_coverage WHERE kind='report' AND scope='ap'"
    ).fetchall()
    assert [row["status"] for row in coverage] == ["unrecoverable"]
    assert "ReportUnavailable" in coverage[0]["detail"]
    assert repo.latest_ingest_coverage_end(kind="report", scope="ap") is None
    assert result.errors == 1


@pytest.mark.asyncio
async def test_stat_report_unsupported_is_distinct_from_supported_empty():
    class UnsupportedClient:
        calls = 0

        async def get_data(self, endpoint, params):
            self.calls += 1
            raise UnifiError("404 api.err.NotFound")

    unsupported_client = UnsupportedClient()
    unsupported = Endpoints(unsupported_client)  # type: ignore[arg-type]
    with pytest.raises(ReportUnavailable):
        await unsupported.stat_report(FIVEMIN, "ap", start_ms=0, end_ms=300_000)
    with pytest.raises(ReportUnavailable):
        await unsupported.stat_report(FIVEMIN, "ap", start_ms=0, end_ms=300_000)
    assert unsupported_client.calls == 1  # sticky unsupported capability

    class EmptyClient:
        async def get_data(self, endpoint, params):
            return []

    supported = Endpoints(EmptyClient())  # type: ignore[arg-type]
    assert await supported.stat_report(FIVEMIN, "ap", start_ms=0, end_ms=300_000) == []


@pytest.mark.asyncio
async def test_finding7_clipped_failed_retry_retires_original_no_redundant_refetch(
    repo: Repository,
):
    """A retention-clipped retry must retire/split the original failed row.

    Finding #7: an actual failed fetch [4800,6000) is recorded 'failed'; then
    retention advances so only [5400,6000) is still fetchable. The successful
    clipped retry records 'complete' for [5400,6000), but the ORIGINAL
    [4800,6000) 'failed' row must NOT survive -- clipping changes the coverage
    primary key, so leaving it would regenerate a redundant refetch every run.
    The fix retires the original and splits it: [4800,5400) -> unrecoverable,
    [5400,6000) -> complete. A subsequent run must NOT refetch the satisfied
    window, while a genuinely still-missing within-retention hole still retries.
    """
    tnow = 6000  # a closed 5-minute bucket boundary (6000 % 300 == 0)
    # retention_floor = now - retention = 6000 - 600 = 5400, landing inside the
    # failed interval so the retry is clipped.
    ret = 600

    def cov_rows():
        return repo._conn.execute(
            "SELECT interval, start_ts, end_ts, status FROM ingest_coverage "
            "WHERE kind='report' AND scope='ap' ORDER BY start_ts, end_ts"
        ).fetchall()

    # An actual failed fetch, recorded first-class as a hole.
    repo.record_ingest_coverage(
        kind="report", scope="ap", interval=FIVEMIN,
        start_ts=4800, end_ts=6000, status="failed", detail="boom",
    )

    ep = FakeEndpoints()  # empty-but-successful retry
    bf = Backfiller(ep, repo, scopes=("ap",), fivemin_retention_s=ret)
    # last_ts == now -> the incremental plan opens no new windows; only the
    # failed-coverage retry drives this run.
    await bf.run({"ap": tnow}, now=tnow)

    rows = [
        (r["interval"], r["start_ts"], r["end_ts"], r["status"]) for r in cov_rows()
    ]
    # Original [4800,6000) failed row is gone; it is split into an unrecoverable
    # pre-retention slice and a complete clipped slice.
    assert rows == [
        (FIVEMIN, 4800, 5400, "unrecoverable"),
        (FIVEMIN, 5400, 6000, "complete"),
    ]
    assert repo.failed_ingest_coverage(kind="report", scope="ap") == []
    # The retry actually clipped to the still-fetchable window.
    assert [(c["start_ms"], c["end_ms"]) for c in ep.calls] == [
        (5400 * 1000, 6000 * 1000)
    ]

    # A SUBSEQUENT run must not redundantly refetch the already-satisfied window.
    ep.calls.clear()
    await bf.run({"ap": tnow}, now=tnow)
    assert ep.calls == []  # nothing left generating retry work

    # A genuinely still-missing hole WITHIN retention is still retried.
    repo.record_ingest_coverage(
        kind="report", scope="ap", interval=FIVEMIN,
        start_ts=5460, end_ts=6000, status="failed", detail="still open",
    )
    ep.calls.clear()
    await bf.run({"ap": tnow}, now=tnow)
    assert [(c["start_ms"], c["end_ms"]) for c in ep.calls] == [
        (5460 * 1000, 6000 * 1000)
    ]
    # ...and it lands as complete (no residual failed row).
    assert repo.failed_ingest_coverage(kind="report", scope="ap") == []
    statuses = {(r["start_ts"], r["end_ts"]): r["status"] for r in cov_rows()}
    assert statuses[(5460, 6000)] == "complete"


@pytest.mark.asyncio
async def test_finding3_cancel_during_clipped_fetch_does_not_lose_recoverable_hole(
    repo: Repository,
):
    """Round-12 durability: a cancel during the clipped retry must not lose the hole.

    Finding #3 regression: the retention-clipped retry retired the ORIGINAL
    failed [c_lo, c_hi) row BEFORE the clipped fetch completed. A cancellation
    between retire and fetch-completion left the recoverable slice
    [clip_start, c_hi) recorded NOWHERE -- the original 'failed' row was gone and
    its 'complete' replacement never written -- so the next run made ZERO
    requests and the hole was permanently lost.

    Scenario (from the finding): failed [4800,5700), complete [5700,6000),
    retention floor 5400. A CancelledError raised during the clipped [5400,5700)
    fetch tears the run down mid-flight (CancelledError is a BaseException, so it
    is NOT swallowed by the per-chunk Exception firewall). The split-first fix
    must leave [5400,5700) durably 'failed', so the NEXT run STILL retries it.
    """
    tnow = 6000  # closed 5-minute bucket boundary (6000 % 300 == 0)
    ret = 600  # retention_floor = 6000 - 600 = 5400, landing inside [4800,5700)

    def cov_rows():
        return repo._conn.execute(
            "SELECT interval, start_ts, end_ts, status FROM ingest_coverage "
            "WHERE kind='report' AND scope='ap' ORDER BY start_ts, end_ts"
        ).fetchall()

    # The pre-existing ledger: an actual failed fetch plus an adjacent complete
    # slice, exactly as the finding derives it from a real repository cursor.
    repo.record_ingest_coverage(
        kind="report", scope="ap", interval=FIVEMIN,
        start_ts=4800, end_ts=5700, status="failed", detail="boom",
    )
    repo.record_ingest_coverage(
        kind="report", scope="ap", interval=FIVEMIN,
        start_ts=5700, end_ts=6000, status="complete",
    )

    class CancelDuringFetch(FakeEndpoints):
        """Raises CancelledError on the first report request (the clipped retry)."""

        def __init__(self):
            super().__init__()
            self.cancelled = False

        async def stat_report(self, interval, scope, *, start_ms, end_ms, attrs):
            self.calls.append({"start_ms": start_ms, "end_ms": end_ms})
            if not self.cancelled:
                self.cancelled = True
                raise __import__("asyncio").CancelledError()
            return []

    ep = CancelDuringFetch()
    bf = Backfiller(ep, repo, scopes=("ap",), fivemin_retention_s=ret)

    # The run is torn down mid-flight by the cancellation during the clipped fetch.
    with pytest.raises(__import__("asyncio").CancelledError):
        await bf.run({"ap": tnow}, now=tnow)

    # The retry did clip to the still-fetchable window before being cancelled.
    assert ep.calls == [{"start_ms": 5400 * 1000, "end_ms": 5700 * 1000}]

    # DURABILITY: despite the cancel BEFORE the fetch completed, the recoverable
    # slice [5400,5700) survives as a 'failed' hole (split-first, then retire);
    # the pre-clip slice is unrecoverable and the untouched complete slice remains.
    rows = [(r["interval"], r["start_ts"], r["end_ts"], r["status"]) for r in cov_rows()]
    assert rows == [
        (FIVEMIN, 4800, 5400, "unrecoverable"),
        (FIVEMIN, 5400, 5700, "failed"),
        (FIVEMIN, 5700, 6000, "complete"),
    ]
    # The regression's tell was a lost hole -> zero requests next run. The hole is
    # NOT lost: the next run STILL retries [5400,5700) (and now succeeds).
    ep.calls.clear()
    await bf.run({"ap": tnow}, now=tnow)
    assert ep.calls == [{"start_ms": 5400 * 1000, "end_ms": 5700 * 1000}]
    assert repo.failed_ingest_coverage(kind="report", scope="ap") == []
    statuses = {(r["start_ts"], r["end_ts"]): r["status"] for r in cov_rows()}
    assert statuses[(5400, 5700)] == "complete"


@pytest.mark.asyncio
async def test_finding3_success_path_preserves_round11_end_state(repo: Repository):
    """The non-cancelled success path still ends unrecoverable+complete, no refetch.

    Same scenario as the cancel test (failed [4800,5700), complete [5700,6000),
    floor 5400) but with a working endpoint: the split-first ordering must not
    regress round-11. The clipped retry lands 'complete', leaving exactly the
    unrecoverable pre-clip slice plus complete slices, no residual 'failed' row,
    and a subsequent run makes NO redundant request.
    """
    tnow = 6000
    ret = 600

    def cov_rows():
        return repo._conn.execute(
            "SELECT interval, start_ts, end_ts, status FROM ingest_coverage "
            "WHERE kind='report' AND scope='ap' ORDER BY start_ts, end_ts"
        ).fetchall()

    repo.record_ingest_coverage(
        kind="report", scope="ap", interval=FIVEMIN,
        start_ts=4800, end_ts=5700, status="failed", detail="boom",
    )
    repo.record_ingest_coverage(
        kind="report", scope="ap", interval=FIVEMIN,
        start_ts=5700, end_ts=6000, status="complete",
    )

    ep = FakeEndpoints()  # empty-but-successful retry
    bf = Backfiller(ep, repo, scopes=("ap",), fivemin_retention_s=ret)
    await bf.run({"ap": tnow}, now=tnow)

    rows = [(r["interval"], r["start_ts"], r["end_ts"], r["status"]) for r in cov_rows()]
    assert rows == [
        (FIVEMIN, 4800, 5400, "unrecoverable"),
        (FIVEMIN, 5400, 5700, "complete"),
        (FIVEMIN, 5700, 6000, "complete"),
    ]
    assert repo.failed_ingest_coverage(kind="report", scope="ap") == []
    assert [(c["start_ms"], c["end_ms"]) for c in ep.calls] == [(5400 * 1000, 5700 * 1000)]

    # No redundant refetch on a subsequent run.
    ep.calls.clear()
    await bf.run({"ap": tnow}, now=tnow)
    assert ep.calls == []


class RawRowsEndpoints:
    """Returns pre-built ``ReportRow`` objects verbatim (no range filtering).

    Unlike :class:`FakeEndpoints` it does not require a ``time`` key on every
    row, so it can replay a missing-timestamp row exactly as a controller might.
    """

    def __init__(self, rows_by_key: dict[tuple[str, str], list] | None = None) -> None:
        self._rows = rows_by_key or {}
        self.calls: list[dict] = []

    async def stat_report(self, interval, scope, *, start_ms, end_ms, attrs):
        self.calls.append(
            {"interval": interval, "scope": scope, "start_ms": start_ms, "end_ms": end_ms}
        )
        return list(self._rows.get((interval, scope), []))


def _report_coverage(repo: Repository, scope: str = "ap") -> list[tuple]:
    return [
        (r["interval"], r["start_ts"], r["end_ts"], r["status"])
        for r in repo._conn.execute(
            "SELECT interval, start_ts, end_ts, status FROM ingest_coverage "
            "WHERE kind='report' AND scope=? ORDER BY start_ts, end_ts",
            (scope,),
        ).fetchall()
    ]


@pytest.mark.asyncio
async def test_w17b_unknown_device_row_records_partial_not_complete(repo: Repository):
    """#w17b: a chunk that DROPPED a row naming an unknown device is 'partial'.

    The known AP exists, but the report row names a different, undiscovered oid.
    Backfill drops the row (skipped_unresolved) and inserts ZERO samples -- yet
    the window must NOT be credited 'complete', or the production cursor
    (latest_ingest_coverage_end) SKIPS re-collecting this lost history once the
    device is discovered. Mirror the event catch-up 'partial' rule.
    """
    _ap(repo, native_id="aa:bb:cc:00:00:01")
    ts = NOW - 1800  # inside the 1 h gap -> a single 5-minute chunk
    rows = {
        (FIVEMIN, "ap"): [
            {"time": ts * 1000, "oid": "ff:ff:ff:ff:ff:ff", "rx_bytes": 42.0},
        ]
    }
    bf = Backfiller(FakeEndpoints(rows), repo, scopes=("ap",))
    result = await bf.run({"ap": NOW - 3600}, now=NOW)

    assert result.rows_inserted == 0
    assert result.scopes["ap"].skipped_unresolved == 1
    # The window is a retryable 'partial' hole, NOT complete (the bug), NOT a
    # transport 'failed'. One chunk was requested.
    cov = _report_coverage(repo)
    assert len(cov) == 1
    assert cov[0][3] == "partial"
    # CRITICAL: the completion cursor is NOT advanced past the dropped window, so
    # the next sweep re-attempts it rather than silently losing the history.
    assert repo.latest_ingest_coverage_end(kind="report", scope="ap") is None


@pytest.mark.asyncio
async def test_w17b_missing_timestamp_row_records_partial_not_complete(repo: Repository):
    """#w17b: a chunk that DROPPED a row with no bucket timestamp is 'partial'."""
    ap_id = _ap(repo, native_id="aa:bb:cc:00:00:01")
    oid = "aa:bb:cc:00:00:01"
    # A resolvable device, but the row carries no ``time`` -> unstorable history.
    rows = {
        (FIVEMIN, "ap"): [ReportRow.model_validate({"oid": oid, "rx_bytes": 42.0})],
    }
    ep = RawRowsEndpoints(rows)
    bf = Backfiller(ep, repo, scopes=("ap",))
    result = await bf.run({"ap": NOW - 3600}, now=NOW)

    assert result.rows_inserted == 0
    # No sample landed for the resolvable device (its row had no timestamp)...
    assert repo.read_raw(repo.get_series(ap_id, "rx_bytes"), 0, NOW + 1) == []
    # ...and every chunk that saw the dropped row is 'partial', never 'complete'.
    cov = _report_coverage(repo)
    assert cov, "expected at least one recorded chunk"
    assert all(status == "partial" for _i, _s, _e, status in cov)
    assert repo.latest_ingest_coverage_end(kind="report", scope="ap") is None


@pytest.mark.asyncio
async def test_w17b_empty_successful_chunk_still_records_complete(repo: Repository):
    """#w17b: a genuinely EMPTY-but-successful chunk drops nothing -> 'complete'.

    The dropped-row rule must not over-fire: a quiet window where the source
    returned no rows at all is a real, successful, empty read and still advances
    the cursor.
    """
    _ap(repo)
    ep = FakeEndpoints()  # no rows for this window at all
    bf = Backfiller(ep, repo, scopes=("ap",))
    result = await bf.run({"ap": NOW - 3600}, now=NOW)

    assert result.errors == 0
    cov = _report_coverage(repo)
    assert len(cov) == 1
    assert cov[0][3] == "complete"
    assert repo.latest_ingest_coverage_end(kind="report", scope="ap") is not None


@pytest.mark.asyncio
async def test_w17b_rerun_completes_once_device_is_known(repo: Repository):
    """#w17b: the 'partial' hole self-heals -- a re-run resolves and completes.

    First run drops the unknown-device row -> 'partial', cursor unmoved. After
    the sync job discovers the device, a re-run over the same (not-advanced)
    window resolves the row, stores its samples, and records 'complete'.
    """
    ts = NOW - 1800
    oid = "aa:bb:cc:00:00:02"  # not yet in inventory on the first run
    rows = {(FIVEMIN, "ap"): [{"time": ts * 1000, "oid": oid, "rx_bytes": 77.0}]}
    ep = FakeEndpoints(rows)
    bf = Backfiller(ep, repo, scopes=("ap",))

    first = await bf.run({"ap": NOW - 3600}, now=NOW)
    assert first.rows_inserted == 0
    assert _report_coverage(repo)[0][3] == "partial"
    cursor_after_partial = repo.latest_ingest_coverage_end(kind="report", scope="ap")
    assert cursor_after_partial is None  # not advanced past the hole

    # The device is discovered; the production loop re-attempts the un-advanced
    # window (cursor is still None -> the gap is re-planned and re-fetched).
    ap_id = _ap(repo, native_id=oid)
    second = await bf.run({"ap": NOW - 3600}, now=NOW)

    assert second.rows_inserted == 1  # one bucket x one metric now resolved
    assert repo.read_raw(repo.get_series(ap_id, "rx_bytes"), 0, NOW + 1)[0]["value"] == 77.0
    # The window is now genuinely complete and the cursor advances.
    statuses = {status for _i, _s, _e, status in _report_coverage(repo)}
    assert "complete" in statuses and "partial" not in statuses
    assert repo.latest_ingest_coverage_end(kind="report", scope="ap") is not None


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
