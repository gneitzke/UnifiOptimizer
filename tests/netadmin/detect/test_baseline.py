"""Tests for netadmin.detect.baseline (EWMA + rolling-quantile baselines)."""

from __future__ import annotations

import pytest

from netadmin.detect.baseline import DEFAULT_ALPHA, DIURNAL_METRICS, Band, Baselines, hour_label
from netadmin.store.repository import Repository

from .conftest import record_gauge

HOUR = 3_600
DAY = 86_400


# --------------------------------------------------------------------------- #
# hour_label + construction
# --------------------------------------------------------------------------- #


def test_hour_label_maps_epoch_to_hour_of_day() -> None:
    assert hour_label(0) == "h00"
    assert hour_label(14 * HOUR) == "h14"
    assert hour_label(23 * HOUR + 59 * 60) == "h23"
    # wraps by day, not by absolute time
    assert hour_label(3 * DAY + 9 * HOUR) == "h09"


def test_for_repository_uses_defaults(repo: Repository) -> None:
    bl = Baselines.for_repository(repo)
    assert bl.alpha == DEFAULT_ALPHA
    assert bl.min_samples == 30


def test_rejects_out_of_range_alpha(repo: Repository) -> None:
    with pytest.raises(ValueError):
        Baselines(repo, alpha=0.0)
    with pytest.raises(ValueError):
        Baselines(repo, alpha=1.0)


# --------------------------------------------------------------------------- #
# EWMA math vs hand-computed
# --------------------------------------------------------------------------- #


def test_ewma_mean_and_variance_match_hand_computed(repo: Repository, ap_entity_id: int) -> None:
    # rssi is a non-diurnal gauge -> single 'all' bucket, values stored verbatim.
    sid = record_gauge(repo, ap_entity_id, "rssi", [(100, 10.0), (200, 20.0), (300, 30.0)])
    bl = Baselines(repo, alpha=0.5, min_samples=1)

    assert bl.update_from_recent(now_ts=400) == 1
    band = bl.band(sid)
    assert band is not None
    # Hand fold with alpha=0.5 over [10, 20, 30]:
    #   10 -> mean=10, var=0
    #   20 -> diff=10, incr=5,  mean=15,   var=0.5*(0 + 10*5)   = 25
    #   30 -> diff=15, incr=7.5, mean=22.5, var=0.5*(25 + 15*7.5)= 68.75
    assert band.mean == pytest.approx(22.5)
    assert band.var == pytest.approx(68.75)
    assert band.std == pytest.approx(68.75**0.5)
    assert band.n == 3


def test_ewma_first_sample_seeds_zero_variance(repo: Repository, ap_entity_id: int) -> None:
    sid = record_gauge(repo, ap_entity_id, "rssi", [(100, -60.0)])
    bl = Baselines(repo, alpha=0.3, min_samples=1)
    bl.update_from_recent(now_ts=200)
    band = bl.band(sid)
    assert band is not None
    assert band.mean == pytest.approx(-60.0)
    assert band.var == pytest.approx(0.0)
    assert band.n == 1


# --------------------------------------------------------------------------- #
# Quantiles
# --------------------------------------------------------------------------- #


def test_quantiles_on_skewed_data(repo: Repository, ap_entity_id: int) -> None:
    # Nine 1.0s and one outlier 100.0 -> P50 pinned at 1, P95 pulled toward the tail.
    points = [(i * 60, 1.0) for i in range(9)] + [(9 * 60, 100.0)]
    sid = record_gauge(repo, ap_entity_id, "rssi", points)
    bl = Baselines(repo, alpha=0.2, min_samples=1)
    bl.update_from_recent(now_ts=10 * 60)

    band = bl.band(sid)
    assert band is not None
    assert band.p05 == pytest.approx(1.0)
    assert band.p50 == pytest.approx(1.0)
    # linear/type-7: idx = 0.95*9 = 8.55 -> 1 + (100-1)*0.55 = 55.45
    assert band.p95 == pytest.approx(55.45)


def test_quantiles_single_sample_are_that_value(repo: Repository, ap_entity_id: int) -> None:
    sid = record_gauge(repo, ap_entity_id, "rssi", [(100, 42.0)])
    bl = Baselines(repo, alpha=0.2, min_samples=1)
    bl.update_from_recent(now_ts=200)
    band = bl.band(sid)
    assert band is not None
    assert band.p05 == band.p50 == band.p95 == pytest.approx(42.0)


# --------------------------------------------------------------------------- #
# Hour-of-day bucket routing
# --------------------------------------------------------------------------- #


