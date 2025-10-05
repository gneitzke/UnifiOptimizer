#!/usr/bin/env python3
"""
Test Enhanced Restart Detection
Verifies intelligent restart analysis:
- Manual restarts are OK (not flagged)
- Single unplanned restarts are low/medium priority
- Cyclic/repeated restarts are high priority
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import Mock
from core.network_health_analyzer import NetworkHealthAnalyzer


def test_manual_restart():
    """Test that manual/upgrade restarts are not flagged as issues"""
    print("\n" + "=" * 70)
    print("Test 1: Manual Restart (Should NOT be flagged)")
    print("=" * 70)

    # Create mock client
    client = Mock()

    # Mock event log showing a manual upgrade restart
    client.get.return_value = {
        "data": [
            {
                "time": 1728000000,
                "msg": "Device AP-Office was restarted by user admin for firmware upgrade",
                "key": "EVT_AP_RestartedUser",
                "device": "aa:bb:cc:dd:ee:ff",
                "ap": "aa:bb:cc:dd:ee:ff",
            }
        ]
    }

    analyzer = NetworkHealthAnalyzer(client, site="default")

    # Device that restarted 12 hours ago (manual upgrade)
    devices = [
        {
            "name": "AP-Office",
            "type": "uap",
            "mac": "aa:bb:cc:dd:ee:ff",
            "uptime": 43200,  # 12 hours
        }
    ]

    result = analyzer._analyze_device_stability(devices)

    # Verify
    assert result["status"] == "healthy", "Status should be healthy for manual restart"
    assert len(result["issues"]) == 0, "Manual restarts should not create issues"
    assert len(result["devices"]["stable"]) == 1, "Device should be considered stable"

    print("✅ Manual restart correctly ignored (no issue created)")
    print(f"   Status: {result['status']}")
    print(f"   Issues: {len(result['issues'])}")
    print(f"   Stable devices: {len(result['devices']['stable'])}")
    return True


def test_single_unplanned_restart():
    """Test that single unplanned restart gets medium priority"""
    print("\n" + "=" * 70)
    print("Test 2: Single Unplanned Restart (Should be MEDIUM priority)")
    print("=" * 70)

    client = Mock()

    # Mock event log showing a single restart without manual keywords
    client.get.return_value = {
        "data": [
            {
                "time": 1728000000,
                "msg": "Device AP-Office disconnected",
                "key": "EVT_AP_Disconnected",
                "device": "aa:bb:cc:dd:ee:ff",
            }
        ]
    }

    analyzer = NetworkHealthAnalyzer(client, site="default")

    # Device that restarted 12 hours ago (unplanned)
    devices = [
        {
            "name": "AP-Office",
            "type": "uap",
            "mac": "aa:bb:cc:dd:ee:ff",
            "uptime": 43200,  # 12 hours
        }
    ]

    result = analyzer._analyze_device_stability(devices)

    # Verify
    assert result["status"] in ["warning", "healthy"], "Status should be warning for single restart"
    assert len(result["issues"]) == 1, "Should have one issue for unplanned restart"
    assert result["issues"][0]["severity"] == "medium", "Single restart should be medium severity"
    assert "single occurrence" in result["issues"][0]["message"].lower(), "Should note it's a single occurrence"

    print("✅ Single unplanned restart correctly flagged as medium priority")
    print(f"   Status: {result['status']}")
    print(f"   Severity: {result['issues'][0]['severity']}")
    print(f"   Message: {result['issues'][0]['message']}")
    return True


def test_cyclic_restarts():
    """Test that multiple restarts get high priority"""
    print("\n" + "=" * 70)
    print("Test 3: Cyclic Restarts (Should be HIGH priority)")
    print("=" * 70)

    client = Mock()

    # Mock event log showing multiple restarts
    client.get.return_value = {
        "data": [
            {
                "time": 1728000000,
                "msg": "Device AP-Office disconnected",
                "key": "EVT_AP_Disconnected",
                "device": "aa:bb:cc:dd:ee:ff",
            },
            {
                "time": 1728100000,
                "msg": "Device AP-Office rebooted",
                "key": "EVT_AP_Rebooted",
                "device": "aa:bb:cc:dd:ee:ff",
            },
            {
                "time": 1728200000,
                "msg": "Device AP-Office restarted",
                "key": "EVT_AP_Restarted",
                "device": "aa:bb:cc:dd:ee:ff",
            },
        ]
    }

    analyzer = NetworkHealthAnalyzer(client, site="default")

    # Device that restarted 12 hours ago (but has history of restarts)
    devices = [
        {
            "name": "AP-Office",
            "type": "uap",
            "mac": "aa:bb:cc:dd:ee:ff",
            "uptime": 43200,  # 12 hours
        }
    ]

    result = analyzer._analyze_device_stability(devices)

    # Verify
    assert result["status"] == "critical", "Status should be critical for cyclic restarts"
    assert len(result["issues"]) == 1, "Should have one issue for cyclic restart"
    assert result["issues"][0]["severity"] == "high", "Cyclic restarts should be high severity"
    assert "cyclic" in result["issues"][0]["message"].lower(), "Should identify as cyclic behavior"
    assert result["issues"][0]["restart_count"] == 3, "Should count all 3 restarts"
    assert len(result["recommendations"]) > 0, "Should have urgent recommendations"

    print("✅ Cyclic restarts correctly flagged as high priority")
    print(f"   Status: {result['status']}")
    print(f"   Severity: {result['issues'][0]['severity']}")
    print(f"   Restart count: {result['issues'][0]['restart_count']}")
    print(f"   Message: {result['issues'][0]['message']}")
    print(f"   Recommendations: {len(result['recommendations'])}")
    return True


def test_old_restart_no_flag():
    """Test that restarts over 7 days ago are ignored"""
    print("\n" + "=" * 70)
    print("Test 4: Old Restart (Should NOT be flagged)")
    print("=" * 70)

    client = Mock()

    # Mock empty event log (no recent events)
    client.get.return_value = {"data": []}

    analyzer = NetworkHealthAnalyzer(client, site="default")

    # Device that restarted 8 days ago
    devices = [
        {
            "name": "AP-Office",
            "type": "uap",
            "mac": "aa:bb:cc:dd:ee:ff",
            "uptime": 691200,  # 8 days
        }
    ]

    result = analyzer._analyze_device_stability(devices)

    # Verify
    assert result["status"] == "healthy", "Status should be healthy for old restart"
    assert len(result["issues"]) == 0, "Old restarts should not create issues"
    assert len(result["devices"]["stable"]) == 1, "Device should be considered stable"

    print("✅ Old restart correctly ignored")
    print(f"   Status: {result['status']}")
    print(f"   Issues: {len(result['issues'])}")
    print(f"   Stable devices: {len(result['devices']['stable'])}")
    return True


def test_mixed_devices():
    """Test analysis with multiple devices in different states"""
    print("\n" + "=" * 70)
    print("Test 5: Mixed Device States")
    print("=" * 70)

    client = Mock()

    # Mock event log with events for different devices
    client.get.return_value = {
        "data": [
            # AP1: Manual restart
            {
                "time": 1728000000,
                "msg": "Device AP1 restarted by user admin for configuration change",
                "key": "EVT_AP_RestartedUser",
                "device": "aa:aa:aa:aa:aa:aa",
            },
            # AP2: Multiple unplanned restarts
            {
                "time": 1728000000,
                "msg": "Device AP2 disconnected",
                "key": "EVT_AP_Disconnected",
                "device": "bb:bb:bb:bb:bb:bb",
            },
            {
                "time": 1728100000,
                "msg": "Device AP2 rebooted",
                "key": "EVT_AP_Rebooted",
                "device": "bb:bb:bb:bb:bb:bb",
            },
            {
                "time": 1728200000,
                "msg": "Device AP2 powered off",
                "key": "EVT_AP_PoweredOff",
                "device": "bb:bb:bb:bb:bb:bb",
            },
        ]
    }

    analyzer = NetworkHealthAnalyzer(client, site="default")

    devices = [
        {
            "name": "AP1",
            "type": "uap",
            "mac": "aa:aa:aa:aa:aa:aa",
            "uptime": 43200,  # 12 hours - manual restart
        },
        {
            "name": "AP2",
            "type": "uap",
            "mac": "bb:bb:bb:bb:bb:bb",
            "uptime": 21600,  # 6 hours - cyclic restarts
        },
        {
            "name": "AP3",
            "type": "uap",
            "mac": "cc:cc:cc:cc:cc:cc",
            "uptime": 2592000,  # 30 days - stable
        },
    ]

    result = analyzer._analyze_device_stability(devices)

    # Verify
    assert result["status"] == "critical", "Should be critical due to AP2"
    assert len(result["issues"]) == 1, "Should have issue for AP2 only"
    assert result["issues"][0]["device"] == "AP2", "Issue should be for AP2"
    assert result["issues"][0]["severity"] == "high", "AP2 should be high severity"
    assert len(result["devices"]["stable"]) == 2, "AP1 (manual) and AP3 should be stable"
    assert len(result["devices"]["cyclic_restart"]) == 1, "AP2 should be cyclic"

    print("✅ Mixed device states correctly analyzed")
    print(f"   Status: {result['status']}")
    print(f"   Issues: {len(result['issues'])}")
    print(f"   Stable devices: {len(result['devices']['stable'])} (AP1, AP3)")
    print(f"   Cyclic restart devices: {len(result['devices']['cyclic_restart'])} (AP2)")
    print(f"   Issue device: {result['issues'][0]['device']} - {result['issues'][0]['message']}")
    return True


def run_all_tests():
    """Run all restart detection tests"""
    print("\n" + "=" * 70)
    print("ENHANCED RESTART DETECTION TEST SUITE")
    print("=" * 70)

    tests = [
        ("Manual Restart", test_manual_restart),
        ("Single Unplanned Restart", test_single_unplanned_restart),
        ("Cyclic Restarts", test_cyclic_restarts),
        ("Old Restart", test_old_restart_no_flag),
        ("Mixed Devices", test_mixed_devices),
    ]

    passed = 0
    failed = 0

    for test_name, test_func in tests:
        try:
            if test_func():
                passed += 1
        except AssertionError as e:
            failed += 1
            print(f"\n❌ {test_name} FAILED: {e}")
        except Exception as e:
            failed += 1
            print(f"\n❌ {test_name} ERROR: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 70)
    print("TEST RESULTS")
    print("=" * 70)
    print(f"✅ Passed: {passed}/{len(tests)}")
    print(f"❌ Failed: {failed}/{len(tests)}")
    print("=" * 70)

    return failed == 0


if __name__ == "__main__":
    try:
        success = run_all_tests()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Test suite error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
