#!/usr/bin/env python3
"""
Test iPhone-Aware Min RSSI Recommendations
Verifies that min RSSI thresholds adapt based on iOS device presence
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import Mock

from core.advanced_analyzer import AdvancedNetworkAnalyzer


def test_no_ios_devices():
    """Test standard thresholds when no iOS devices present"""
    print("\n" + "=" * 70)
    print("Test 1: No iOS Devices (Standard Thresholds)")
    print("=" * 70)

    client = Mock()
    analyzer = AdvancedNetworkAnalyzer(client, site="default")

    # Mock devices with min RSSI disabled
    devices = [
        {
            "type": "uap",
            "name": "AP-Office",
            "_id": "abc123",
            "radio_table": [
                {"radio": "ng", "min_rssi_enabled": False},
                {"radio": "na", "min_rssi_enabled": False},
            ],
        }
    ]

    # Mock clients - all Android/Windows (no iOS)
    clients = [
        {"hostname": "android-phone", "name": "Samsung Galaxy", "oui": "samsung"},
        {"hostname": "windows-laptop", "name": "Dell Laptop", "oui": "dell"},
    ]

    result = analyzer.analyze_min_rssi(devices, clients)

    # Verify
    assert not result["ios_devices_detected"], "Should not detect iOS devices"
    assert result["ios_device_count"] == 0, "iOS count should be 0"
    assert result["threshold_type"] == "standard", "Should use standard thresholds"
    assert len(result["recommendations"]) == 2, "Should have 2 recommendations (2 radios)"

    # Check recommended values are standard
    rec_24ghz = next(r for r in result["recommendations"] if r["radio"] == "ng")
    rec_5ghz = next(r for r in result["recommendations"] if r["radio"] == "na")

    assert rec_24ghz["recommended_value"] == -75, "2.4GHz should be -75 (standard)"
    assert rec_5ghz["recommended_value"] == -72, "5GHz should be -72 (standard)"

    print("✅ Standard thresholds used with no iOS devices")
    print(f"   iOS detected: {result['ios_devices_detected']}")
    print(f"   Threshold type: {result['threshold_type']}")
    print(f"   2.4GHz recommendation: {rec_24ghz['recommended_value']} dBm")
    print(f"   5GHz recommendation: {rec_5ghz['recommended_value']} dBm")
    return True


def test_few_ios_devices():
    """Test standard thresholds when <20% iOS devices"""
    print("\n" + "=" * 70)
    print("Test 2: Few iOS Devices (<20% - Standard Thresholds)")
    print("=" * 70)

    client = Mock()
    analyzer = AdvancedNetworkAnalyzer(client, site="default")

    devices = [
        {
            "type": "uap",
            "name": "AP-Office",
            "_id": "abc123",
            "radio_table": [
                {"radio": "ng", "min_rssi_enabled": False},
                {"radio": "na", "min_rssi_enabled": False},
            ],
        }
    ]

    # Mock clients - 1 iOS out of 10 = 10%
    clients = [
        {"hostname": "johns-iphone", "name": "iPhone 15", "oui": "apple"},
    ] + [
        {"hostname": f"android-{i}", "name": f"Android {i}", "oui": "samsung"}
        for i in range(9)
    ]

    result = analyzer.analyze_min_rssi(devices, clients)

    # Verify
    assert result["ios_devices_detected"], "Should detect iOS device"
    assert result["ios_device_count"] == 1, "Should count 1 iOS device"
    assert result["ios_percentage"] == 10.0, "Should be 10%"
    assert result["threshold_type"] == "standard", "Should still use standard (< 20%)"

    rec_24ghz = next(r for r in result["recommendations"] if r["radio"] == "ng")
    rec_5ghz = next(r for r in result["recommendations"] if r["radio"] == "na")

    assert rec_24ghz["recommended_value"] == -75, "2.4GHz should be -75 (standard)"
    assert rec_5ghz["recommended_value"] == -72, "5GHz should be -72 (standard)"

    print("✅ Standard thresholds used with few iOS devices")
    print(f"   iOS detected: {result['ios_devices_detected']} ({result['ios_device_count']} devices)")
    print(f"   iOS percentage: {result['ios_percentage']:.1f}%")
    print(f"   Threshold type: {result['threshold_type']}")
    print(f"   2.4GHz recommendation: {rec_24ghz['recommended_value']} dBm")
    print(f"   5GHz recommendation: {rec_5ghz['recommended_value']} dBm")
    return True


def test_many_ios_devices():
    """Test iOS-friendly thresholds when >20% iOS devices"""
    print("\n" + "=" * 70)
    print("Test 3: Many iOS Devices (>20% - iOS-Friendly Thresholds)")
    print("=" * 70)

    client = Mock()
    analyzer = AdvancedNetworkAnalyzer(client, site="default")

    devices = [
        {
            "type": "uap",
            "name": "AP-Office",
            "_id": "abc123",
            "radio_table": [
                {"radio": "ng", "min_rssi_enabled": False},
                {"radio": "na", "min_rssi_enabled": False},
            ],
        }
    ]

    # Mock clients - 3 iOS out of 10 = 30%
    clients = [
        {"hostname": "johns-iphone", "name": "iPhone 15", "oui": "apple"},
        {"hostname": "janes-ipad", "name": "iPad Pro", "oui": "apple"},
        {"hostname": "bobs-iphone", "name": "iPhone 14", "oui": "apple"},
    ] + [
        {"hostname": f"android-{i}", "name": f"Android {i}", "oui": "samsung"}
        for i in range(7)
    ]

    result = analyzer.analyze_min_rssi(devices, clients)

    # Verify
    assert result["ios_devices_detected"], "Should detect iOS devices"
    assert result["ios_device_count"] == 3, "Should count 3 iOS devices"
    assert result["ios_percentage"] == 30.0, "Should be 30%"
    assert result["threshold_type"] == "iOS-friendly", "Should use iOS-friendly thresholds"

    rec_24ghz = next(r for r in result["recommendations"] if r["radio"] == "ng")
    rec_5ghz = next(r for r in result["recommendations"] if r["radio"] == "na")

    assert rec_24ghz["recommended_value"] == -78, "2.4GHz should be -78 (iOS-friendly)"
    assert rec_5ghz["recommended_value"] == -75, "5GHz should be -75 (iOS-friendly)"
    assert ("iOS devices" in rec_24ghz["recommendation"]
            or rec_24ghz.get("ios_friendly") is True), "Should indicate iOS-friendly mode"

    print("✅ iOS-friendly thresholds used with many iOS devices")
    print(f"   iOS detected: {result['ios_devices_detected']} ({result['ios_device_count']} devices)")
    print(f"   iOS percentage: {result['ios_percentage']:.1f}%")
    print(f"   Threshold type: {result['threshold_type']}")
    print(f"   2.4GHz recommendation: {rec_24ghz['recommended_value']} dBm")
    print(f"   5GHz recommendation: {rec_5ghz['recommended_value']} dBm")
    print(f"   Recommendation message: {rec_24ghz['recommendation'][:80]}...")
    return True


def test_aggressive_threshold_with_ios():
    """Test warning when aggressive threshold set with iOS devices"""
    print("\n" + "=" * 70)
    print("Test 4: Aggressive Threshold with iOS Devices (Warning)")
    print("=" * 70)

    client = Mock()
    analyzer = AdvancedNetworkAnalyzer(client, site="default")

    # Mock device with aggressive min RSSI already set
    devices = [
        {
            "type": "uap",
            "name": "AP-Office",
            "_id": "abc123",
            "radio_table": [
                {"radio": "ng", "min_rssi_enabled": True, "min_rssi": -72},  # Aggressive for 2.4GHz
                {"radio": "na", "min_rssi_enabled": True, "min_rssi": -70},  # Very aggressive for 5GHz
            ],
        }
    ]

    # Mock clients - 50% iOS devices
    clients = [
        {"hostname": f"iphone-{i}", "name": f"iPhone {i}", "oui": "apple"}
        for i in range(5)
    ] + [
        {"hostname": f"android-{i}", "name": f"Android {i}", "oui": "samsung"}
        for i in range(5)
    ]

    result = analyzer.analyze_min_rssi(devices, clients)

    # Verify
    assert result["ios_devices_detected"], "Should detect iOS devices"
    assert result["ios_device_count"] == 5, "Should count 5 iOS devices"
    assert result["threshold_type"] == "iOS-friendly", "Should recognize need for iOS-friendly"

    # Should have warnings about aggressive thresholds
    warnings = [r for r in result["recommendations"] if r["type"] == "min_rssi_ios_warning"]
    assert len(warnings) == 2, "Should have warnings for both radios"

    warning_24 = next(r for r in warnings if r["radio"] == "ng")
    warning_5 = next(r for r in warnings if r["radio"] == "na")

    assert warning_24["priority"] == "medium", "Warning should be medium priority"
    assert "disconnect frequently" in warning_24["recommendation"].lower(), "Should warn about disconnects"
    assert warning_24["recommended_value"] == -78, "Should recommend -78 for 2.4GHz"
    assert warning_5["recommended_value"] == -75, "Should recommend -75 for 5GHz"

    print("✅ Warnings generated for aggressive thresholds with iOS devices")
    print(f"   iOS detected: {result['ios_devices_detected']} ({result['ios_device_count']} devices)")
    print(f"   iOS percentage: {result['ios_percentage']:.1f}%")
    print(f"   Warnings: {len(warnings)}")
    print(f"   2.4GHz warning: {warning_24['message']}")
    print(f"   5GHz warning: {warning_5['message']}")
    print(f"   Recommendation: {warning_24['recommendation'][:80]}...")
    return True


def test_all_ios_network():
    """Test iOS-friendly thresholds in all-Apple environment"""
    print("\n" + "=" * 70)
    print("Test 5: All-iOS Network (100% Apple)")
    print("=" * 70)

    client = Mock()
    analyzer = AdvancedNetworkAnalyzer(client, site="default")

    devices = [
        {
            "type": "uap",
            "name": "AP-Office",
            "_id": "abc123",
            "radio_table": [
                {"radio": "ng", "min_rssi_enabled": False},
                {"radio": "na", "min_rssi_enabled": False},
            ],
        }
    ]

    # Mock clients - all iOS
    clients = [
        {"hostname": "johns-iphone", "name": "iPhone 16 Pro", "oui": "apple"},
        {"hostname": "janes-iphone", "name": "iPhone 15", "oui": "apple"},
        {"hostname": "office-ipad", "name": "iPad Pro", "oui": "apple"},
        {"hostname": "bobs-macbook", "name": "MacBook Pro", "oui": "apple"},
    ]

    result = analyzer.analyze_min_rssi(devices, clients)

    # Verify
    assert result["ios_devices_detected"], "Should detect iOS devices"
    assert result["ios_device_count"] == 4, "Should count all 4 iOS devices"
    assert result["ios_percentage"] == 100.0, "Should be 100%"
    assert result["threshold_type"] == "iOS-friendly", "Must use iOS-friendly in all-Apple network"

    rec_24ghz = next(r for r in result["recommendations"] if r["radio"] == "ng")
    rec_5ghz = next(r for r in result["recommendations"] if r["radio"] == "na")

    assert rec_24ghz["recommended_value"] == -78, "2.4GHz should be -78 (iOS-friendly)"
    assert rec_5ghz["recommended_value"] == -75, "5GHz should be -75 (iOS-friendly)"

    print("✅ iOS-friendly thresholds enforced in all-Apple environment")
    print(f"   iOS detected: {result['ios_devices_detected']} ({result['ios_device_count']} devices)")
    print(f"   iOS percentage: {result['ios_percentage']:.1f}%")
    print(f"   Threshold type: {result['threshold_type']}")
    print(f"   2.4GHz recommendation: {rec_24ghz['recommended_value']} dBm")
    print(f"   5GHz recommendation: {rec_5ghz['recommended_value']} dBm")
    return True


def run_all_tests():
    """Run all iPhone-aware min RSSI tests"""
    print("\n" + "=" * 70)
    print("IPHONE-AWARE MIN RSSI TEST SUITE")
    print("=" * 70)

    tests = [
        ("No iOS Devices", test_no_ios_devices),
        ("Few iOS Devices (<20%)", test_few_ios_devices),
        ("Many iOS Devices (>20%)", test_many_ios_devices),
        ("Aggressive Threshold Warning", test_aggressive_threshold_with_ios),
        ("All-iOS Network", test_all_ios_network),
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
            import traceback
            traceback.print_exc()
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

    if passed == len(tests):
        print("\n🎉 All tests passed! iPhone-aware min RSSI logic working correctly.")

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
