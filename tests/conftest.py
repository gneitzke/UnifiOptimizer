"""Shared pytest fixtures for UniFi Optimizer tests.

Provides mock devices, clients, events, and analysis data that can be
reused across test modules.
"""

import os
import sys

import pytest

# Ensure project root is on sys.path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Mock Devices
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_wired_ap():
    """A wired-uplink AP with 2.4 + 5 GHz radios."""
    return {
        "_id": "ap_wired_001",
        "mac": "aa:bb:cc:dd:ee:01",
        "name": "AP-Living-Room",
        "type": "uap",
        "model": "U6-LR",
        "adopted": True,
        "state": 1,
        "uplink": {"type": "wire"},
        "radio_table": [
            {
                "radio": "ng",
                "channel": 6,
                "ht": 20,
                "tx_power_mode": "auto",
                "tx_power": 20,
                "min_rssi_enabled": False,
            },
            {
                "radio": "na",
                "channel": 36,
                "ht": 80,
                "tx_power_mode": "high",
                "tx_power": 27,
                "min_rssi_enabled": False,
            },
        ],
    }


@pytest.fixture
def mock_mesh_child_ap():
    """A wireless-uplink (mesh child) AP."""
    return {
        "_id": "ap_mesh_001",
        "mac": "aa:bb:cc:dd:ee:02",
        "name": "AP-Garage-Mesh",
        "type": "uap",
        "model": "U6-Mesh",
        "adopted": True,
        "state": 1,
        "uplink": {
            "type": "wireless",
            "rssi": -68,
            "uplink_remote_mac": "aa:bb:cc:dd:ee:01",
        },
        "radio_table": [
            {"radio": "ng", "channel": 1, "ht": 20, "tx_power_mode": "high", "tx_power": 20},
            {"radio": "na", "channel": 44, "ht": 80, "tx_power_mode": "high", "tx_power": 27},
        ],
    }


@pytest.fixture
def mock_6ghz_ap():
    """A tri-band AP with 6 GHz radio."""
    return {
        "_id": "ap_6ghz_001",
        "mac": "aa:bb:cc:dd:ee:03",
        "name": "AP-Office-WiFi7",
        "type": "uap",
        "model": "U7-Pro",
        "adopted": True,
        "state": 1,
        "uplink": {"type": "wire"},
        "radio_table": [
            {"radio": "ng", "channel": 11, "ht": 20, "tx_power_mode": "auto", "tx_power": 18},
            {"radio": "na", "channel": 149, "ht": 80, "tx_power_mode": "high", "tx_power": 27},
            {"radio": "6e", "channel": 37, "ht": 160, "tx_power_mode": "auto", "tx_power": 30},
        ],
    }


@pytest.fixture
def mock_all_devices(mock_wired_ap, mock_mesh_child_ap, mock_6ghz_ap):
    """List of all mock AP devices."""
    return [mock_wired_ap, mock_mesh_child_ap, mock_6ghz_ap]


# ---------------------------------------------------------------------------
# Mock Clients
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_wireless_client_good():
    """A well-connected 5 GHz client."""
    return {
        "mac": "11:22:33:44:55:01",
        "hostname": "MacBook-Pro",
        "name": "MacBook-Pro",
        "ap_mac": "aa:bb:cc:dd:ee:01",
        "radio": "na",
        "radio_proto": "ax",
        "rssi": -52,
        "channel": 36,
        "is_wired": False,
        "satisfaction": 98,
    }


@pytest.fixture
def mock_wireless_client_weak():
    """A client with weak signal in possible dead zone."""
    return {
        "mac": "11:22:33:44:55:02",
        "hostname": "IoT-Sensor",
        "name": "IoT-Sensor",
        "ap_mac": "aa:bb:cc:dd:ee:01",
        "radio": "ng",
        "radio_proto": "n",
        "rssi": -82,
        "channel": 6,
        "is_wired": False,
        "satisfaction": 45,
    }


@pytest.fixture
def mock_wireless_client_wrong_band():
    """A 5 GHz-capable client stuck on 2.4 GHz."""
    return {
        "mac": "11:22:33:44:55:03",
        "hostname": "iPhone-15",
        "name": "iPhone-15",
        "ap_mac": "aa:bb:cc:dd:ee:01",
        "radio": "ng",
        "radio_proto": "ax",
        "rssi": -55,
        "channel": 6,
        "is_wired": False,
        "satisfaction": 80,
    }


@pytest.fixture
def mock_wired_client():
    """A wired client."""
    return {
        "mac": "11:22:33:44:55:04",
        "hostname": "Desktop-PC",
        "is_wired": True,
    }


