"""Tests for core.services.analysis_service — client findings generation."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.services.analysis_service import generate_client_findings


class TestGenerateClientFindings:
    """Test per-client findings generation."""

    def test_wrong_band_detected(self, mock_wireless_client_wrong_band, mock_all_devices):
        findings = generate_client_findings([mock_wireless_client_wrong_band], mock_all_devices)
        wrong_band = [f for f in findings if f["type"] == "wrong_band"]
        assert len(wrong_band) == 1
        assert "iPhone-15" in wrong_band[0]["message"]
        assert wrong_band[0]["severity"] == "medium"

    def test_dead_zone_detected(self, mock_wireless_client_weak, mock_all_devices):
        findings = generate_client_findings([mock_wireless_client_weak], mock_all_devices)
        dead_zone = [f for f in findings if f["type"] == "dead_zone"]
        assert len(dead_zone) == 1
        assert dead_zone[0]["rssi"] == -82
        assert "IoT-Sensor" in dead_zone[0]["message"]

    def test_very_weak_is_high_severity(self, mock_all_devices):
        client = {
            "mac": "ff:ff:ff:ff:ff:01",
            "hostname": "Far-Device",
            "radio": "ng",
            "radio_proto": "n",
            "rssi": -90,
            "ap_mac": "aa:bb:cc:dd:ee:01",
            "is_wired": False,
        }
        findings = generate_client_findings([client], mock_all_devices)
        dead_zone = [f for f in findings if f["type"] == "dead_zone"]
        assert dead_zone[0]["severity"] == "high"

    def test_good_client_no_findings(self, mock_wireless_client_good, mock_all_devices):
        findings = generate_client_findings([mock_wireless_client_good], mock_all_devices)
        assert len(findings) == 0

    def test_wired_clients_ignored(self, mock_wired_client, mock_all_devices):
        findings = generate_client_findings([mock_wired_client], mock_all_devices)
        assert len(findings) == 0

    def test_empty_input(self):
        assert generate_client_findings([], []) == []
        assert generate_client_findings(None, []) == []

    def test_positive_rssi_normalized(self, mock_all_devices):
        """Positive RSSI values should be normalized to negative."""
        client = {
            "mac": "ff:ff:ff:ff:ff:02",
            "hostname": "Positive-RSSI",
            "radio": "ng",
            "radio_proto": "n",
            "rssi": 85,  # positive = bug in controller, should become -85
            "ap_mac": "aa:bb:cc:dd:ee:01",
            "is_wired": False,
        }
        findings = generate_client_findings([client], mock_all_devices)
        dead_zone = [f for f in findings if f["type"] == "dead_zone"]
        assert len(dead_zone) == 1
        assert dead_zone[0]["rssi"] == -85

    def test_mixed_clients(self, mock_all_clients, mock_all_devices):
        """Test with a mix of good, bad, and wired clients."""
        findings = generate_client_findings(mock_all_clients, mock_all_devices)
        types = [f["type"] for f in findings]
        assert "wrong_band" in types
        assert "dead_zone" in types
        # Good client and wired client should not produce findings
        assert len(findings) == 2
