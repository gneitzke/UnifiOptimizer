#!/usr/bin/env python3
"""
Test Airtime Analysis 6GHz Support
Verifies that airtime utilization correctly displays 6GHz band status
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.advanced_analyzer import AdvancedNetworkAnalyzer
from unittest.mock import Mock


def test_airtime_6ghz_band_detection():
    """Test that airtime analysis correctly identifies 6GHz band"""
    print("\n" + "="*70)
    print("Testing 6GHz Band Detection in Airtime Analysis")
    print("="*70 + "\n")

    # Create mock client
    mock_client = Mock()
    analyzer = AdvancedNetworkAnalyzer(mock_client, site="default")

    # Mock devices with 2.4GHz, 5GHz, and 6GHz radios
    devices = [
        {
            "type": "uap",
            "name": "Living Room AP",
            "_id": "ap1",
            "mac": "aa:bb:cc:dd:ee:01",
            "radio_table_stats": [
                {
                    "name": "ra0",
                    "radio": "ng",
                    "cu_total": 25,
                    "cu_self_tx": 10,
                    "cu_self_rx": 5,
                    "num_sta": 8
                },
                {
                    "name": "ra1",
                    "radio": "na",
                    "cu_total": 45,
                    "cu_self_tx": 20,
                    "cu_self_rx": 10,
                    "num_sta": 12
                },
                {
                    "name": "ra2",
                    "radio": "6e",  # 6GHz radio
                    "cu_total": 15,
                    "cu_self_tx": 8,
                    "cu_self_rx": 3,
                    "num_sta": 5
                }
            ]
        },
        {
            "type": "uap",
            "name": "Bedroom AP",
            "_id": "ap2",
            "mac": "aa:bb:cc:dd:ee:02",
            "radio_table_stats": [
                {
                    "name": "ra0",
                    "radio": "ng",
                    "cu_total": 60,
                    "cu_self_tx": 30,
                    "cu_self_rx": 15,
                    "num_sta": 15
                },
                {
                    "name": "ra1",
                    "radio": "na",
                    "cu_total": 75,  # Saturated
                    "cu_self_tx": 40,
                    "cu_self_rx": 20,
                    "num_sta": 20
                }
                # No 6GHz radio on this AP
            ]
        }
    ]

    # Run analysis
    results = analyzer.analyze_airtime_utilization(devices, lookback_hours=24)

    # Check results
    print("📊 Airtime Utilization Results:\n")

    ap_utilization = results.get("ap_utilization", {})
    print(f"Total AP/Band combinations tracked: {len(ap_utilization)}")

    for key, data in ap_utilization.items():
        print(f"  • {key}")
        print(f"    Airtime: {data['airtime_pct']:.1f}%")
        print(f"    Clients: {data['clients']}")

    print(f"\nSaturated APs: {len(results.get('saturated_aps', []))}")
    print(f"Severity: {results.get('severity', 'ok')}")

    print("\n✅ Validation Checks:")

    # Check that 6GHz band is detected
    has_6ghz = any("6GHz" in key for key in ap_utilization.keys())
    assert has_6ghz, "6GHz band not detected in airtime analysis"
    print("  ✓ 6GHz band correctly detected")

    # Check specific AP bands
    expected_keys = [
        "Living Room AP (2.4GHz)",
        "Living Room AP (5GHz)",
        "Living Room AP (6GHz)",
        "Bedroom AP (2.4GHz)",
        "Bedroom AP (5GHz)"
    ]

    for key in expected_keys:
        assert key in ap_utilization, f"Expected key '{key}' not found"
    print(f"  ✓ All {len(expected_keys)} expected AP/Band combinations found")

    # Verify 6GHz specific data
    living_room_6ghz = ap_utilization.get("Living Room AP (6GHz)")
    assert living_room_6ghz is not None, "Living Room 6GHz data not found"
    assert living_room_6ghz['airtime_pct'] == 15, "6GHz airtime incorrect"
    assert living_room_6ghz['clients'] == 5, "6GHz client count incorrect"
    print("  ✓ 6GHz airtime data is accurate")

    # Verify no 6GHz for Bedroom AP (doesn't have 6GHz radio)
    has_bedroom_6ghz = "Bedroom AP (6GHz)" in ap_utilization
    assert not has_bedroom_6ghz, "Bedroom AP should not have 6GHz (no radio)"
    print("  ✓ APs without 6GHz radios correctly excluded")

    # Check saturation detection works for all bands
    saturated = results.get("saturated_aps", [])
    saturated_5ghz = any(ap['band'] == '5GHz' for ap in saturated)
    assert saturated_5ghz, "Saturated 5GHz AP not detected"
    print("  ✓ Saturation detection works across all bands")

    print("\n" + "="*70)
    print("✅ All Airtime 6GHz Tests PASSED!")
    print("="*70 + "\n")

    return True


if __name__ == "__main__":
    try:
        test_airtime_6ghz_band_detection()
        print("\n🎉 6GHz AIRTIME SUPPORT VERIFIED!\n")
        print("Current Status in HTML reports will now show:")
        print("  • Living Room AP (2.4GHz)")
        print("  • Living Room AP (5GHz)")
        print("  • Living Room AP (6GHz)  ← NEW!")
        print()
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}\n")
        exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}\n")
        import traceback
        traceback.print_exc()
        exit(1)
