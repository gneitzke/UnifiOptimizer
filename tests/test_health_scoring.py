#!/usr/bin/env python3
"""
Unit tests for Phase 2: health scoring, client findings, and RF improvements.
Runs with: python3 -m unittest tests/test_health_scoring.py
"""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestRSSIToQuality(unittest.TestCase):
    """Tests for continuous RSSI-to-quality curve."""

    def setUp(self):
        from core.client_health import ClientHealthAnalyzer
        self.analyzer = ClientHealthAnalyzer()

    def test_excellent_signal(self):
        score = self.analyzer._rssi_to_quality(-50)
        self.assertEqual(score, 100)

    def test_worst_signal(self):
        score = self.analyzer._rssi_to_quality(-95)
        self.assertEqual(score, 0)

    def test_midpoint_signal(self):
        score = self.analyzer._rssi_to_quality(-72)
        # Should be roughly 51 ((95-72)/45*100 ≈ 51.1)
        self.assertAlmostEqual(score, 51.1, delta=1)

    def test_continuous_no_jumps(self):
        """Verify no sudden jumps — adjacent dBm values should differ by ~2.2 points."""
        prev = self.analyzer._rssi_to_quality(-50)
        for rssi in range(-51, -95, -1):
            current = self.analyzer._rssi_to_quality(rssi)
            delta = abs(prev - current)
            self.assertLessEqual(delta, 3, f"Jump too large at {rssi} dBm: {delta}")
            prev = current

    def test_clamp_above_range(self):
        self.assertEqual(self.analyzer._rssi_to_quality(-30), 100)

    def test_clamp_below_range(self):
        self.assertEqual(self.analyzer._rssi_to_quality(-110), 0)

    def test_gap_fixed_between_80_and_90(self):
        """The old code gave -81 and -95 the same score (20). The new curve should differentiate."""
        score_81 = self.analyzer._rssi_to_quality(-81)
        score_95 = self.analyzer._rssi_to_quality(-95)
        self.assertGreater(score_81, score_95 + 10, "Client at -81 should score significantly higher than -95")


class TestStabilityScore(unittest.TestCase):
    """Tests for disconnect stability scoring."""

    def setUp(self):
        from core.client_health import ClientHealthAnalyzer
        self.analyzer = ClientHealthAnalyzer()

    def test_no_disconnects_is_perfect(self):
        self.assertEqual(self.analyzer._stability_score(0), 100)

    def test_few_disconnects_moderate_penalty(self):
        score = self.analyzer._stability_score(3, lookback_days=3)
        # 1 disconnect/day → ~70
        self.assertGreater(score, 60)
        self.assertLess(score, 80)

    def test_many_disconnects_low_score(self):
        score = self.analyzer._stability_score(15, lookback_days=3)
        self.assertLess(score, 30)

    def test_capped_not_negative(self):
        score = self.analyzer._stability_score(100, lookback_days=1)
        self.assertGreaterEqual(score, 0)

    def test_normalized_by_lookback(self):
        """Same disconnect count over longer period should score higher."""
        short = self.analyzer._stability_score(6, lookback_days=1)
        long = self.analyzer._stability_score(6, lookback_days=7)
        self.assertGreater(long, short)


class TestRoamingHealth(unittest.TestCase):
    """Tests for roaming health scoring."""

    def setUp(self):
        from core.client_health import ClientHealthAnalyzer
        self.analyzer = ClientHealthAnalyzer()

    def test_no_roaming_good_signal(self):
        score = self.analyzer._roaming_health(0, -55)
        self.assertGreater(score, 90, "Good signal + no roaming = great")

    def test_no_roaming_poor_signal_is_sticky(self):
        score = self.analyzer._roaming_health(0, -82)
        self.assertLess(score, 40, "Poor signal + no roaming = sticky client problem")

    def test_moderate_roaming_good_signal(self):
        score = self.analyzer._roaming_health(15, -55, lookback_days=3)
        self.assertGreater(score, 80, "Healthy roaming should be rewarded")

    def test_excessive_roaming_low_score(self):
        score = self.analyzer._roaming_health(100, -65, lookback_days=3)
        self.assertLess(score, 50, "Excessive roaming suggests AP flapping")


class TestWeightedComposite(unittest.TestCase):
    """Tests for the full weighted composite health score."""

    def setUp(self):
        from core.client_health import ClientHealthAnalyzer
        self.analyzer = ClientHealthAnalyzer()

    def test_wired_clients_excluded_from_wireless_scoring(self):
        self.analyzer.clients = [
            {"mac": "aa:bb:cc:dd:ee:ff", "is_wired": True, "hostname": "Server"},
        ]
        scores = self.analyzer._calculate_health_scores()
        self.assertEqual(len(scores), 1)
        self.assertTrue(scores[0]["is_wired"])
        self.assertEqual(scores[0]["score"], 100)

    def test_excellent_wireless_client(self):
        self.analyzer.clients = [
            {"mac": "11:22:33:44:55:66", "rssi": -45, "hostname": "Phone",
             "is_wired": False, "radio_proto": "ax", "tx_rate": 1000},
        ]
        scores = self.analyzer._calculate_health_scores()
        self.assertGreater(scores[0]["score"], 85)

    def test_poor_wireless_client(self):
        self.analyzer.clients = [
            {"mac": "11:22:33:44:55:66", "rssi": -85, "hostname": "Camera",
             "is_wired": False, "radio_proto": "n", "tx_rate": 10},
        ]
        scores = self.analyzer._calculate_health_scores()
        self.assertLessEqual(scores[0]["score"], 40)

    def test_positive_rssi_normalized(self):
        self.analyzer.clients = [
            {"mac": "11:22:33:44:55:66", "rssi": 65, "hostname": "Phone",
             "is_wired": False, "radio_proto": "ax", "tx_rate": 500},
        ]
        scores = self.analyzer._calculate_health_scores()
        # rssi=65 should be normalized to -65 → ~66 signal quality
        self.assertGreater(scores[0]["signal_score"], 50)
        self.assertLess(scores[0]["signal_score"], 80)


