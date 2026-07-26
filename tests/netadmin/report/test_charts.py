"""Unit tests for the pure report-chart computations.

These are the invariants the "no false data" / dataviz gates hinge on: the RSSI
histogram bins sum to the value count and colour the weak tail exactly at the
coverage floor, neighbour noise aggregates to per-channel counts (never per-BSSID),
and the health trend omits empty buckets instead of interpolating a line.
"""

from __future__ import annotations

from netadmin.report import charts


# --------------------------------------------------------------------------- #
# RSSI histogram
# --------------------------------------------------------------------------- #
def test_histogram_bins_sum_to_value_count() -> None:
    values = [-45.0, -55.0, -62.0, -68.0, -71.0, -73.0, -78.0, -84.0, -90.0]
    hist = charts.rssi_histogram(values, weak_threshold_dbm=-72.0)
    assert hist["total"] == len(values)
    assert sum(b["count"] for b in hist["bins"]) == len(values)


def test_histogram_empty_is_honest_zero() -> None:
    hist = charts.rssi_histogram([], weak_threshold_dbm=-72.0)
    assert hist["total"] == 0
    assert sum(b["count"] for b in hist["bins"]) == 0
    assert hist["weak_count"] == 0
    assert hist["median_dbm"] is None
    assert hist["min_dbm"] is None and hist["max_dbm"] is None


def test_histogram_weak_tail_matches_coverage_floor() -> None:
    # A value exactly at the threshold is NOT weak (classifier is rssi < threshold);
    # anything below it is. -73 is weak, -72 is not.
    values = [-72.0, -73.0, -80.0, -60.0]
    hist = charts.rssi_histogram(values, weak_threshold_dbm=-72.0)
    # weak bins are those whose ceil <= -72; count of values in them = 2 (-73, -80).
    assert hist["weak_count"] == 2
    for b in hist["bins"]:
        if b["ceil"] is not None and b["ceil"] <= -72:
            assert b["weak"] is True
        else:
            assert b["weak"] is False


def test_histogram_threshold_injected_as_edge() -> None:
    hist = charts.rssi_histogram([-70.0], weak_threshold_dbm=-70.0)
    ceils = [b["ceil"] for b in hist["bins"] if b["ceil"] is not None]
    assert -70 in ceils  # the weak threshold is always a bin edge


# --------------------------------------------------------------------------- #
# Neighbour density (aggregated, never per-BSSID)
# --------------------------------------------------------------------------- #
def test_neighbor_density_aggregates_per_channel() -> None:
    rows = (
        [{"band": "2.4", "channel": 6}] * 4
        + [{"band": "2.4", "channel": 11}] * 2
        + [{"band": "5", "channel": 36}]
    )
    dens = charts.neighbor_density(rows)
    assert dens["total"] == 7
    # 7 BSSes collapse to 3 channel bars -- aggregated, not one row per BSSID.
    assert len(dens["by_channel"]) == 3
    by = {(b["band"], b["channel"]): b["count"] for b in dens["by_channel"]}
    assert by == {("2.4", 6): 4, ("2.4", 11): 2, ("5", 36): 1}
    assert sum(b["count"] for b in dens["by_channel"]) == 7


def test_neighbor_density_empty() -> None:
    dens = charts.neighbor_density([])
    assert dens == {"total": 0, "by_channel": [], "by_band": []}


def test_neighbor_density_row_without_channel_counts_total_only() -> None:
    rows = [{"band": "2.4", "channel": None}, {"band": "2.4", "channel": 6}]
    dens = charts.neighbor_density(rows)
    assert dens["total"] == 2
    assert len(dens["by_channel"]) == 1  # only the row with a channel gets a bar
    assert {b["band"]: b["count"] for b in dens["by_band"]} == {"2.4": 2}


# --------------------------------------------------------------------------- #
# Clients per AP
# --------------------------------------------------------------------------- #
def test_clients_per_ap_sorted_busiest_first_and_keeps_zero() -> None:
    aps = [
        {"entity_id": 1, "name": "quiet"},
        {"entity_id": 2, "name": "busy"},
        {"entity_id": 3, "name": "empty"},
    ]
    bars = charts.clients_per_ap(aps, {1: 3, 2: 9})
    assert [b["name"] for b in bars] == ["busy", "quiet", "empty"]
    assert bars[-1]["client_count"] == 0  # an AP with no clients is kept honestly


# --------------------------------------------------------------------------- #
# Health trend (gaps stay gaps)
# --------------------------------------------------------------------------- #
def test_health_trend_blends_and_omits_gaps() -> None:
    start, end = 0, 1000
    # One coarse bucket with coverage 90/100 -> 0.9; the rest empty.
    rows = [
        {"sle": "coverage", "classifier": "ok", "bucket_ts": 100, "minutes": 90.0},
        {"sle": "coverage", "classifier": "weak_signal", "bucket_ts": 100, "minutes": 10.0},
    ]
    weights = {"coverage": 0.25}
    trend = charts.health_trend(rows, start, end, buckets=10, weights=weights)
    # Exactly one point (the one bucket with data); empty buckets are omitted.
    assert len(trend) == 1
    assert trend[0]["score"] == 90
    assert trend[0]["ts"] == 100


def test_health_trend_empty_is_empty_list() -> None:
    assert charts.health_trend([], 0, 1000, buckets=10, weights={"coverage": 1.0}) == []