def test_diurnal_metric_gets_hour_buckets(repo: Repository, ap_entity_id: int) -> None:
    assert "cu_total" in DIURNAL_METRICS
    # cu_total is diurnal: samples at 09:xx and 14:xx go to h09 and h14 (and 'all').
    points = [
        (9 * HOUR + 60, 30.0),
        (9 * HOUR + 120, 32.0),
        (14 * HOUR + 60, 70.0),
        (14 * HOUR + 120, 72.0),
    ]
    sid = record_gauge(repo, ap_entity_id, "cu_total", points)
    bl = Baselines(repo, alpha=0.5, min_samples=1)
    bl.update_from_recent(now_ts=15 * HOUR)

    buckets = {str(r["bucket"]) for r in repo.get_baselines(sid)}
    assert "all" in buckets
    assert "h09" in buckets
    assert "h14" in buckets

    # Hour bands separate day from evening; 'all' spans both.
    h09 = bl.band(sid, bucket="h09")
    h14 = bl.band(sid, bucket="h14")
    assert h09 is not None and h14 is not None
    assert h09.mean < h14.mean
    assert h09.n == 2 and h14.n == 2


def test_non_diurnal_metric_only_all_bucket(repo: Repository, ap_entity_id: int) -> None:
    assert "rssi" not in DIURNAL_METRICS
    points = [(9 * HOUR + 60, -60.0), (14 * HOUR + 60, -62.0)]
    sid = record_gauge(repo, ap_entity_id, "rssi", points)
    bl = Baselines(repo, alpha=0.5, min_samples=1)
    bl.update_from_recent(now_ts=15 * HOUR)

    buckets = {str(r["bucket"]) for r in repo.get_baselines(sid) if str(r["bucket"]) != "_meta"}
    assert buckets == {"all"}
    assert bl.band(sid, bucket="h09") is None  # no hour buckets for non-diurnal


def test_band_none_bucket_defaults_to_all(repo: Repository, ap_entity_id: int) -> None:
    sid = record_gauge(repo, ap_entity_id, "rssi", [(100, 1.0), (200, 2.0)])
    bl = Baselines(repo, alpha=0.5, min_samples=1)
    bl.update_from_recent(now_ts=300)
    assert bl.band(sid) == bl.band(sid, bucket="all")


# --------------------------------------------------------------------------- #
# Cold start
# --------------------------------------------------------------------------- #


def test_band_none_when_no_baseline(repo: Repository, ap_entity_id: int) -> None:
    sid = record_gauge(repo, ap_entity_id, "rssi", [(100, 1.0)])
    bl = Baselines(repo, min_samples=1)
    # No update run yet -> nothing persisted.
    assert bl.band(sid) is None


def test_band_none_below_min_samples(repo: Repository, ap_entity_id: int) -> None:
    # Default min_samples is 30; only 5 samples -> None despite baseline rows.
    points = [(i * 60, float(i)) for i in range(5)]
    sid = record_gauge(repo, ap_entity_id, "rssi", points)
    bl = Baselines(repo)  # min_samples=30
    bl.update_from_recent(now_ts=10 * 60)
    assert bl.band(sid) is None

    # Cross the floor: 30 samples -> a band appears.
    more = [(i * 60, float(i)) for i in range(5, 35)]
    record_gauge(repo, ap_entity_id, "rssi", more)
    bl.update_from_recent(now_ts=40 * 60)
    assert bl.band(sid) is not None


# --------------------------------------------------------------------------- #
# Watermark incrementality
# --------------------------------------------------------------------------- #


def test_watermark_only_processes_new_samples(repo: Repository, ap_entity_id: int) -> None:
    sid = record_gauge(repo, ap_entity_id, "rssi", [(100, 10.0), (200, 20.0)])
    bl = Baselines(repo, alpha=0.5, min_samples=1)
    assert bl.update_from_recent(now_ts=250) == 1
    first = bl.band(sid)
    assert first is not None and first.n == 2

    # A second run with no new samples touches nothing.
    assert bl.update_from_recent(now_ts=300) == 0
    unchanged = bl.band(sid)
    assert unchanged is not None
    assert unchanged.mean == pytest.approx(first.mean)
    assert unchanged.n == 2

    # Add one more sample; only it is folded (mean continues from the prior state).
    record_gauge(repo, ap_entity_id, "rssi", [(400, 30.0)])
    assert bl.update_from_recent(now_ts=500) == 1
    third = bl.band(sid)
    assert third is not None
    assert third.n == 3
    # Fold 30 onto mean=15: diff=15, mean=15+7.5=22.5 (same as folding all three).
    assert third.mean == pytest.approx(22.5)


