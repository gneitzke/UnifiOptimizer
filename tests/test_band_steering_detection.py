#!/usr/bin/env python3
"""
Test Band Steering Configuration Reading
Verifies that band steering settings are properly detected on dual-band and tri-band APs
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.advanced_analyzer import AdvancedNetworkAnalyzer


def test_band_steering_detection():
    """Test that band steering settings are properly detected"""
    print("\n" + "=" * 70)
    print("Testing Band Steering Detection")
    print("=" * 70 + "\n")

    analyzer = AdvancedNetworkAnalyzer(client=None, site="default")

    # Test Case 1: Dual-band AP with band steering ON
    print("Test 1: Dual-band AP with band steering enabled")
    devices_dual_on = [
        {
            "type": "uap",
            "name": "Dual-Band AP (ON)",
            "_id": "ap1",
            "mac": "aa:bb:cc:dd:ee:01",
            "bandsteering_mode": "prefer_5g",
            "radio_table": [
                {"radio": "ng", "channel": 6},
                {"radio": "na", "channel": 36},
            ],
        }
    ]

    result = analyzer.analyze_band_steering(devices_dual_on, [])
    assert "Dual-Band AP (ON)" in result["band_steering_enabled"]
    assert result["band_steering_enabled"]["Dual-Band AP (ON)"] == True
    print("  ✓ Dual-band AP with 'prefer_5g' detected as enabled")

    # Test Case 2: Dual-band AP with band steering OFF
    print("\nTest 2: Dual-band AP with band steering disabled")
    devices_dual_off = [
        {
            "type": "uap",
            "name": "Dual-Band AP (OFF)",
            "_id": "ap2",
            "mac": "aa:bb:cc:dd:ee:02",
            "bandsteering_mode": "off",
            "radio_table": [
                {"radio": "ng", "channel": 1},
                {"radio": "na", "channel": 149},
            ],
        }
    ]

    result = analyzer.analyze_band_steering(devices_dual_off, [])
    assert "Dual-Band AP (OFF)" in result["band_steering_enabled"]
    assert result["band_steering_enabled"]["Dual-Band AP (OFF)"] == False
    print("  ✓ Dual-band AP with 'off' detected as disabled")

    # Test Case 3: Tri-band AP (with 6GHz) with band steering ON
    print("\nTest 3: Tri-band AP with band steering enabled")
    devices_tri_on = [
        {
            "type": "uap",
            "name": "Tri-Band AP (ON)",
            "_id": "ap3",
            "mac": "aa:bb:cc:dd:ee:03",
            "bandsteering_mode": "prefer_5g",
            "radio_table": [
                {"radio": "ng", "channel": 6},
                {"radio": "na", "channel": 36},
                {"radio": "6e", "channel": 37},  # 6GHz radio
            ],
        }
    ]

    result = analyzer.analyze_band_steering(devices_tri_on, [])
    assert "Tri-Band AP (ON)" in result["band_steering_enabled"]
    assert result["band_steering_enabled"]["Tri-Band AP (ON)"] == True
    print("  ✓ Tri-band AP with 'prefer_5g' detected as enabled")

    # Test Case 4: Tri-band AP with band steering OFF
    print("\nTest 4: Tri-band AP with band steering disabled")
    devices_tri_off = [
        {
            "type": "uap",
            "name": "Tri-Band AP (OFF)",
            "_id": "ap4",
            "mac": "aa:bb:cc:dd:ee:04",
            "bandsteering_mode": "off",
            "radio_table": [
                {"radio": "ng", "channel": 11},
                {"radio": "na", "channel": 44},
                {"radio": "6e", "channel": 53},  # 6GHz radio
            ],
        }
    ]

    result = analyzer.analyze_band_steering(devices_tri_off, [])
    assert "Tri-Band AP (OFF)" in result["band_steering_enabled"]
    assert result["band_steering_enabled"]["Tri-Band AP (OFF)"] == False
    print("  ✓ Tri-band AP with 'off' detected as disabled")

    # Test Case 5: Mixed APs
    print("\nTest 5: Mixed dual-band and tri-band APs")
    devices_mixed = [
        {
            "type": "uap",
            "name": "Dual ON",
            "_id": "ap5",
            "mac": "aa:bb:cc:dd:ee:05",
            "bandsteering_mode": "prefer_5g",
            "radio_table": [
                {"radio": "ng", "channel": 6},
                {"radio": "na", "channel": 36},
            ],
        },
        {
            "type": "uap",
            "name": "Tri ON",
            "_id": "ap6",
            "mac": "aa:bb:cc:dd:ee:06",
            "bandsteering_mode": "equal",  # Another valid mode
            "radio_table": [
                {"radio": "ng", "channel": 1},
                {"radio": "na", "channel": 149},
                {"radio": "6e", "channel": 37},
            ],
        },
        {
            "type": "uap",
            "name": "Dual OFF",
            "_id": "ap7",
            "mac": "aa:bb:cc:dd:ee:07",
            "bandsteering_mode": "off",
            "radio_table": [
                {"radio": "ng", "channel": 11},
                {"radio": "na", "channel": 44},
            ],
        },
    ]

    result = analyzer.analyze_band_steering(devices_mixed, [])
    assert result["band_steering_enabled"]["Dual ON"] == True
    assert result["band_steering_enabled"]["Tri ON"] == True
    assert result["band_steering_enabled"]["Dual OFF"] == False
    enabled_count = sum(1 for v in result["band_steering_enabled"].values() if v)
    assert enabled_count == 2
    print("  ✓ Mixed APs correctly counted: 2 enabled, 1 disabled")

    # Test Case 6: Check for different band steering mode values
    print("\nTest 6: Different band steering mode values")
    modes_to_test = ["prefer_5g", "equal", "prefer_2g"]
    for mode in modes_to_test:
        devices = [
            {
                "type": "uap",
                "name": f"AP with {mode}",
                "_id": f"ap_{mode}",
                "mac": f"aa:bb:cc:dd:ee:{mode[:2]}",
                "bandsteering_mode": mode,
                "radio_table": [
                    {"radio": "ng", "channel": 6},
                    {"radio": "na", "channel": 36},
                ],
            }
        ]
        result = analyzer.analyze_band_steering(devices, [])
        is_enabled = result["band_steering_enabled"][f"AP with {mode}"]
        print(f"  ✓ Mode '{mode}' detected as {'enabled' if is_enabled else 'disabled'}")

    print("\n" + "=" * 70)
    print("✅ All band steering detection tests passed!")
    print("=" * 70 + "\n")
    return True


if __name__ == "__main__":
    try:
        test_band_steering_detection()
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)
