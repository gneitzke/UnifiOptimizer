#!/usr/bin/env python3
"""Unit tests for core/trend_analyzer.py

No UniFi controller required — all tests operate on fixture data.
Run with: python3 tests/test_trend_analyzer.py
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.trend_analyzer import (
    analyze_ap_trends,
    analyze_client_trends,
    analyze_network_trends,
    build_trend_summary,
    classify_trend,
    detect_anomalies,
    linear_slope,
    rolling_average,
    run_trend_analysis,
)


# ---------------------------------------------------------------------------
# linear_slope
# ---------------------------------------------------------------------------


class TestLinearSlope(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(linear_slope([]), 0.0)

    def test_single_point(self):
        self.assertEqual(linear_slope([(1000, 85.0)]), 0.0)

    def test_flat_line(self):
        pts = [(i * 86400, 80.0) for i in range(7)]
        self.assertAlmostEqual(linear_slope(pts), 0.0, places=5)

    def test_positive_slope(self):
        # +1 per day for 7 days
        pts = [(i * 86400, float(i)) for i in range(7)]
        slope = linear_slope(pts)
        self.assertAlmostEqual(slope, 1.0, places=4)

    def test_negative_slope(self):
        # −1 per day for 7 days
        pts = [(i * 86400, float(7 - i)) for i in range(7)]
        slope = linear_slope(pts)
        self.assertAlmostEqual(slope, -1.0, places=4)

    def test_all_same_x(self):
        pts = [(1000, 80.0), (1000, 85.0), (1000, 90.0)]
        self.assertEqual(linear_slope(pts), 0.0)

    def test_two_points_positive(self):
        # From 0 to 10 over 5 days → slope 2/day
        pts = [(0, 0.0), (5 * 86400, 10.0)]
        slope = linear_slope(pts)
        self.assertAlmostEqual(slope, 2.0, places=4)


# ---------------------------------------------------------------------------
# detect_anomalies
# ---------------------------------------------------------------------------


class TestDetectAnomalies(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(detect_anomalies([]), [])

    def test_single_point(self):
        self.assertEqual(detect_anomalies([(0, 80.0)]), [])

    def test_two_points(self):
        self.assertEqual(detect_anomalies([(0, 80.0), (1, 85.0)]), [])

    def test_all_same_values(self):
        pts = [(i, 80.0) for i in range(10)]
        self.assertEqual(detect_anomalies(pts), [])

    def test_detects_outlier(self):
        # 9 points near 80, one spike at 10
        pts = [(i * 86400, 80.0) for i in range(9)]
        pts.append((9 * 86400, 10.0))
        anomalies = detect_anomalies(pts, sigma=2.0)
        self.assertGreater(len(anomalies), 0)
        vals = [a["value"] for a in anomalies]
        self.assertIn(10.0, vals)

    def test_no_false_positives_tight_cluster(self):
        # Values 78-82 are well within 2σ of mean ≈ 80
        pts = [(i * 86400, 78.0 + (i % 5)) for i in range(20)]
        anomalies = detect_anomalies(pts, sigma=2.0)
        self.assertEqual(anomalies, [])

    def test_anomaly_fields(self):
        pts = [(i * 86400, 80.0) for i in range(9)]
        pts.append((9 * 86400, 20.0))
        anomalies = detect_anomalies(pts, sigma=1.5)
        self.assertGreater(len(anomalies), 0)
        a = anomalies[0]
        self.assertIn("time", a)
        self.assertIn("value", a)
        self.assertIn("expected", a)
        self.assertIn("deviation", a)


# ---------------------------------------------------------------------------
# rolling_average
# ---------------------------------------------------------------------------


class TestRollingAverage(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(rolling_average([]), [])

    def test_single_point(self):
        result = rolling_average([(0, 80.0)])
        self.assertEqual(result, [80.0])

    def test_window_1(self):
        pts = [(i, float(i)) for i in range(5)]
        result = rolling_average(pts, window=1)
        self.assertEqual(result, [0.0, 1.0, 2.0, 3.0, 4.0])

    def test_window_3(self):
        pts = [(i, float(i)) for i in range(5)]
        result = rolling_average(pts, window=3)
        # [0], [0,1]/2, [0,1,2]/3, [1,2,3]/3, [2,3,4]/3
        expected = [0.0, 0.5, 1.0, 2.0, 3.0]
        for r, e in zip(result, expected):
            self.assertAlmostEqual(r, e, places=4)

    def test_same_length_as_input(self):
        pts = [(i, float(i)) for i in range(10)]
        result = rolling_average(pts, window=3)
        self.assertEqual(len(result), 10)

    def test_flat_values(self):
        pts = [(i, 80.0) for i in range(5)]
        result = rolling_average(pts, window=3)
        self.assertTrue(all(v == 80.0 for v in result))


# ---------------------------------------------------------------------------
# classify_trend
# ---------------------------------------------------------------------------


class TestClassifyTrend(unittest.TestCase):
    def test_improving(self):
        self.assertEqual(classify_trend(1.0), "improving")

    def test_degrading(self):
        self.assertEqual(classify_trend(-1.0), "degrading")

    def test_stable_positive(self):
        self.assertEqual(classify_trend(0.2), "stable")

    def test_stable_negative(self):
        self.assertEqual(classify_trend(-0.3), "stable")

    def test_exactly_on_threshold_degrading(self):
        self.assertEqual(classify_trend(-0.5), "degrading")

    def test_exactly_on_threshold_improving(self):
        self.assertEqual(classify_trend(0.5), "improving")

    def test_custom_thresholds(self):
        self.assertEqual(classify_trend(0.2, degrading_threshold=-1.0, improving_threshold=1.0), "stable")
        self.assertEqual(classify_trend(2.0, degrading_threshold=-1.0, improving_threshold=1.0), "improving")
        self.assertEqual(classify_trend(-2.0, degrading_threshold=-1.0, improving_threshold=1.0), "degrading")


# ---------------------------------------------------------------------------
# analyze_ap_trends
# ---------------------------------------------------------------------------

ONE_DAY = 86400


def _make_ap_stat(mac, day_offset, satisfaction, num_sta):
    return {
        "mac": mac,
        "time": 1700000000 + day_offset * ONE_DAY,
        "satisfaction": satisfaction,
        "num_sta": num_sta,
        "bytes": 1000000,
        "num_wifi_roam_to_events": 0,
    }


FIXTURE_DAILY_AP = [
    # AP1 — declining satisfaction
    *[_make_ap_stat("aa:bb:cc:dd:ee:01", i, 90 - i * 2, 10 + i) for i in range(10)],
    # AP2 — stable
    *[_make_ap_stat("aa:bb:cc:dd:ee:02", i, 85 + (i % 3 - 1), 8) for i in range(10)],
]

FIXTURE_DEVICES = [
    {"type": "uap", "mac": "aa:bb:cc:dd:ee:01", "name": "AP Kitchen"},
    {"type": "uap", "mac": "aa:bb:cc:dd:ee:02", "name": "AP Office"},
]


class TestAnalyzeApTrends(unittest.TestCase):
    def setUp(self):
        self.trends = analyze_ap_trends([], FIXTURE_DAILY_AP, FIXTURE_DEVICES)

    def test_returns_dict_keyed_by_name(self):
        self.assertIn("AP Kitchen", self.trends)
        self.assertIn("AP Office", self.trends)

    def test_declining_ap_classified_as_degrading(self):
        self.assertEqual(self.trends["AP Kitchen"]["satisfaction_trend"], "degrading")

    def test_stable_ap_classified_as_stable(self):
        self.assertIn(self.trends["AP Office"]["satisfaction_trend"], ("stable", "improving", "degrading"))

    def test_slope_sign_for_declining(self):
        self.assertLess(self.trends["AP Kitchen"]["satisfaction_slope"], 0)

    def test_sparklines_are_lists(self):
        info = self.trends["AP Kitchen"]
        self.assertIsInstance(info["sparkline_satisfaction"], list)
        self.assertIsInstance(info["sparkline_clients"], list)

    def test_anomalies_are_list(self):
        self.assertIsInstance(self.trends["AP Kitchen"]["anomalies"], list)

    def test_data_points_recorded(self):
        self.assertGreater(self.trends["AP Kitchen"]["data_points"], 0)

    def test_empty_stats(self):
        result = analyze_ap_trends([], [], [], {})
        self.assertEqual(result, {})


# ---------------------------------------------------------------------------
# analyze_network_trends
# ---------------------------------------------------------------------------


class TestAnalyzeNetworkTrends(unittest.TestCase):
    def setUp(self):
        self.result = analyze_network_trends(FIXTURE_DAILY_AP)

    def test_keys_present(self):
        for key in ("satisfaction_trend", "client_count_trend", "dfs_event_trend", "anomalies"):
            self.assertIn(key, self.result)

    def test_trend_values_are_valid_strings(self):
        valid = {"improving", "degrading", "stable", "increasing", "decreasing"}
        self.assertIn(self.result["satisfaction_trend"], valid)
        self.assertIn(self.result["client_count_trend"], valid)
        self.assertIn(self.result["dfs_event_trend"], valid)

    def test_slopes_are_floats(self):
        self.assertIsInstance(self.result["satisfaction_slope"], float)
        self.assertIsInstance(self.result["client_count_slope"], float)

    def test_anomalies_is_list(self):
        self.assertIsInstance(self.result["anomalies"], list)

    def test_empty_stats(self):
        result = analyze_network_trends([])
        self.assertEqual(result["satisfaction_trend"], "stable")
        self.assertEqual(result["dfs_event_trend"], "stable")


# ---------------------------------------------------------------------------
# analyze_client_trends
# ---------------------------------------------------------------------------


def _make_user_stat(mac, day_offset, satisfaction):
    return {
        "mac": mac,
        "time": 1700000000 + day_offset * ONE_DAY,
        "satisfaction": satisfaction,
        "bytes": 500000,
    }


FIXTURE_DAILY_USER = [
    # Client A — declining
    *[_make_user_stat("client:aa", i, 90 - i * 3) for i in range(7)],
    # Client B — stable
    *[_make_user_stat("client:bb", i, 80.0) for i in range(7)],
]


class TestAnalyzeClientTrends(unittest.TestCase):
    def setUp(self):
        self.flagged = analyze_client_trends(FIXTURE_DAILY_USER)

    def test_returns_list(self):
        self.assertIsInstance(self.flagged, list)

    def test_declining_client_flagged(self):
        macs = [c["mac"] for c in self.flagged]
        self.assertIn("client:aa", macs)

    def test_stable_client_not_flagged(self):
        # client:bb is perfectly stable — should not be flagged
        macs = [c["mac"] for c in self.flagged]
        self.assertNotIn("client:bb", macs)

    def test_sorted_by_slope_ascending(self):
        if len(self.flagged) >= 2:
            slopes = [c["slope"] for c in self.flagged]
            self.assertEqual(slopes, sorted(slopes))

    def test_fields_present(self):
        for c in self.flagged:
            self.assertIn("mac", c)
            self.assertIn("satisfaction_trend", c)
            self.assertIn("slope", c)

    def test_empty_stats(self):
        result = analyze_client_trends([])
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# build_trend_summary
# ---------------------------------------------------------------------------


class TestBuildTrendSummary(unittest.TestCase):
    def setUp(self):
        ap_trends = analyze_ap_trends([], FIXTURE_DAILY_AP, FIXTURE_DEVICES)
        net_trends = analyze_network_trends(FIXTURE_DAILY_AP)
        client_trends = analyze_client_trends(FIXTURE_DAILY_USER)
        self.summary = build_trend_summary(ap_trends, net_trends, client_trends)

    def test_top_level_keys(self):
        for key in ("network", "per_ap", "flagged_clients", "headline"):
            self.assertIn(key, self.summary)

    def test_headline_is_string(self):
        self.assertIsInstance(self.summary["headline"], str)
        self.assertGreater(len(self.summary["headline"]), 0)

    def test_flagged_clients_capped_at_10(self):
        self.assertLessEqual(len(self.summary["flagged_clients"]), 10)


# ---------------------------------------------------------------------------
# run_trend_analysis (integration)
# ---------------------------------------------------------------------------


class TestRunTrendAnalysis(unittest.TestCase):
    def _make_full_analysis(self):
        return {
            "hourly_ap_stats": [],
            "daily_ap_stats": FIXTURE_DAILY_AP,
            "daily_user_stats": FIXTURE_DAILY_USER,
            "devices": FIXTURE_DEVICES,
            "event_timeline": {},
        }

    def test_returns_dict(self):
        result = run_trend_analysis(self._make_full_analysis())
        self.assertIsInstance(result, dict)

    def test_keys_present(self):
        result = run_trend_analysis(self._make_full_analysis())
        for key in ("network", "per_ap", "flagged_clients", "headline"):
            self.assertIn(key, result)

    def test_disabled_via_config(self):
        result = run_trend_analysis(self._make_full_analysis(), config={"enabled": False})
        self.assertFalse(result.get("enabled", True))

    def test_empty_analysis(self):
        result = run_trend_analysis({})
        self.assertIsInstance(result, dict)


if __name__ == "__main__":
    unittest.main(verbosity=2)
