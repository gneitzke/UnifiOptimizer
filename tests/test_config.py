"""Tests for utils/config.py — configuration loading and access."""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.config import get_config, get_option, get_threshold


class TestGetThreshold:
    """Test dot-notation threshold access."""

    def test_rssi_excellent(self):
        val = get_threshold("rssi.excellent")
        assert isinstance(val, (int, float))
        assert val < 0  # RSSI values are negative dBm

    def test_rssi_poor(self):
        val = get_threshold("rssi.poor")
        assert val < get_threshold("rssi.fair")

    def test_mesh_threshold(self):
        val = get_threshold("mesh.uplink_critical_dbm")
        assert isinstance(val, (int, float))
        assert val < 0

    def test_missing_key_returns_none(self):
        val = get_threshold("nonexistent.key")
        assert val is None

    def test_partial_path(self):
        val = get_threshold("rssi")
        assert isinstance(val, dict)


class TestGetOption:
    """Test option value access."""

    def test_lookback_days_default(self):
        val = get_option("lookback_days")
        assert isinstance(val, int)
        assert val > 0

    def test_generate_html_report(self):
        val = get_option("generate_html_report")
        assert isinstance(val, bool)

    def test_missing_option_returns_none(self):
        val = get_option("totally_fake_option")
        assert val is None


class TestGetConfig:
    """Test full config loading."""

    def test_returns_dict(self):
        cfg = get_config()
        assert isinstance(cfg, dict)

    def test_has_thresholds(self):
        cfg = get_config()
        assert "thresholds" in cfg

    def test_has_options(self):
        cfg = get_config()
        assert "options" in cfg

    def test_singleton_returns_same_object(self):
        """Config should be loaded once and cached."""
        a = get_config()
        b = get_config()
        assert a is b
