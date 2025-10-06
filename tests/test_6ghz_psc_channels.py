#!/usr/bin/env python3
"""
Test 6GHz PSC (Preferred Scanning Channels) Detection

PSC channels are scanned first by WiFi 6E/7 clients for faster discovery.
Non-PSC channels may result in slower initial connections.

PSC Channels: 5, 21, 37, 53, 69, 85, 101, 117, 133, 149, 165, 181, 197
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.advanced_analyzer import AdvancedNetworkAnalyzer


class MockClient:
    """Mock UniFi client for testing"""

    def __init__(self):
        pass

    def get(self, endpoint):
        return None


def test_psc_channel_detection():
    """Test PSC channel detection and recommendations"""

    print("=" * 70)
    print("Testing 6GHz PSC Channel Detection")
    print("=" * 70)

    # Create analyzer
    analyzer = AdvancedNetworkAnalyzer(MockClient(), "default")

    # Test Case 1: All PSC Channels (Optimal)
    print("\n📊 Test Case 1: All APs Using PSC Channels (Optimal)")
    print("=" * 70)

    devices_psc = [
        {
            "name": "AP-Living-Room",
            "type": "uap",
            "mac": "aa:bb:cc:dd:ee:01",
            "radio_table": [
                {
                    "radio": "6e",  # 6GHz
                    "channel": 149,  # PSC channel
                    "ht": 160,  # 160MHz
                    "tx_power": "auto"
                }
            ]
        },
        {
            "name": "AP-Bedroom",
            "type": "uap",
            "mac": "aa:bb:cc:dd:ee:02",
            "radio_table": [
                {
                    "radio": "6e",  # 6GHz
                    "channel": 37,  # PSC channel
                    "ht": 80,  # 80MHz
                    "tx_power": "auto"
                }
            ]
        },
        {
            "name": "AP-Office",
            "type": "uap",
            "mac": "aa:bb:cc:dd:ee:03",
            "radio_table": [
                {
                    "radio": "6e",  # 6GHz
                    "channel": 5,  # PSC channel
                    "ht": 80,  # 80MHz
                    "tx_power": "auto"
                }
            ]
        }
    ]

    result_psc = analyzer.analyze_6ghz_psc_channels(devices_psc)

    print(f"Total 6GHz Radios: {result_psc['radios_6ghz']}")
    print(f"PSC Compliant: {result_psc['psc_compliant']}")
    print(f"Non-PSC: {result_psc['non_psc']}")
    print(f"PSC Compliance: {result_psc.get('psc_compliance_pct', 0):.1f}%")
    print(f"Summary: {result_psc['summary']}")
    print(f"Recommendations: {len(result_psc['recommendations'])}")

    assert result_psc["radios_6ghz"] == 3, "Should detect 3 6GHz radios"
    assert result_psc["psc_compliant"] == 3, "All should be PSC compliant"
    assert result_psc["non_psc"] == 0, "No non-PSC radios"
    assert result_psc["psc_compliance_pct"] == 100.0, "Should be 100% compliant"
    assert len(result_psc["recommendations"]) == 0, "No recommendations needed"
    assert result_psc["severity"] == "ok", "Severity should be ok"

    print("\n✅ All APs correctly identified as PSC compliant")
    print("   Fast client discovery optimized")

    # Test Case 2: Mixed PSC and Non-PSC (Dustin's Network Simulation)
    print("\n📊 Test Case 2: Mixed PSC/Non-PSC Channels (Realistic)")
    print("=" * 70)

    devices_mixed = [
        {
            "name": "U7 Pro - Main",
            "type": "uap",
            "mac": "aa:bb:cc:dd:ee:04",
            "radio_table": [
                {
                    "radio": "6e",  # 6GHz
                    "channel": 149,  # PSC channel (GOOD)
                    "ht": 320,  # 320MHz (WiFi 7)
                    "tx_power": "auto"
                }
            ]
        },
        {
            "name": "U7 Pro - Upstairs",
            "type": "uap",
            "mac": "aa:bb:cc:dd:ee:05",
            "radio_table": [
                {
                    "radio": "6e",  # 6GHz
                    "channel": 45,  # Non-PSC channel (SUBOPTIMAL)
                    "ht": 160,  # 160MHz
                    "tx_power": "auto"
                }
            ]
        }
    ]

    result_mixed = analyzer.analyze_6ghz_psc_channels(devices_mixed)

    print(f"Total 6GHz Radios: {result_mixed['radios_6ghz']}")
    print(f"PSC Compliant: {result_mixed['psc_compliant']}")
    print(f"Non-PSC: {result_mixed['non_psc']}")
    print(f"PSC Compliance: {result_mixed.get('psc_compliance_pct', 0):.1f}%")
    print(f"Summary: {result_mixed['summary']}")

    assert result_mixed["radios_6ghz"] == 2, "Should detect 2 6GHz radios"
    assert result_mixed["psc_compliant"] == 1, "One PSC compliant"
    assert result_mixed["non_psc"] == 1, "One non-PSC"
    assert result_mixed["psc_compliance_pct"] == 50.0, "Should be 50% compliant"
    assert len(result_mixed["recommendations"]) == 1, "One recommendation"
    assert result_mixed["severity"] == "info", "Severity should be info"

    # Check recommendation details
    rec = result_mixed["recommendations"][0]
    print(f"\n⚠️  Non-PSC Detection:")
    print(f"   Device: {rec['device']}")
    print(f"   Current Channel: {rec['current_channel']}")
    print(f"   Recommended Channel: {rec['recommended_channel']} (nearest PSC)")
    print(f"   Message: {rec['message']}")

    assert rec["type"] == "non_psc_channel", "Should be non-PSC type"
    assert rec["device"] == "U7 Pro - Upstairs", "Should flag upstairs AP"
    assert rec["current_channel"] == 45, "Current channel should be 45"
    assert rec["recommended_channel"] in [37, 53], "Should recommend nearest PSC (37 or 53)"
    assert rec["priority"] == "low", "Priority should be low"

    print("\n✅ Correctly flags non-PSC channel usage")
    print("   Recommends nearest PSC channel for faster discovery")

    # Test Case 3: All Non-PSC (Worst Case)
    print("\n📊 Test Case 3: All Non-PSC Channels (Suboptimal)")
    print("=" * 70)

    devices_non_psc = [
        {
            "name": "AP-A",
            "type": "uap",
            "mac": "aa:bb:cc:dd:ee:06",
            "radio_table": [
                {
                    "radio": "6e",
                    "channel": 13,  # Non-PSC
                    "ht": 80,
                    "tx_power": "auto"
                }
            ]
        },
        {
            "name": "AP-B",
            "type": "uap",
            "mac": "aa:bb:cc:dd:ee:07",
            "radio_table": [
                {
                    "radio": "6e",
                    "channel": 61,  # Non-PSC
                    "ht": 160,
                    "tx_power": "auto"
                }
            ]
        },
        {
            "name": "AP-C",
            "type": "uap",
            "mac": "aa:bb:cc:dd:ee:08",
            "radio_table": [
                {
                    "radio": "6e",
                    "channel": 125,  # Non-PSC
                    "ht": 160,
                    "tx_power": "auto"
                }
            ]
        }
    ]

    result_non_psc = analyzer.analyze_6ghz_psc_channels(devices_non_psc)

    print(f"Total 6GHz Radios: {result_non_psc['radios_6ghz']}")
    print(f"PSC Compliant: {result_non_psc['psc_compliant']}")
    print(f"Non-PSC: {result_non_psc['non_psc']}")
    print(f"PSC Compliance: {result_non_psc.get('psc_compliance_pct', 0):.1f}%")
    print(f"Summary: {result_non_psc['summary']}")

    assert result_non_psc["radios_6ghz"] == 3, "Should detect 3 6GHz radios"
    assert result_non_psc["psc_compliant"] == 0, "No PSC compliant"
    assert result_non_psc["non_psc"] == 3, "All non-PSC"
    assert result_non_psc["psc_compliance_pct"] == 0.0, "Should be 0% compliant"
    assert len(result_non_psc["recommendations"]) == 3, "Three recommendations"

    # Check all recommendations
    print("\n⚠️  Non-PSC Recommendations:")
    for rec in result_non_psc["recommendations"]:
        print(f"   {rec['device']}: Ch {rec['current_channel']} → Ch {rec['recommended_channel']} (PSC)")

    # Verify nearest PSC calculations
    rec1 = next(r for r in result_non_psc["recommendations"] if r["current_channel"] == 13)
    rec2 = next(r for r in result_non_psc["recommendations"] if r["current_channel"] == 61)
    rec3 = next(r for r in result_non_psc["recommendations"] if r["current_channel"] == 125)

    # Channel 13 → nearest PSC is 5 or 21 (distance: 8 or 8)
    assert rec1["recommended_channel"] in [5, 21], f"Ch 13 should recommend 5 or 21, got {rec1['recommended_channel']}"

    # Channel 61 → nearest PSC is 53 or 69 (distance: 8 or 8)
    assert rec2["recommended_channel"] in [53, 69], f"Ch 61 should recommend 53 or 69, got {rec2['recommended_channel']}"

    # Channel 125 → nearest PSC is 117 or 133 (distance: 8 or 8)
    assert rec3["recommended_channel"] in [117, 133], f"Ch 125 should recommend 117 or 133, got {rec3['recommended_channel']}"

    print("\n✅ Correctly calculates nearest PSC channels")
    print("   All non-PSC channels flagged for optimization")

    # Test Case 4: No 6GHz Radios
    print("\n📊 Test Case 4: No 6GHz Radios (5GHz/2.4GHz Only)")
    print("=" * 70)

    devices_no_6ghz = [
        {
            "name": "AP-Legacy",
            "type": "uap",
            "mac": "aa:bb:cc:dd:ee:09",
            "radio_table": [
                {"radio": "ng", "channel": 6},  # 2.4GHz
                {"radio": "na", "channel": 36}  # 5GHz
            ]
        }
    ]

    result_no_6ghz = analyzer.analyze_6ghz_psc_channels(devices_no_6ghz)

    print(f"Total 6GHz Radios: {result_no_6ghz['radios_6ghz']}")
    print(f"Summary: {result_no_6ghz['summary']}")

    assert result_no_6ghz["radios_6ghz"] == 0, "Should detect no 6GHz radios"
    assert result_no_6ghz["summary"] == "No 6GHz radios detected"
    assert len(result_no_6ghz["recommendations"]) == 0, "No recommendations"

    print("\n✅ Correctly handles networks without 6GHz")

    # Test Case 5: Wide Channels (320MHz)
    print("\n📊 Test Case 5: Wide Channels (320MHz WiFi 7)")
    print("=" * 70)

    devices_wide = [
        {
            "name": "U7 Pro Max",
            "type": "uap",
            "mac": "aa:bb:cc:dd:ee:10",
            "radio_table": [
                {
                    "radio": "6e",
                    "channel": 149,  # PSC channel
                    "ht": 320,  # 320MHz (WiFi 7)
                    "tx_power": "auto"
                }
            ]
        }
    ]

    result_wide = analyzer.analyze_6ghz_psc_channels(devices_wide)

    print(f"Total 6GHz Radios: {result_wide['radios_6ghz']}")
    print(f"PSC Compliant: {result_wide['psc_compliant']}")

    # Find the PSC radio
    psc_radio = result_wide["psc_radios"][0]
    print(f"Channel: {psc_radio['channel']} (PSC)")
    print(f"Width: {psc_radio['channel_width']}MHz")

    assert result_wide["psc_compliant"] == 1, "Should be PSC compliant"
    assert psc_radio["channel_width"] == 320, "Should detect 320MHz"
    assert psc_radio["is_psc"] is True, "Should be flagged as PSC"

    print("\n✅ Correctly handles wide channels (320MHz)")
    print("   Primary channel verified as PSC")

    print("\n" + "=" * 70)
    print("✅ ALL TESTS PASSED!")
    print("=" * 70)
    print("\n🎯 KEY FINDINGS:")
    print("  • PSC channel detection working correctly")
    print("  • Nearest PSC channel calculation accurate")
    print("  • Supports all channel widths (80/160/320 MHz)")
    print("  • Handles mixed deployments and edge cases")
    print("  • Provides actionable recommendations")
    print("\n🎉 6GHz PSC CHANNEL ANALYSIS WORKING!")


if __name__ == "__main__":
    test_psc_channel_detection()
