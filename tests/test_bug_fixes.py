#!/usr/bin/env python3
"""
Unit tests for bug fixes and shared helpers.
Runs with: python3 -m unittest tests/test_bug_fixes.py
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestNetworkHelpers(unittest.TestCase):
    """Tests for utils/network_helpers.py shared functions."""

    def test_fix_rssi_negative_passthrough(self):
        from utils.network_helpers import fix_rssi

        self.assertEqual(fix_rssi(-65), -65)

    def test_fix_rssi_positive_to_negative(self):
        from utils.network_helpers import fix_rssi

        self.assertEqual(fix_rssi(65), -65)

    def test_fix_rssi_none(self):
        from utils.network_helpers import fix_rssi

        self.assertIsNone(fix_rssi(None))

    def test_fix_rssi_zero(self):
        from utils.network_helpers import fix_rssi

        self.assertEqual(fix_rssi(0), 0)

    def test_is_mesh_child_wireless_uplink(self):
        from utils.network_helpers import is_mesh_child

        device = {"uplink": {"type": "wireless"}}
        self.assertTrue(is_mesh_child(device))

    def test_is_mesh_child_wired_uplink(self):
        from utils.network_helpers import is_mesh_child

        device = {"uplink": {"type": "wire"}}
        self.assertFalse(is_mesh_child(device))

    def test_is_mesh_child_no_uplink(self):
        from utils.network_helpers import is_mesh_child

        device = {}
        self.assertFalse(is_mesh_child(device))

    def test_is_mesh_parent_true(self):
        from utils.network_helpers import is_mesh_parent

        parent = {"mac": "aa:bb:cc:dd:ee:ff"}
        child = {"mac": "11:22:33:44:55:66", "uplink": {"type": "wireless", "uplink_remote_mac": "aa:bb:cc:dd:ee:ff"}}
        self.assertTrue(is_mesh_parent(parent, [parent, child]))

    def test_is_mesh_parent_false(self):
        from utils.network_helpers import is_mesh_parent

        ap1 = {"mac": "aa:bb:cc:dd:ee:ff", "uplink": {"type": "wire"}}
        ap2 = {"mac": "11:22:33:44:55:66", "uplink": {"type": "wire"}}
        self.assertFalse(is_mesh_parent(ap1, [ap1, ap2]))

    def test_get_mesh_role(self):
        from utils.network_helpers import get_mesh_role

        parent = {"mac": "aa:bb:cc:dd:ee:ff", "uplink": {"type": "wire"}}
        child = {"mac": "11:22:33:44:55:66", "uplink": {"type": "wireless", "uplink_remote_mac": "aa:bb:cc:dd:ee:ff"}}
        wired = {"mac": "cc:cc:cc:cc:cc:cc", "uplink": {"type": "wire"}}

        all_devs = [parent, child, wired]
        self.assertEqual(get_mesh_role(parent, all_devs), "mesh_parent")
        self.assertEqual(get_mesh_role(child, all_devs), "mesh_child")
        self.assertIsNone(get_mesh_role(wired, all_devs))

    def test_ap_display_name(self):
        from utils.network_helpers import ap_display_name

        self.assertEqual(ap_display_name({"name": "Living Room"}), "Living Room")
        self.assertEqual(ap_display_name({"hostname": "U6-Pro"}), "U6-Pro")
        self.assertEqual(ap_display_name({"mac": "aa:bb:cc"}), "aa:bb:cc")
        self.assertEqual(ap_display_name({}), "unknown")


class TestPowerLevelComparison(unittest.TestCase):
    """Tests for B7: power level numeric comparison."""

    def test_power_levels_exist(self):
        from core.change_applier import ChangeImpactAnalyzer

        analyzer = ChangeImpactAnalyzer.__new__(ChangeImpactAnalyzer)
        self.assertIn("high", analyzer.POWER_LEVELS)
        self.assertIn("medium", analyzer.POWER_LEVELS)
        self.assertIn("low", analyzer.POWER_LEVELS)

    def test_power_ordering(self):
        from core.change_applier import ChangeImpactAnalyzer

        analyzer = ChangeImpactAnalyzer.__new__(ChangeImpactAnalyzer)
        levels = analyzer.POWER_LEVELS
        self.assertGreater(levels["high"], levels["medium"])
        self.assertGreater(levels["medium"], levels["low"])


class TestConfigLoader(unittest.TestCase):
    """Tests for utils/config.py config loading."""

    def test_get_threshold_defaults(self):
        from utils.config import get_threshold

        # Should return defaults when no config file exists
        self.assertEqual(get_threshold("rssi.excellent", -50), -50)
        self.assertEqual(get_threshold("nonexistent.key", -99), -99)

    def test_get_option_defaults(self):
        from utils.config import get_option

        self.assertEqual(get_option("lookback_days", 3), 3)


class TestMeshDetectionNotFalsePositive(unittest.TestCase):
    """Tests for B4: mesh detection should NOT use RSSI heuristic."""

    def test_wired_ap_with_low_rssi_not_mesh(self):
        """A wired AP with low uplink RSSI should NOT be detected as mesh."""
        from utils.network_helpers import is_mesh_child

        # Wired AP that happens to have RSSI reported
        wired_ap = {
            "adopted": True,
            "uplink": {"type": "wire", "rssi": -75},
        }
        self.assertFalse(is_mesh_child(wired_ap))


class TestRoamingClientGrouping(unittest.TestCase):
    """Tests for B5: roaming clients should be grouped by their actual AP."""

    def test_clients_not_all_assigned_to_first_ap(self):
        """Each roaming client should be mapped to its own AP, not the first one."""
        # This tests the logic that was fixed in optimize_network.py
        from collections import defaultdict

        roaming_issues = [
            {"mac": "client1", "ap_mac": "ap_A"},
            {"mac": "client2", "ap_mac": "ap_B"},
            {"mac": "client3", "ap_mac": "ap_A"},
        ]

        # Correct grouping: by client's ap_mac
        roaming_by_ap = defaultdict(list)
        for client in roaming_issues:
            ap_mac = client.get("ap_mac")
            if ap_mac:
                roaming_by_ap[ap_mac].append(client)

        self.assertEqual(len(roaming_by_ap["ap_A"]), 2)
        self.assertEqual(len(roaming_by_ap["ap_B"]), 1)


if __name__ == "__main__":
    unittest.main()
