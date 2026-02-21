#!/usr/bin/env python3
"""
Unit tests for core/ai_advisor.py

These tests cover pure-logic functions that require no controller connection,
no AI backend, and no API keys.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.ai_advisor import _build_context, ask


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_ANALYSIS = {
    "health_score": {"score": 87, "grade": "B", "status": "Good"},
    "health_analysis": {
        "component_scores": {"rf_health": 90, "client_health": 85},
        "issues": [
            {
                "severity": "medium",
                "message": "2.4GHz using auto channel",
                "recommendation": "Set to channel 1, 6, or 11",
            }
        ],
    },
    "ap_analysis": {
        "ap_details": [
            {
                "name": "U7 Pro - Kitchen",
                "model": "U7PRO",
                "is_mesh": False,
                "client_count": 12,
                "radios": {
                    "2.4GHz": {"channel": 6, "width": 20},
                    "5GHz": {"channel": 36, "width": 80},
                },
            }
        ]
    },
    "client_findings": [
        {
            "client": "iPhone",
            "mac": "aa:bb:cc:dd:ee:ff",
            "ap": "U7 Pro - Kitchen",
            "type": "wrong_band",
            "severity": "medium",
            "message": "iPhone on 2.4GHz but supports ax",
            "rssi": -65,
        }
    ],
    "recommendations": [
        {
            "device": {"name": "U7 Pro - Kitchen"},
            "action": "min_rssi_disabled",
            "reason": "Min RSSI disabled on 5GHz",
            "priority": "medium",
            "band": "5GHz",
        }
    ],
    "dfs_analysis": {
        "events_by_ap": {"U7 Pro - Kitchen": ["event1", "event2"]},
    },
    "band_steering_analysis": {"summary": "Band steering disabled on 2 APs"},
}


# ---------------------------------------------------------------------------
# _build_context tests
# ---------------------------------------------------------------------------


class TestBuildContext(unittest.TestCase):
    def test_contains_health_score(self):
        ctx = _build_context(MINIMAL_ANALYSIS, [])
        self.assertIn("87/100", ctx)
        self.assertIn("Grade B", ctx)

    def test_contains_ap_name(self):
        ctx = _build_context(MINIMAL_ANALYSIS, [])
        self.assertIn("U7 Pro - Kitchen", ctx)

    def test_contains_client_finding(self):
        ctx = _build_context(MINIMAL_ANALYSIS, [])
        self.assertIn("iPhone on 2.4GHz", ctx)
        self.assertIn("-65", ctx)

    def test_contains_network_issue(self):
        ctx = _build_context(MINIMAL_ANALYSIS, [])
        self.assertIn("2.4GHz using auto channel", ctx)
        self.assertIn("Set to channel 1, 6, or 11", ctx)

    def test_contains_recommendation(self):
        ctx = _build_context(MINIMAL_ANALYSIS, [])
        self.assertIn("min_rssi_disabled", ctx)
        self.assertIn("Min RSSI disabled on 5GHz", ctx)

    def test_contains_dfs_events(self):
        ctx = _build_context(MINIMAL_ANALYSIS, [])
        self.assertIn("DFS RADAR EVENTS", ctx)
        self.assertIn("2 event(s)", ctx)

    def test_contains_band_steering(self):
        ctx = _build_context(MINIMAL_ANALYSIS, [])
        self.assertIn("Band steering disabled on 2 APs", ctx)

    def test_empty_analysis_does_not_crash(self):
        """_build_context must never raise on missing/empty data."""
        ctx = _build_context({}, [])
        self.assertIsInstance(ctx, str)

    def test_mesh_ap_tagged(self):
        analysis = {
            "ap_analysis": {
                "ap_details": [
                    {
                        "name": "Mesh Node",
                        "model": "U6MESH",
                        "is_mesh": True,
                        "client_count": 3,
                        "radios": {},
                    }
                ]
            }
        }
        ctx = _build_context(analysis, [])
        self.assertIn("[MESH]", ctx)

    def test_no_raw_json_blobs(self):
        """Context should not contain raw JSON device blobs from recommendations."""
        ctx = _build_context(MINIMAL_ANALYSIS, [])
        # The full device config (port_table, required_version, etc.) must not appear
        self.assertNotIn("port_table", ctx)
        self.assertNotIn("required_version", ctx)

    def test_returns_string(self):
        ctx = _build_context(MINIMAL_ANALYSIS, [])
        self.assertIsInstance(ctx, str)
        self.assertGreater(len(ctx), 0)


# ---------------------------------------------------------------------------
# ask() error path tests (no backend required)
# ---------------------------------------------------------------------------


class TestAskErrorPaths(unittest.TestCase):
    def test_raises_on_unknown_backend(self):
        with self.assertRaises(ValueError) as cm:
            ask("test question", analysis_data={}, backend="nonsense")
        self.assertIn("nonsense", str(cm.exception))

    def test_raises_runtime_error_on_missing_cache(self):
        """ask() with no data and no cache files should raise RuntimeError."""
        import tempfile
        import os

        # Run from a temp dir guaranteed to have no cache files
        original_dir = os.getcwd()
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                os.chdir(tmpdir)
                with self.assertRaises(RuntimeError) as cm:
                    ask("test question")
                self.assertIn("No analysis cache found", str(cm.exception))
        finally:
            os.chdir(original_dir)


# ---------------------------------------------------------------------------
# generate_summary() tests
# ---------------------------------------------------------------------------


class TestGenerateSummary(unittest.TestCase):
    def test_returns_none_when_disabled(self):
        """generate_summary returns None by default (summary_in_report: false)."""
        from core.ai_advisor import generate_summary

        result = generate_summary(MINIMAL_ANALYSIS, [])
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
