"""
Test suite for mesh AP protection and client population analysis
Validates that mesh APs are protected from aggressive min RSSI and band steering
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.advanced_analyzer import AdvancedNetworkAnalyzer


class MockClient:
    """Mock UniFi client for testing"""

    def __init__(self):
        pass

    def get(self, endpoint):
        """Return empty data"""
        return {"data": []}


def test_mesh_ap_min_rssi_protection():
    """
    Test: Mesh APs with min RSSI enabled should generate CRITICAL warning
    """
    print("\n" + "=" * 70)
    print("TEST 1: Mesh AP Min RSSI Protection")
    print("=" * 70)

    mock_client = MockClient()
    analyzer = AdvancedNetworkAnalyzer(mock_client, site="default")

    # Create test data: 1 wired AP, 1 mesh AP (both with min RSSI enabled)
    devices = [
        {
            "type": "uap",
            "name": "Wired-AP",
            "_id": "wired123",
            "uplink": {"type": "wire"},  # Wired AP
            "radio_table": [
                {
                    "radio": "ng",  # 2.4GHz
                    "min_rssi_enabled": True,
                    "min_rssi": -75,
                },
                {
                    "radio": "na",  # 5GHz
                    "min_rssi_enabled": True,
                    "min_rssi": -72,
                },
            ],
        },
        {
            "type": "uap",
            "name": "Mesh-AP",
            "_id": "mesh123",
            "uplink": {"type": "wireless", "rssi": -65},  # Wireless mesh uplink
            "radio_table": [
                {
                    "radio": "ng",  # 2.4GHz
                    "min_rssi_enabled": True,
                    "min_rssi": -75,
                },
                {
                    "radio": "na",  # 5GHz
                    "min_rssi_enabled": True,
                    "min_rssi": -72,
                },
            ],
        },
    ]

    result = analyzer.analyze_min_rssi(devices, clients=[])

    print(f"\n📊 Test Setup:")
    print(f"   Wired AP with min RSSI: ✅ (should be OK)")
    print(f"   Mesh AP with min RSSI: 🚨 (should generate warning)")

    print(f"\n🔍 Analysis Results:")
    print(f"   Mesh APs detected: {len(result['mesh_aps_detected'])}")
    print(f"   Mesh APs with min RSSI: {len(result['mesh_aps_with_min_rssi'])}")
    print(f"   Total recommendations: {len(result['recommendations'])}")

    # Find mesh-specific warnings
    mesh_warnings = [
        r for r in result["recommendations"] if r.get("type") == "mesh_min_rssi_danger"
    ]

    print(f"\n⚠️  Mesh AP Warnings:")
    for warning in mesh_warnings:
        print(f"   - {warning['device']} {warning['band']}: {warning['priority']}")
        print(f"     {warning['message']}")

    # Validate
    assert len(result["mesh_aps_detected"]) == 1, "Should detect 1 mesh AP"
    assert len(result["mesh_aps_with_min_rssi"]) == 2, "Mesh AP has 2 radios with min RSSI"
    assert len(mesh_warnings) == 2, f"Should have 2 critical warnings for mesh AP (got {len(mesh_warnings)})"
    assert all(
        w["priority"] == "critical" for w in mesh_warnings
    ), "Mesh warnings should be CRITICAL priority"

    print(f"\n✅ PASS: Mesh AP min RSSI protection working correctly")
    return True


def test_mesh_ap_min_rssi_disabled_ok():
    """
    Test: Mesh APs with min RSSI disabled should NOT generate warnings
    """
    print("\n" + "=" * 70)
    print("TEST 2: Mesh AP with Min RSSI Disabled (OK)")
    print("=" * 70)

    mock_client = MockClient()
    analyzer = AdvancedNetworkAnalyzer(mock_client, site="default")

    # Mesh AP with min RSSI properly disabled
    devices = [
        {
            "type": "uap",
            "name": "Mesh-AP-Good",
            "_id": "mesh456",
            "uplink": {"type": "wireless", "rssi": -60},
            "radio_table": [
                {
                    "radio": "ng",
                    "min_rssi_enabled": False,  # Disabled (good!)
                },
                {
                    "radio": "na",
                    "min_rssi_enabled": False,  # Disabled (good!)
                },
            ],
        },
    ]

    result = analyzer.analyze_min_rssi(devices, clients=[])

    print(f"\n📊 Test Setup:")
    print(f"   Mesh AP with min RSSI disabled: ✅ (correct configuration)")

    print(f"\n🔍 Analysis Results:")
    print(f"   Mesh APs detected: {len(result['mesh_aps_detected'])}")
    print(f"   Mesh AP warnings: {len([r for r in result['recommendations'] if 'mesh' in r.get('type', '')])}")

    # Should detect mesh AP but NOT generate any warnings or recommendations
    mesh_warnings = [
        r for r in result["recommendations"] if "mesh" in r.get("type", "").lower()
    ]

    assert len(result["mesh_aps_detected"]) == 1, "Should detect 1 mesh AP"
    assert len(mesh_warnings) == 0, f"Should have NO warnings for properly configured mesh AP (got {len(mesh_warnings)})"

    print(f"\n✅ PASS: Mesh AP with disabled min RSSI correctly has no warnings")
    return True


def test_mesh_ap_band_steering_warning():
    """
    Test: Mesh APs with band steering enabled should generate warning
    """
    print("\n" + "=" * 70)
    print("TEST 3: Mesh AP Band Steering Warning")
    print("=" * 70)

    mock_client = MockClient()
    analyzer = AdvancedNetworkAnalyzer(mock_client, site="default")

    devices = [
        {
            "type": "uap",
            "name": "Wired-AP",
            "_id": "wired789",
            "uplink": {"type": "wire"},
            "bandsteering_mode": "off",  # Disabled (bad for wired AP)
            "radio_table": [],
        },
        {
            "type": "uap",
            "name": "Mesh-AP",
            "_id": "mesh789",
            "uplink": {"type": "wireless", "rssi": -58},
            "bandsteering_mode": "prefer_5g",  # Enabled (may cause mesh issues)
            "radio_table": [],
        },
    ]

    # Create some test clients
    clients = [{"hostname": "test-device", "is_wired": False, "radio": "ng"}]

    result = analyzer.analyze_band_steering(devices, clients)

    print(f"\n📊 Test Setup:")
    print(f"   Wired AP with band steering off: ❌ (should recommend enabling)")
    print(f"   Mesh AP with band steering on: ⚠️  (should warn)")

    print(f"\n🔍 Analysis Results:")
    print(f"   Mesh APs detected: {len(result['mesh_aps_detected'])}")
    print(f"   Mesh APs with band steering: {len(result['mesh_aps_with_band_steering'])}")

    mesh_warnings = [
        r
        for r in result["recommendations"]
        if r.get("type") == "mesh_band_steering_warning"
    ]

    print(f"\n⚠️  Mesh Band Steering Warnings:")
    for warning in mesh_warnings:
        print(f"   - {warning['device']}: {warning['priority']}")
        print(f"     {warning['message']}")

    # Validate
    assert len(result["mesh_aps_detected"]) == 1, "Should detect 1 mesh AP"
    assert len(result["mesh_aps_with_band_steering"]) == 1, "Should flag mesh AP with band steering"
    assert len(mesh_warnings) == 1, f"Should have 1 warning for mesh AP band steering (got {len(mesh_warnings)})"
    assert mesh_warnings[0]["priority"] == "medium", "Mesh band steering warning should be MEDIUM priority"

    # Should NOT recommend enabling band steering on mesh AP
    wired_recommendations = [r for r in result["recommendations"] if r.get("device") == "Wired-AP"]
    mesh_enable_recommendations = [
        r for r in result["recommendations"]
        if r.get("device") == "Mesh-AP" and r.get("type") == "band_steering"
    ]

    assert len(wired_recommendations) > 0, "Should recommend enabling band steering on wired AP"
    assert len(mesh_enable_recommendations) == 0, "Should NOT recommend enabling band steering on mesh AP"

    print(f"\n✅ PASS: Mesh AP band steering protection working correctly")
    return True


def test_client_population_analysis():
    """
    Test: Client population analyzer correctly categorizes devices
    """
    print("\n" + "=" * 70)
    print("TEST 4: Client Population Analysis")
    print("=" * 70)

    mock_client = MockClient()
    analyzer = AdvancedNetworkAnalyzer(mock_client, site="default")

    # Create diverse client population
    clients = [
        # iOS devices
        {"hostname": "johns-iphone", "name": "iPhone", "oui": "apple", "radio_proto": "ax"},
        {"hostname": "marys-ipad", "name": "iPad", "oui": "apple", "radio_proto": "ax"},
        {"hostname": "bobs-iphone-13", "name": "iPhone", "oui": "apple", "radio_proto": "ax"},
        # Android devices
        {"hostname": "android-galaxy", "name": "Galaxy", "oui": "samsung", "radio_proto": "ax"},
        {"hostname": "pixel-7", "name": "Pixel", "oui": "google", "radio_proto": "ax"},
        # Windows/Mac
        {"hostname": "work-laptop", "name": "Windows-PC", "oui": "intel", "radio_proto": "ax"},
        {"hostname": "macbook-pro", "name": "MacBook", "oui": "apple", "radio_proto": "ax"},
        # IoT
        {"hostname": "nest-thermostat", "name": "Nest", "oui": "nest", "radio_proto": "n"},
        {"hostname": "ring-doorbell", "name": "Ring", "oui": "ring", "radio_proto": "n"},
        {"hostname": "smart-tv", "name": "Samsung-TV", "oui": "samsung", "radio_proto": "ac"},
    ]

    result = analyzer.analyze_client_population(clients)

    print(f"\n📊 Client Population:")
    print(f"   Total clients: {result['total_clients']}")
    print(f"   iOS devices: {result['ios_count']} ({result['device_distribution']['ios_percentage']}%)")
    print(f"   Android devices: {result['android_count']} ({result['device_distribution']['android_percentage']}%)")
    print(f"   Mac devices: {result['mac_count']}")
    print(f"   Windows devices: {result['windows_count']}")
    print(f"   IoT devices: {result['iot_count']} ({result['device_distribution']['iot_percentage']}%)")

    print(f"\n📡 WiFi Generations:")
    for gen, count in result["wifi_generations"].items():
        print(f"   {gen}: {count}")

    print(f"\n💡 Recommendations:")
    for rec in result["recommendations"]:
        print(f"   - {rec['type']}: {rec['message']}")
        print(f"     {rec['recommendation']}")

    # Validate
    assert result["total_clients"] == 10, "Should count all 10 clients"
    assert result["ios_count"] == 3, f"Should detect 3 iOS devices (got {result['ios_count']})"
    assert result["android_count"] == 2, f"Should detect 2 Android devices (got {result['android_count']})"
    assert result["mac_count"] == 1, "Should detect 1 Mac"
    assert result["windows_count"] == 1, "Should detect 1 Windows device"
    assert result["iot_count"] == 3, "Should detect 3 IoT devices"

    # 3 iPhones out of 10 = 30% iOS
    assert result["device_distribution"]["ios_percentage"] == 30.0, "Should calculate 30% iOS"

    # Should recommend iOS-friendly thresholds (>20% iOS)
    ios_recommendations = [r for r in result["recommendations"] if "ios" in r.get("type", "")]
    assert len(ios_recommendations) > 0, "Should have iOS-related recommendation at 30% iOS population"

    print(f"\n✅ PASS: Client population analysis working correctly")
    return True


def test_ios_percentage_adjusts_recommendations():
    """
    Test: Min RSSI recommendations adjust based on iOS percentage
    """
    print("\n" + "=" * 70)
    print("TEST 5: iOS Percentage Adjusts Min RSSI Recommendations")
    print("=" * 70)

    mock_client = MockClient()
    analyzer = AdvancedNetworkAnalyzer(mock_client, site="default")

    # Single wired AP (not mesh)
    devices = [
        {
            "type": "uap",
            "name": "Office-AP",
            "_id": "office123",
            "uplink": {"type": "wire"},
            "radio_table": [
                {"radio": "ng", "min_rssi_enabled": False},
                {"radio": "na", "min_rssi_enabled": False},
            ],
        },
    ]

    # Test with 50% iOS clients (should use iOS-friendly thresholds)
    clients_high_ios = [
        {"hostname": "iphone-1", "oui": "apple", "radio_proto": "ax"},
        {"hostname": "iphone-2", "oui": "apple", "radio_proto": "ax"},
        {"hostname": "iphone-3", "oui": "apple", "radio_proto": "ax"},
        {"hostname": "iphone-4", "oui": "apple", "radio_proto": "ax"},
        {"hostname": "iphone-5", "oui": "apple", "radio_proto": "ax"},
        {"hostname": "android-1", "oui": "samsung", "radio_proto": "ax"},
        {"hostname": "android-2", "oui": "samsung", "radio_proto": "ax"},
        {"hostname": "android-3", "oui": "samsung", "radio_proto": "ax"},
        {"hostname": "android-4", "oui": "samsung", "radio_proto": "ax"},
        {"hostname": "android-5", "oui": "samsung", "radio_proto": "ax"},
    ]

    result_high_ios = analyzer.analyze_min_rssi(devices, clients=clients_high_ios)

    print(f"\n📊 High iOS Environment (50% iOS):")
    print(f"   iOS percentage: {result_high_ios['ios_percentage']:.1f}%")
    print(f"   Threshold type: {result_high_ios['threshold_type']}")

    # Check recommended values in recommendations
    recommendations = [r for r in result_high_ios["recommendations"] if r.get("recommended_value")]
    if recommendations:
        print(f"   Recommended 2.4GHz: {[r['recommended_value'] for r in recommendations if r['band'] == '2.4GHz'][0]} dBm")
        print(f"   Recommended 5GHz: {[r['recommended_value'] for r in recommendations if r['band'] == '5GHz'][0]} dBm")

    # Test with 10% iOS clients (should use standard thresholds)
    clients_low_ios = [
        {"hostname": "iphone-1", "oui": "apple", "radio_proto": "ax"},
        {"hostname": "android-1", "oui": "samsung", "radio_proto": "ax"},
        {"hostname": "android-2", "oui": "samsung", "radio_proto": "ax"},
        {"hostname": "android-3", "oui": "samsung", "radio_proto": "ax"},
        {"hostname": "android-4", "oui": "samsung", "radio_proto": "ax"},
        {"hostname": "android-5", "oui": "samsung", "radio_proto": "ax"},
        {"hostname": "android-6", "oui": "samsung", "radio_proto": "ax"},
        {"hostname": "android-7", "oui": "samsung", "radio_proto": "ax"},
        {"hostname": "android-8", "oui": "samsung", "radio_proto": "ax"},
        {"hostname": "android-9", "oui": "samsung", "radio_proto": "ax"},
    ]

    result_low_ios = analyzer.analyze_min_rssi(devices, clients=clients_low_ios)

    print(f"\n📊 Low iOS Environment (10% iOS):")
    print(f"   iOS percentage: {result_low_ios['ios_percentage']:.1f}%")
    print(f"   Threshold type: {result_low_ios['threshold_type']}")

    recommendations = [r for r in result_low_ios["recommendations"] if r.get("recommended_value")]
    if recommendations:
        print(f"   Recommended 2.4GHz: {[r['recommended_value'] for r in recommendations if r['band'] == '2.4GHz'][0]} dBm")
        print(f"   Recommended 5GHz: {[r['recommended_value'] for r in recommendations if r['band'] == '5GHz'][0]} dBm")

    # Validate
    assert result_high_ios["ios_percentage"] == 50.0, "Should calculate 50% iOS"
    assert result_high_ios["threshold_type"] == "iOS-friendly", "Should use iOS-friendly thresholds at 50% iOS"

    assert result_low_ios["ios_percentage"] == 10.0, "Should calculate 10% iOS"
    assert result_low_ios["threshold_type"] == "standard", "Should use standard thresholds at 10% iOS"

    print(f"\n✅ PASS: iOS percentage correctly adjusts min RSSI recommendations")
    return True


if __name__ == "__main__":
    try:
        test_mesh_ap_min_rssi_protection()
        test_mesh_ap_min_rssi_disabled_ok()
        test_mesh_ap_band_steering_warning()
        test_client_population_analysis()
        test_ios_percentage_adjusts_recommendations()

        print("\n" + "=" * 70)
        print("🎉 ALL TESTS PASSED!")
        print("=" * 70)
        print("\n✅ Mesh AP Protection:")
        print("   - Mesh APs with min RSSI generate CRITICAL warnings")
        print("   - Mesh APs with min RSSI disabled have no warnings")
        print("   - Mesh APs with band steering generate warnings")
        print("   - Mesh APs excluded from enable recommendations")
        print("\n✅ Client Population Analysis:")
        print("   - Correctly categorizes iOS, Android, desktop, IoT devices")
        print("   - Calculates accurate device percentages")
        print("   - Generates appropriate recommendations")
        print("\n✅ iOS-Aware Min RSSI:")
        print("   - Adjusts thresholds based on iOS client percentage")
        print("   - Uses iOS-friendly thresholds when >20% iOS")
        print("   - Uses standard thresholds when <20% iOS")
        print("\n")

    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}\n")
        sys.exit(1)
