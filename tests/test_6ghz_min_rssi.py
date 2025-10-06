#!/usr/bin/env python3
"""
Test 6GHz Min RSSI Recommendations
Verifies that 6GHz gets appropriate min RSSI thresholds (gentler than 5GHz)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import Mock

from core.advanced_analyzer import AdvancedNetworkAnalyzer


def test_6ghz_min_rssi_thresholds():
    """Test that 6GHz gets gentler min RSSI thresholds than 5GHz"""
    print("\n" + "=" * 70)
    print("Testing 6GHz Min RSSI Recommendations")
    print("=" * 70 + "\n")

    mock_client = Mock()
    analyzer = AdvancedNetworkAnalyzer(mock_client, site="default")

    # Test Case 1: Standard thresholds (< 20% iOS)
    print("📊 Test Case 1: Standard Thresholds (No iOS devices)")
    print("=" * 70)

    devices = [
        {
            "type": "uap",
            "name": "U7 Pro - Living Room",
            "_id": "ap1",
            "mac": "aa:bb:cc:dd:ee:01",
            "uplink": {"type": "wire"},
            "radio_table": [
                {
                    "radio": "ng",
                    "min_rssi_enabled": False,
                    "min_rssi": None
                },
                {
                    "radio": "na",
                    "min_rssi_enabled": False,
                    "min_rssi": None
                },
                {
                    "radio": "6e",  # 6GHz
                    "min_rssi_enabled": False,
                    "min_rssi": None
                }
            ]
        }
    ]

    # No iOS devices (0% iOS)
    clients = [
        {"hostname": "android-phone", "oui": "samsung"},
        {"hostname": "windows-laptop", "oui": "dell"},
    ]

    result = analyzer.analyze_min_rssi(devices, clients)

    print(f"iOS Percentage: {result['ios_percentage']:.1f}%")
    print(f"Threshold Type: {result['threshold_type']}")
    print(f"Total Radios: {result['total_radios']}")
    print(f"Disabled: {result['disabled_count']}")

    # Get recommendations for each band
    recommendations = result.get("recommendations", [])
    band_recommendations = {}
    for rec in recommendations:
        if rec.get("type") == "min_rssi_disabled":
            band = rec.get("band")
            recommended_value = rec.get("recommended_value")
            band_recommendations[band] = recommended_value
            print(f"  {band}: {recommended_value} dBm (recommended)")

    # Assertions for standard thresholds
    assert result["threshold_type"] == "standard", "Should use standard thresholds"
    assert band_recommendations.get("2.4GHz") == -75, "2.4GHz should be -75 dBm"
    assert band_recommendations.get("5GHz") == -72, "5GHz should be -72 dBm"
    assert band_recommendations.get("6GHz") == -70, "6GHz should be -70 dBm (gentler than 5GHz)"

    print("\n✅ Standard thresholds correct:")
    print("   • 2.4GHz: -75 dBm")
    print("   • 5GHz:   -72 dBm")
    print("   • 6GHz:   -70 dBm (gentler due to poor propagation)")

    # Test Case 2: iOS-friendly thresholds (≥ 20% iOS)
    print("\n📊 Test Case 2: iOS-Friendly Thresholds (50% iOS devices)")
    print("=" * 70)

    clients_ios_heavy = [
        {"hostname": "iPhone-15", "oui": "apple"},
        {"hostname": "iPhone-14", "oui": "apple"},
        {"hostname": "iPad-Pro", "oui": "apple"},
        {"hostname": "iPad-Air", "oui": "apple"},
        {"hostname": "MacBook-Pro", "oui": "apple"},
        {"hostname": "android-phone", "oui": "samsung"},
        {"hostname": "android-tablet", "oui": "samsung"},
        {"hostname": "windows-laptop", "oui": "dell"},
    ]

    result_ios = analyzer.analyze_min_rssi(devices, clients_ios_heavy)

    print(f"iOS Percentage: {result_ios['ios_percentage']:.1f}%")
    print(f"Threshold Type: {result_ios['threshold_type']}")

    # Get iOS-friendly recommendations
    recommendations_ios = result_ios.get("recommendations", [])
    band_recommendations_ios = {}
    for rec in recommendations_ios:
        if rec.get("type") == "min_rssi_disabled":
            band = rec.get("band")
            recommended_value = rec.get("recommended_value")
            band_recommendations_ios[band] = recommended_value
            print(f"  {band}: {recommended_value} dBm (iOS-friendly)")

    # Assertions for iOS-friendly thresholds
    assert result_ios["threshold_type"] == "iOS-friendly", "Should use iOS-friendly thresholds"
    assert result_ios["ios_percentage"] >= 20, "Should have ≥20% iOS"
    assert band_recommendations_ios.get("2.4GHz") == -78, "2.4GHz should be -78 dBm (iOS-friendly)"
    assert band_recommendations_ios.get("5GHz") == -75, "5GHz should be -75 dBm (iOS-friendly)"
    assert band_recommendations_ios.get("6GHz") == -72, "6GHz should be -72 dBm (iOS-friendly, gentler)"

    print("\n✅ iOS-friendly thresholds correct:")
    print("   • 2.4GHz: -78 dBm (relaxed for iOS)")
    print("   • 5GHz:   -75 dBm (relaxed for iOS)")
    print("   • 6GHz:   -72 dBm (relaxed + poor propagation)")

    # Test Case 3: 6GHz with suboptimal value (too aggressive)
    print("\n📊 Test Case 3: 6GHz with Too-Aggressive Min RSSI")
    print("=" * 70)

    devices_aggressive = [
        {
            "type": "uap",
            "name": "U7 Pro - Bedroom",
            "_id": "ap2",
            "mac": "aa:bb:cc:dd:ee:02",
            "uplink": {"type": "wire"},
            "radio_table": [
                {
                    "radio": "6e",  # 6GHz
                    "min_rssi_enabled": True,
                    "min_rssi": -81  # Too weak! (should be -70, difference >10 dBm)
                }
            ]
        }
    ]

    result_aggressive = analyzer.analyze_min_rssi(devices_aggressive, clients)

    # Find 6GHz suboptimal recommendation
    suboptimal_recs = [r for r in result_aggressive["recommendations"]
                       if r.get("type") == "min_rssi_suboptimal" and r.get("band") == "6GHz"]

    assert len(suboptimal_recs) > 0, "Should flag -81 dBm as suboptimal for 6GHz"

    rec = suboptimal_recs[0]
    print(f"Current Value: {rec['current_value']} dBm")
    print(f"Recommended: {rec['recommended_value']} dBm")
    print(f"Message: {rec['message']}")
    print(f"Recommendation: {rec['recommendation']}")

    assert rec["current_value"] == -81, "Current should be -81"
    assert rec["recommended_value"] == -70, "Should recommend -70 for 6GHz"

    print("\n✅ Correctly flags -81 dBm as too weak/suboptimal")
    print("   Recommends -70 dBm for optimal 6GHz roaming")

    # Test Case 4: 6GHz gentler than 5GHz validation
    print("\n📊 Test Case 4: Verify 6GHz is Gentler than 5GHz")
    print("=" * 70)

    # Standard thresholds
    assert band_recommendations["6GHz"] > band_recommendations["5GHz"], \
        "6GHz threshold should be LESS negative (gentler) than 5GHz"

    # iOS-friendly thresholds
    assert band_recommendations_ios["6GHz"] > band_recommendations_ios["5GHz"], \
        "iOS-friendly 6GHz threshold should be LESS negative (gentler) than 5GHz"

    print("Standard:")
    print(f"  5GHz: {band_recommendations['5GHz']} dBm")
    print(f"  6GHz: {band_recommendations['6GHz']} dBm (gentler by {band_recommendations['6GHz'] - band_recommendations['5GHz']} dBm)")

    print("\nIOS-Friendly:")
    print(f"  5GHz: {band_recommendations_ios['5GHz']} dBm")
    print(f"  6GHz: {band_recommendations_ios['6GHz']} dBm (gentler by {band_recommendations_ios['6GHz'] - band_recommendations_ios['5GHz']} dBm)")

    print("\n✅ 6GHz correctly uses gentler thresholds than 5GHz")
    print("   Accounts for worse propagation characteristics")

    print("\n" + "=" * 70)
    print("✅ ALL TESTS PASSED!")
    print("=" * 70 + "\n")

    print("🎯 KEY FINDINGS:")
    print("  • 6GHz min RSSI: -70 dBm standard, -72 dBm iOS-friendly")
    print("  • 6GHz thresholds 2 dBm gentler than 5GHz")
    print("  • Accounts for worse propagation (less wall penetration)")
    print("  • Prevents premature disconnects on 6GHz band")
    print()

    return True


if __name__ == "__main__":
    try:
        test_6ghz_min_rssi_thresholds()
        print("🎉 6GHz MIN RSSI SUPPORT WORKING!\n")
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}\n")
        exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}\n")
        import traceback
        traceback.print_exc()
        exit(1)