@pytest.fixture
def mock_all_clients(
    mock_wireless_client_good,
    mock_wireless_client_weak,
    mock_wireless_client_wrong_band,
    mock_wired_client,
):
    """List of all mock clients."""
    return [
        mock_wireless_client_good,
        mock_wireless_client_weak,
        mock_wireless_client_wrong_band,
        mock_wired_client,
    ]


# ---------------------------------------------------------------------------
# Mock Recommendations
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_channel_recommendation(mock_wired_ap):
    """A sample channel-change recommendation."""
    return {
        "ap": {"name": "AP-Living-Room", "mac": "aa:bb:cc:dd:ee:01", "is_mesh": False},
        "type": "channel_optimization",
        "issue": "channel overlap",
        "radio": {"radio": "ng", "channel": 6, "tx_power_mode": "auto"},
        "band": "2.4GHz",
        "new_channel": 11,
        "message": "Channel 6 has 3 overlapping APs",
        "recommendation": "Change to channel 11",
        "priority": "medium",
        "affected_clients": 5,
    }


@pytest.fixture
def mock_power_recommendation(mock_wired_ap):
    """A sample power-change recommendation."""
    return {
        "ap": {"name": "AP-Living-Room", "mac": "aa:bb:cc:dd:ee:01", "is_mesh": False},
        "type": "power_optimization",
        "issue": "power too high",
        "radio": {"radio": "na", "channel": 36, "tx_power_mode": "high", "tx_power": 27},
        "band": "5GHz",
        "message": "TX power is high with nearby APs",
        "recommendation": "Reduce to medium",
        "priority": "low",
        "affected_clients": 8,
    }


@pytest.fixture
def mock_mesh_power_recommendation(mock_mesh_child_ap):
    """A power recommendation targeting a mesh AP (should be skipped)."""
    return {
        "ap": {
            "name": "AP-Garage-Mesh",
            "mac": "aa:bb:cc:dd:ee:02",
            "is_mesh": True,
        },
        "type": "power_optimization",
        "issue": "power too high",
        "radio": {"radio": "na", "channel": 44, "tx_power_mode": "high"},
        "band": "5GHz",
        "message": "TX power is high",
        "recommendation": "Reduce to medium",
        "priority": "low",
        "affected_clients": 2,
    }


@pytest.fixture
def mock_min_rssi_recommendation():
    """A sample min-RSSI recommendation."""
    return {
        "ap": {"name": "AP-Living-Room", "mac": "aa:bb:cc:dd:ee:01", "is_mesh": False},
        "type": "min_rssi_disabled",
        "issue": "min_rssi not configured",
        "radio": "ng",
        "band": "2.4GHz",
        "recommended_value": -75,
        "message": "Min RSSI not enabled on 2.4GHz",
        "recommendation": "Enable min RSSI at -75 dBm",
        "priority": "medium",
    }


@pytest.fixture
def mock_band_steering_recommendation():
    """A sample band-steering recommendation."""
    return {
        "ap": {"name": "AP-Living-Room", "mac": "aa:bb:cc:dd:ee:01", "is_mesh": False},
        "type": "band_steering",
        "issue": "band_steering disabled",
        "message": "Band steering not enabled",
        "recommendation": "Enable prefer-5G band steering",
        "priority": "medium",
        "affected_clients": 3,
    }


# ---------------------------------------------------------------------------
# Mock Analysis Data
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_health_score():
    """A sample health score dict."""
    return {
        "score": 82,
        "grade": "B",
        "status": "Good",
        "components": {
            "rf_health": 78,
            "client_health": 85,
            "infrastructure": 88,
            "security": 80,
        },
    }


@pytest.fixture
def mock_analysis_result(mock_all_devices, mock_all_clients, mock_health_score):
    """A minimal but complete analysis result dict."""
    return {
        "devices": mock_all_devices,
        "clients": mock_all_clients,
        "ap_analysis": {
            "total_aps": 3,
            "mesh_aps": [mock_all_devices[1]],
            "wired_aps": [mock_all_devices[0], mock_all_devices[2]],
        },
        "client_analysis": {
            "total_clients": 4,
            "clients": mock_all_clients,
            "weak_signal": [mock_all_clients[1]],
            "frequent_disconnects": [],
            "poor_health": [],
            "signal_distribution": {
                "excellent": 1,
                "good": 1,
                "fair": 0,
                "poor": 1,
                "critical": 0,
                "wired": 1,
            },
        },
        "recommendations": [],
        "health_score": mock_health_score,
        "health_analysis": {"categories": {}, "issues": []},
        "dfs_analysis": {"total_events": 0},
        "band_steering_analysis": {},
        "min_rssi_analysis": {"ios_device_count": 1},
        "airtime_analysis": {"saturated_aps": []},
        "client_findings": [],
        "api_errors": None,
        "has_incomplete_data": False,
    }
