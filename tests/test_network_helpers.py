"""Tests for utils/network_helpers.py — mesh detection and RSSI helpers."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.network_helpers import ap_display_name, fix_rssi, get_mesh_role, is_mesh_child, is_mesh_parent


class TestFixRssi:
    """RSSI normalization."""

    def test_negative_rssi_unchanged(self):
        assert fix_rssi(-65) == -65

    def test_positive_rssi_negated(self):
        assert fix_rssi(72) == -72

    def test_zero_rssi(self):
        assert fix_rssi(0) == 0


class TestIsMeshChild:

    def test_wireless_uplink_is_mesh(self, mock_mesh_child_ap):
        assert is_mesh_child(mock_mesh_child_ap) is True

    def test_wired_uplink_not_mesh(self, mock_wired_ap):
        assert is_mesh_child(mock_wired_ap) is False

    def test_no_uplink_not_mesh(self):
        device = {"name": "NoUplink", "uplink": {}}
        assert is_mesh_child(device) is False


class TestIsMeshParent:

    def test_ap_with_mesh_child_is_parent(self, mock_wired_ap, mock_all_devices):
        # mock_mesh_child_ap has uplink_remote_mac pointing to mock_wired_ap
        assert is_mesh_parent(mock_wired_ap, mock_all_devices) is True

    def test_ap_without_children_not_parent(self, mock_6ghz_ap, mock_all_devices):
        assert is_mesh_parent(mock_6ghz_ap, mock_all_devices) is False

    def test_mesh_child_not_parent(self, mock_mesh_child_ap, mock_all_devices):
        # The mesh child itself is not a parent (no other AP points to it)
        assert is_mesh_parent(mock_mesh_child_ap, mock_all_devices) is False


class TestGetMeshRole:

    def test_mesh_child_role(self, mock_mesh_child_ap, mock_all_devices):
        role = get_mesh_role(mock_mesh_child_ap, mock_all_devices)
        assert "mesh_child" in role

    def test_mesh_parent_role(self, mock_wired_ap, mock_all_devices):
        role = get_mesh_role(mock_wired_ap, mock_all_devices)
        assert "mesh_parent" in role

    def test_no_mesh_role(self, mock_6ghz_ap, mock_all_devices):
        role = get_mesh_role(mock_6ghz_ap, mock_all_devices)
        assert role is None


class TestApDisplayName:

    def test_name_preferred(self):
        device = {"name": "My AP", "hostname": "uap-pro", "mac": "aa:bb:cc:dd:ee:ff"}
        assert ap_display_name(device) == "My AP"

    def test_hostname_fallback(self):
        device = {"hostname": "uap-pro", "mac": "aa:bb:cc:dd:ee:ff"}
        assert ap_display_name(device) == "uap-pro"

    def test_mac_fallback(self):
        device = {"mac": "aa:bb:cc:dd:ee:ff"}
        assert ap_display_name(device) == "aa:bb:cc:dd:ee:ff"