class TestNetworkHealthScore(unittest.TestCase):
    """Tests for the category-weighted network health scoring."""

    def test_channel_sharing_not_penalized(self):
        """Normal 2.4GHz channel sharing should NOT reduce score to 85."""
        from unittest.mock import MagicMock
        from core.network_health_analyzer import NetworkHealthAnalyzer

        analyzer = NetworkHealthAnalyzer(MagicMock(), "default")

        # 7 APs evenly distributed across channels 1, 6, 11
        devices = []
        channels = [1, 1, 6, 6, 11, 11, 11]
        for i, ch in enumerate(channels):
            devices.append({
                "type": "uap",
                "name": f"AP-{i}",
                "mac": f"aa:bb:cc:dd:ee:{i:02x}",
                "uplink": {"type": "wire"},
                "radio_table": [
                    {"radio": "ng", "channel": ch, "tx_power": 20, "tx_power_mode": "auto",
                     "channel_utilization": 30},
                    {"radio": "na", "channel": 36 + i * 4, "tx_power": 23, "tx_power_mode": "auto",
                     "channel_utilization": 20},
                ],
                "uptime": 604800,
                "state": 1,
                "version": "6.6.55",
                "upgradable": False,
            })

        result = analyzer._analyze_channel_health(devices)
        # With 7 APs and 3 channels: max is 3 per channel (11)
        # expected_per_channel = 7/3 ≈ 2.33
        # 3 APs on ch11 = 3, threshold = 2.33 + 1.5 = 3.83 → NOT penalized
        self.assertLessEqual(result["score_penalty"], 0,
                             "Balanced 7-AP distribution should not be penalized")

    def test_component_scores_present(self):
        """Health analysis should include component_scores breakdown."""
        from unittest.mock import MagicMock
        from core.network_health_analyzer import NetworkHealthAnalyzer

        analyzer = NetworkHealthAnalyzer(MagicMock(), "default")
        result = analyzer.analyze_network_health([], [])
        self.assertIn("component_scores", result)
        self.assertIn("rf_health", result["component_scores"])
        self.assertIn("client_health", result["component_scores"])
        self.assertIn("infrastructure", result["component_scores"])
        self.assertIn("security", result["component_scores"])


class TestClientFindings(unittest.TestCase):
    """Tests for per-client findings generation."""

    def test_wrong_band_detection(self):
        from core.optimize_network import generate_client_findings

        clients = [
            {"mac": "aa:bb", "hostname": "Phone", "rssi": -55, "radio": "ng",
             "radio_proto": "ac", "ap_mac": "ap1", "is_wired": False},
        ]
        devices = [{"type": "uap", "mac": "ap1", "name": "Living Room"}]
        findings = generate_client_findings(clients, devices)

        wrong_band = [f for f in findings if f["type"] == "wrong_band"]
        self.assertEqual(len(wrong_band), 1)
        self.assertIn("2.4GHz", wrong_band[0]["message"])

    def test_dead_zone_detection(self):
        from core.optimize_network import generate_client_findings

        clients = [
            {"mac": "aa:bb", "hostname": "Camera", "rssi": -87, "radio": "na",
             "radio_proto": "n", "ap_mac": "ap1", "is_wired": False},
        ]
        devices = [{"type": "uap", "mac": "ap1", "name": "Garage"}]
        findings = generate_client_findings(clients, devices)

        dead_zone = [f for f in findings if f["type"] == "dead_zone"]
        self.assertEqual(len(dead_zone), 1)
        self.assertEqual(dead_zone[0]["severity"], "high")

    def test_wired_clients_ignored(self):
        from core.optimize_network import generate_client_findings

        clients = [{"mac": "aa:bb", "hostname": "Server", "is_wired": True}]
        findings = generate_client_findings(clients, [])
        self.assertEqual(len(findings), 0)

    def test_good_wireless_client_no_findings(self):
        from core.optimize_network import generate_client_findings

        clients = [
            {"mac": "aa:bb", "hostname": "Phone", "rssi": -55, "radio": "na",
             "radio_proto": "ax", "ap_mac": "ap1", "is_wired": False},
        ]
        findings = generate_client_findings(clients, [])
        self.assertEqual(len(findings), 0)


class Test6GHzDetection(unittest.TestCase):
    """Tests for B13: 6GHz radio detection should NOT include 'ax'."""

    def test_ax_not_classified_as_6ghz(self):
        """802.11ax (WiFi 6) radio should be classified as 5GHz, not 6GHz."""
        from core.network_health_analyzer import NetworkHealthAnalyzer
        from unittest.mock import MagicMock

        analyzer = NetworkHealthAnalyzer(MagicMock(), "default")
        devices = [{
            "type": "uap",
            "name": "TestAP",
            "mac": "aa:bb:cc:dd:ee:ff",
            "uplink": {"type": "wire"},
            "radio_table": [
                {"radio": "ax", "channel": 36, "tx_power": 20, "tx_power_mode": "auto"},
            ],
            "uptime": 604800,
        }]
        result = analyzer._analyze_radio_health(devices)
        # "ax" radio should be "Unknown" band, NOT "6GHz"
        for radio_info in result["radios"]:
            if radio_info["radio"] == "ax":
                self.assertNotEqual(radio_info["band"], "6GHz",
                                    "'ax' radio should NOT be classified as 6GHz")


if __name__ == "__main__":
    unittest.main()
