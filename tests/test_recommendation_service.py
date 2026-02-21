"""Tests for core.services.recommendation_service — recommendation conversion logic."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.services.recommendation_service import convert_recommendations


class TestConvertChannelRecommendations:
    """Channel-change recommendation conversion."""

    def test_basic_channel_change(self, mock_channel_recommendation, mock_all_devices):
        converted, skipped = convert_recommendations(
            [mock_channel_recommendation], mock_all_devices
        )
        assert len(converted) == 1
        assert converted[0]["action"] == "channel_change"
        assert converted[0]["new_channel"] == 11
        assert converted[0]["current_channel"] == 6
        assert converted[0]["radio"] == "ng"

    def test_channel_fallback_24ghz(self, mock_all_devices):
        rec = {
            "ap": {"name": "AP-Living-Room", "mac": "aa:bb:cc:dd:ee:01", "is_mesh": False},
            "type": "channel_optimization",
            "issue": "channel overlap",
            "radio": {"radio": "ng", "channel": 1},
            "band": "2.4GHz",
            "message": "Overlap",
            "recommendation": "Change channel",
            "priority": "medium",
        }
        converted, _ = convert_recommendations([rec], mock_all_devices)
        assert converted[0]["new_channel"] == 6

    def test_channel_fallback_5ghz_dfs(self, mock_all_devices):
        rec = {
            "ap": {"name": "AP-Living-Room", "mac": "aa:bb:cc:dd:ee:01", "is_mesh": False},
            "type": "channel_optimization",
            "issue": "channel overlap",
            "radio": {"radio": "na", "channel": 100},
            "band": "5GHz",
            "message": "DFS channel",
            "recommendation": "Move off DFS",
            "priority": "high",
        }
        converted, _ = convert_recommendations([rec], mock_all_devices)
        assert converted[0]["new_channel"] == 36


class TestConvertPowerRecommendations:
    """Power-change recommendation conversion."""

    def test_basic_power_change(self, mock_power_recommendation, mock_all_devices):
        # Use the 6GHz AP which is NOT a mesh parent
        rec = mock_power_recommendation.copy()
        rec["ap"] = {"name": "AP-Office-WiFi7", "mac": "aa:bb:cc:dd:ee:03", "is_mesh": False}
        converted, skipped = convert_recommendations([rec], mock_all_devices)
        assert len(converted) == 1
        assert converted[0]["action"] == "power_change"
        assert converted[0]["new_power"] == "medium"
        assert converted[0]["current_power"] == "high"

    def test_mesh_ap_power_skipped(self, mock_mesh_power_recommendation, mock_all_devices):
        converted, skipped = convert_recommendations(
            [mock_mesh_power_recommendation], mock_all_devices
        )
        assert len(converted) == 0
        assert len(skipped) == 1
        assert skipped[0]["reason"] == "mesh AP"

    def test_mesh_parent_power_skipped(self, mock_all_devices):
        """Power change should be skipped for APs that are mesh parents."""
        rec = {
            "ap": {"name": "AP-Living-Room", "mac": "aa:bb:cc:dd:ee:01", "is_mesh": False},
            "type": "power_optimization",
            "issue": "power too high",
            "radio": {"radio": "na", "channel": 36, "tx_power_mode": "high"},
            "band": "5GHz",
            "message": "Power high",
            "recommendation": "Reduce",
            "priority": "low",
            "affected_clients": 5,
        }
        # mock_all_devices[1] is a mesh child with uplink_remote_mac pointing to [0]
        converted, skipped = convert_recommendations([rec], mock_all_devices)
        assert len(converted) == 0
        assert len(skipped) == 1


class TestConvertBandSteeringRecommendations:

    def test_basic_band_steering(self, mock_band_steering_recommendation, mock_all_devices):
        converted, _ = convert_recommendations(
            [mock_band_steering_recommendation], mock_all_devices
        )
        assert len(converted) == 1
        assert converted[0]["action"] == "band_steering"
        assert converted[0]["new_mode"] == "prefer_5g"


class TestConvertMinRssiRecommendations:

    def test_basic_min_rssi(self, mock_min_rssi_recommendation, mock_all_devices):
        converted, _ = convert_recommendations(
            [mock_min_rssi_recommendation], mock_all_devices
        )
        assert len(converted) == 1
        assert converted[0]["action"] == "min_rssi"
        assert converted[0]["new_enabled"] is True
        assert converted[0]["new_value"] == -75

    def test_mesh_min_rssi_skipped(self, mock_all_devices):
        rec = {
            "ap": {"name": "AP-Garage-Mesh", "mac": "aa:bb:cc:dd:ee:02", "is_mesh": True},
            "type": "mesh_min_rssi_danger",
            "issue": "min_rssi on mesh",
            "message": "Min RSSI dangerous on mesh",
            "recommendation": "Disable min RSSI",
            "priority": "high",
        }
        converted, _ = convert_recommendations([rec], mock_all_devices)
        assert len(converted) == 0


class TestConvertMixed:

    def test_empty_input(self):
        converted, skipped = convert_recommendations([], [])
        assert converted == []
        assert skipped == []

    def test_rec_without_ap_info_skipped(self):
        rec = {"type": "unknown", "issue": "something", "message": "m", "recommendation": "r"}
        converted, _ = convert_recommendations([rec], [])
        assert len(converted) == 0

    def test_unknown_device_creates_minimal(self):
        rec = {
            "ap": {"name": "Unknown-AP", "mac": "ff:ff:ff:ff:ff:ff", "is_mesh": False},
            "type": "channel_optimization",
            "issue": "channel",
            "radio": {"radio": "ng", "channel": 6},
            "band": "2.4GHz",
            "message": "msg",
            "recommendation": "rec",
            "priority": "low",
        }
        converted, _ = convert_recommendations([rec], [])
        assert len(converted) == 1
        assert converted[0]["device"]["mac"] == "ff:ff:ff:ff:ff:ff"
