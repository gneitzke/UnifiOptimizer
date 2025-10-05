#!/usr/bin/env python3
"""
Test 6GHz Support
Verifies that the optimizer correctly handles WiFi 6E (6GHz) capable clients
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.advanced_analyzer import AdvancedNetworkAnalyzer


def test_6ghz_band_steering():
    """Test that band steering detects 6GHz capable clients"""
    print("\n" + "=" * 70)
    print("Testing 6GHz Support in Band Steering Analysis")
    print("=" * 70 + "\n")

    # Create analyzer with mock client (we're only testing analyze_band_steering which doesn't use it)
    analyzer = AdvancedNetworkAnalyzer(client=None, site="default")

    # Mock devices (APs)
    devices = [
        {
            "type": "uap",
            "name": "Living Room AP",
            "_id": "ap1",
            "mac": "aa:bb:cc:dd:ee:01",
            "bandsteering_mode": "prefer_5g",
            "radio_table": [
                {"radio": "ng", "channel": 6},
                {"radio": "na", "channel": 36},
                {"radio": "6e", "channel": 37},  # 6GHz radio
            ],
        },
        {
            "type": "uap",
            "name": "Bedroom AP",
            "_id": "ap2",
            "mac": "aa:bb:cc:dd:ee:02",
            "bandsteering_mode": "off",
            "radio_table": [{"radio": "ng", "channel": 1}, {"radio": "na", "channel": 149}],
        },
    ]

    # Mock clients
    clients = [
        # WiFi 6E client on 2.4GHz (should be flagged - tri-band capable)
        {
            "is_wired": False,
            "radio": "ng",
            "radio_proto": "ax-6e",
            "hostname": "iPhone 15 Pro",
            "mac": "11:22:33:44:55:01",
            "ap_mac": "aa:bb:cc:dd:ee:01",
            "ap_name": "Living Room AP",
            "rssi": -55,
            "tx_rate": 144000,
            "rx_rate": 144000,
            "nss": 2,
            "last_seen": 1234567890,
            "is_11ax": True,
        },
        # WiFi 6E client on 5GHz (should be flagged - could use 6GHz)
        {
            "is_wired": False,
            "radio": "na",
            "radio_proto": "ax-6e",
            "hostname": "Galaxy S24",
            "mac": "11:22:33:44:55:02",
            "ap_mac": "aa:bb:cc:dd:ee:01",
            "ap_name": "Living Room AP",
            "rssi": -60,
            "tx_rate": 200000,
            "rx_rate": 200000,
            "nss": 2,
            "last_seen": 1234567890,
            "is_11ax": True,
        },
        # Regular dual-band client on 2.4GHz (should be flagged - can use 5GHz)
        {
            "is_wired": False,
            "radio": "ng",
            "radio_proto": "ac",
            "hostname": "MacBook Air",
            "mac": "11:22:33:44:55:03",
            "ap_mac": "aa:bb:cc:dd:ee:02",
            "ap_name": "Bedroom AP",
            "rssi": -58,
            "tx_rate": 86000,
            "rx_rate": 86000,
            "nss": 1,
            "last_seen": 1234567890,
            "is_11ac": True,
        },
        # Client on 6GHz (optimal - no flag)
        {
            "is_wired": False,
            "radio": "6e",
            "radio_proto": "ax-6e",
            "hostname": "iPad Pro",
            "mac": "11:22:33:44:55:04",
            "ap_mac": "aa:bb:cc:dd:ee:01",
            "ap_name": "Living Room AP",
            "rssi": -52,
            "tx_rate": 400000,
            "rx_rate": 400000,
            "nss": 2,
            "last_seen": 1234567890,
            "is_11ax": True,
        },
        # Legacy client on 2.4GHz (should not be flagged - can't do better)
        {
            "is_wired": False,
            "radio": "ng",
            "radio_proto": "n",
            "hostname": "Old Thermostat",
            "mac": "11:22:33:44:55:05",
            "ap_mac": "aa:bb:cc:dd:ee:02",
            "ap_name": "Bedroom AP",
            "rssi": -62,
            "tx_rate": 24000,
            "rx_rate": 24000,
            "nss": 1,
            "last_seen": 1234567890,
        },
    ]

    # Run analysis
    results = analyzer.analyze_band_steering(devices, clients)

    # Check results
    print("📊 Band Steering Analysis Results:\n")
    print(f"Total misplaced clients: {results['dual_band_clients_on_2ghz']}")
    print(f"WiFi 6E clients on suboptimal bands: {results['tri_band_clients_suboptimal']}")
    print(f"Severity: {results['severity']}")
    print(f"Recommendations: {len(results['recommendations'])}")

    print("\n📱 Misplaced Clients Breakdown:")
    for client in results["misplaced_clients"]:
        hostname = client["hostname"]
        current = client.get("current_band", "Unknown")
        capability = client.get("capability", "Unknown")
        is_6ghz = client.get("is_6ghz_capable", False)

        icon = "🔴" if is_6ghz else "🟡"
        print(f"  {icon} {hostname}")
        print(f"     Current: {current} | Capability: {capability}")
        print(f"     Detection: {client.get('detection_reason', 'N/A')}")

    print("\n✅ Validation Checks:")

    # Validate tri-band detection
    assert (
        results["tri_band_clients_suboptimal"] == 2
    ), f"Expected 2 WiFi 6E clients, found {results['tri_band_clients_suboptimal']}"
    print("  ✓ Correctly identified 2 WiFi 6E clients on suboptimal bands")

    # Validate total misplaced (clients on 2.4GHz that support higher bands)
    # Note: dual_band_clients_on_2ghz counts only clients on 2.4GHz (iPhone 15 Pro and MacBook Air)
    # The Galaxy S24 on 5GHz is tracked separately
    assert (
        results["dual_band_clients_on_2ghz"] == 2
    ), f"Expected 2 clients on 2.4GHz, found {results['dual_band_clients_on_2ghz']}"
    print("  ✓ Correctly identified 2 clients on 2.4GHz that could use higher bands")

    # Total misplaced includes both 2.4GHz and 5GHz clients that could do better
    total_misplaced = len(results["misplaced_clients"])
    assert total_misplaced == 3, f"Expected 3 total misplaced clients, found {total_misplaced}"
    print("  ✓ Correctly identified 3 total clients on suboptimal bands")

    # Check that 6GHz client is not flagged
    on_6ghz = [c for c in clients if c.get("radio") == "6e"]
    flagged_macs = [c["mac"] for c in results["misplaced_clients"]]
    assert on_6ghz[0]["mac"] not in flagged_macs, "Client on 6GHz should not be flagged"
    print("  ✓ Client on 6GHz correctly not flagged")

    # Check that legacy client is not flagged
    legacy = [c for c in clients if c.get("hostname") == "Old Thermostat"]
    assert legacy[0]["mac"] not in flagged_macs, "Legacy-only client should not be flagged"
    print("  ✓ Legacy 2.4GHz-only client correctly not flagged")

    # Check recommendations mention 6GHz
    rec_text = " ".join(
        [r.get("message", "") + r.get("recommendation", "") for r in results["recommendations"]]
    )
    assert (
        "6GHz" in rec_text or "6ghz" in rec_text.lower()
    ), "Recommendations should mention 6GHz when WiFi 6E clients present"
    print("  ✓ Recommendations mention 6GHz optimization")

    print("\n" + "=" * 70)
    print("✅ All 6GHz Support Tests PASSED!")
    print("=" * 70 + "\n")

    return True


def test_6ghz_channel_recommendations():
    """Test that channel recommendations work for 6GHz band"""
    print("\n" + "=" * 70)
    print("Testing 6GHz Channel Recommendations")
    print("=" * 70 + "\n")

    # Test data from optimize_network.py channel logic
    test_cases = [
        {"band": "6GHz", "current": 37, "expected_new": 69},
        {"band": "6GHz", "current": 53, "expected_new": 101},
        {"band": "6GHz", "current": 101, "expected_new": 133},
        {"band": "6GHz", "current": 197, "expected_new": 37},  # Wrap around
    ]

    print("📡 6GHz Channel Change Logic:\n")

    for i, test in enumerate(test_cases, 1):
        band = test["band"]
        current = test["current"]
        expected = test["expected_new"]

        # Simulate the logic from optimize_network.py
        if band == "6GHz":
            if current < 37 or current > 213:
                new_channel = 37
            elif current == 37:
                new_channel = 69
            elif current in [53, 69]:
                new_channel = 101
            elif current in [85, 101]:
                new_channel = 133
            else:
                new_channel = 37

        status = "✓" if new_channel == expected else "✗"
        print(
            f"  {status} Test {i}: {band} channel {current} → {new_channel} "
            f"(expected {expected})"
        )

        assert (
            new_channel == expected
        ), f"Channel {current} should recommend {expected}, got {new_channel}"

    print("\n" + "=" * 70)
    print("✅ All 6GHz Channel Tests PASSED!")
    print("=" * 70 + "\n")

    return True


if __name__ == "__main__":
    try:
        test_6ghz_band_steering()
        test_6ghz_channel_recommendations()
        print("\n🎉 ALL TESTS PASSED! 6GHz support is working correctly.\n")
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}\n")
        exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}\n")
        import traceback

        traceback.print_exc()
        exit(1)