def test_samples_after_now_ts_deferred(repo: Repository, ap_entity_id: int) -> None:
    # A sample stamped in the future is not folded until now_ts reaches it.
    sid = record_gauge(repo, ap_entity_id, "rssi", [(100, 10.0), (500, 20.0)])
    bl = Baselines(repo, alpha=0.5, min_samples=1)
    bl.update_from_recent(now_ts=200)  # only ts=100 is <= now
    band = bl.band(sid)
    assert band is not None and band.n == 1

    bl.update_from_recent(now_ts=600)  # now ts=500 is eligible
    band = bl.band(sid)
    assert band is not None and band.n == 2


# --------------------------------------------------------------------------- #
# Backfill awareness
# --------------------------------------------------------------------------- #


def test_backfill_only_hours_skipped_from_ewma(repo: Repository, ap_entity_id: int) -> None:
    # Live poll only in hour 14; samples exist in hour 14 (live) and hour 15 (gap
    # reconstructed from backfill -> no live poll_run that hour).
    repo.record_poll_run(job="device", ok=True, ts=14 * HOUR + 30, source="live")
    points = [
        (14 * HOUR + 60, 50.0),
        (14 * HOUR + 120, 52.0),
        (15 * HOUR + 60, 999.0),  # backfill-only hour
        (15 * HOUR + 120, 999.0),
    ]
    sid = record_gauge(repo, ap_entity_id, "rssi", points)
    bl = Baselines(repo, alpha=0.5, min_samples=1, backfill_aware=True)
    bl.update_from_recent(now_ts=16 * HOUR)

    band = bl.band(sid)
    assert band is not None
    # Only the two live hour-14 samples fold into the EWMA.
    assert band.n == 2
    assert band.mean == pytest.approx(51.0)  # 50 then 52 with alpha=0.5

    # Watermark advanced past the skipped hour-15 samples: nothing reprocesses.
    assert bl.update_from_recent(now_ts=17 * HOUR) == 0

    # Quantiles are computed over stored raw (including backfilled rows), so the
    # 999s are visible there -- the EWMA centre is the strictly-live statistic.
    assert band.p95 == pytest.approx(999.0)


def test_backfill_gating_fails_open_without_poll_runs(repo: Repository, ap_entity_id: int) -> None:
    # No poll accounting at all -> every sample folds (a plain unit-test scenario).
    points = [(14 * HOUR + 60, 10.0), (15 * HOUR + 60, 20.0)]
    sid = record_gauge(repo, ap_entity_id, "rssi", points)
    bl = Baselines(repo, alpha=0.5, min_samples=1, backfill_aware=True)
    bl.update_from_recent(now_ts=16 * HOUR)
    band = bl.band(sid)
    assert band is not None
    assert band.n == 2


def test_backfill_aware_false_folds_everything(repo: Repository, ap_entity_id: int) -> None:
    repo.record_poll_run(job="device", ok=True, ts=14 * HOUR + 30, source="live")
    points = [(14 * HOUR + 60, 10.0), (15 * HOUR + 60, 20.0)]
    sid = record_gauge(repo, ap_entity_id, "rssi", points)
    bl = Baselines(repo, alpha=0.5, min_samples=1, backfill_aware=False)
    bl.update_from_recent(now_ts=16 * HOUR)
    band = bl.band(sid)
    assert band is not None
    assert band.n == 2  # hour-15 sample not gated out


# --------------------------------------------------------------------------- #
# Multi-series accounting
# --------------------------------------------------------------------------- #


def test_update_counts_series_touched(repo: Repository, ap_entity_id: int) -> None:
    record_gauge(repo, ap_entity_id, "rssi", [(100, 1.0)])
    record_gauge(repo, ap_entity_id, "noise", [(100, 2.0)])
    record_gauge(repo, ap_entity_id, "cu_total", [(100, 3.0)])
    bl = Baselines(repo, alpha=0.5, min_samples=1)
    assert bl.update_from_recent(now_ts=200) == 3
    assert bl.update_from_recent(now_ts=300) == 0


def test_gap_tolerant_across_missed_hours(repo: Repository, ap_entity_id: int) -> None:
    # A long gap between two live windows must not corrupt state; the second
    # window simply continues the EWMA from where the first left off.
    repo.record_poll_run(job="device", ok=True, ts=1 * HOUR + 30, source="live")
    repo.record_poll_run(job="device", ok=True, ts=50 * HOUR + 30, source="live")
    sid = record_gauge(
        repo,
        ap_entity_id,
        "rssi",
        [(1 * HOUR + 60, 10.0), (50 * HOUR + 60, 30.0)],
    )
    bl = Baselines(repo, alpha=0.5, min_samples=1, backfill_aware=True)
    bl.update_from_recent(now_ts=51 * HOUR)
    band = bl.band(sid)
    assert band is not None
    assert band.n == 2
    # 10 then 30 with alpha=0.5 -> mean 20.
    assert band.mean == pytest.approx(20.0)
