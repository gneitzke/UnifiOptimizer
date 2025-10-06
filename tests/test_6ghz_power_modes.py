#!/usr/bin/env python3
"""
Test 6GHz Power Mode Analysis (LPI/VLP/SP)

Power modes determine range and coverage on 6GHz:
- LPI (Low Power Indoor): +24dBm EIRP, no AFC required, limited range
- VLP (Very Low Power): +14dBm EIRP, portable devices only, very limited
- SP (Standard Power): +36dBm EIRP, requires AFC, ~4x range vs LPI

This test simulates Dustin's network scenario where LPI + high retry rate
indicates insufficient coverage that SP mode would fix.
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


def test_6ghz_power_modes():
    """Test 6GHz power mode analysis and AFC recommendations"""

    print("=" * 70)
    print("Testing 6GHz Power Mode Analysis (LPI/VLP/SP)")
    print("=" * 70)

    # Create analyzer
    analyzer = AdvancedNetworkAnalyzer(MockClient(), "default")

    # Test Case 1: Dustin's Network - LPI with High Retry Rate
    print("\n📊 Test Case 1: LPI Mode with High Retry Rate (Dustin's Scenario)")
    print("=" * 70)

    devices_dustin = [
        {
            "name": "U7 Pro - Main",
            "type": "uap",
            "model": "U7-Pro",
            "mac": "aa:bb:cc:dd:ee:01",
            "radio_table": [
                {
                    "radio": "6e",  # 6GHz
                    "channel": 149,  # PSC channel
                    "ht": 320,  # 320MHz (WiFi 7)
                    "tx_power": "auto",
                    "tx_power_mode": "LPI",  # Limited power
                }
            ],
            "radio_table_stats": [
                {
                    "radio": "6e",
                    "tx_retries_pct": 27.7,  # CRITICAL retry rate
                    "num_sta": 2,  # 2 clients
                }
            ]
        }
    ]

    result_dustin = analyzer.analyze_6ghz_power_modes(devices_dustin)

    print(f"Total 6GHz Radios: {result_dustin['radios_6ghz']}")
    print(f"LPI Mode: {result_dustin['lpi_mode']}")
    print(f"SP Mode: {result_dustin['sp_mode']}")
    print(f"Summary: {result_dustin['summary']}")
    print(f"Severity: {result_dustin['severity']}")
    print(f"Recommendations: {len(result_dustin['recommendations'])}")

    assert result_dustin["radios_6ghz"] == 1, "Should detect 1 6GHz radio"
    assert result_dustin["lpi_mode"] == 1, "Should detect LPI mode"
    assert result_dustin["sp_mode"] == 0, "No SP mode"
    assert len(result_dustin["recommendations"]) == 1, "Should have 1 recommendation"
    assert result_dustin["severity"] == "high", "Should be high severity (>20% retry)"

    rec = result_dustin["recommendations"][0]
    print(f"\n🚨 AFC Recommendation:")
    print(f"   Device: {rec['device']}")
    print(f"   Current Mode: {rec['current_mode']}")
    print(f"   Recommended Mode: {rec['recommended_mode']}")
    print(f"   Power Gain: +{rec['power_gain_db']} dB")
    print(f"   Range Multiplier: {rec['range_multiplier']}")
    print(f"   Priority: {rec['priority']}")
    print(f"   Retry Rate: {rec['retry_pct']:.1f}%")

    assert rec["type"] == "6ghz_power_mode_upgrade", "Should be power mode upgrade"
    assert rec["current_mode"] == "LPI", "Current should be LPI"
    assert rec["recommended_mode"] == "SP (Standard Power)", "Should recommend SP"
    assert rec["power_gain_db"] == 12, "Should show +12dB gain"
    assert rec["range_multiplier"] == "~4x", "Should show 4x range improvement"
    assert rec["priority"] == "high", "Should be high priority (>20% retry)"
    assert rec["retry_pct"] == 27.7, "Should capture retry rate"

    assert "High TX retry rate (27.7%) indicates coverage issues" in rec["recommendation"]
    assert "320MHz width with elevated retries" in rec["recommendation"]
    assert "AFC registration is FREE" in rec["recommendation"]

    print("\n✅ Correctly identifies LPI as insufficient for 27.7% retry rate")
    print("   Recommends AFC/SP mode for +12dB gain and ~4x range")

    # Test Case 2: Standard Power Mode (Optimal)
    print("\n📊 Test Case 2: Standard Power Mode (Optimal)")
    print("=" * 70)

    devices_sp = [
        {
            "name": "U7 Pro - Office",
            "type": "uap",
            "model": "U7-Pro",
            "mac": "aa:bb:cc:dd:ee:02",
            "radio_table": [
                {
                    "radio": "6e",
                    "channel": 37,
                    "ht": 160,
                    "tx_power": "auto",
                    "tx_power_mode": "SP",  # Standard Power
                }
            ],
            "radio_table_stats": [
                {
                    "radio": "6e",
                    "tx_retries_pct": 4.2,  # Excellent
                    "num_sta": 5,
                }
            ]
        }
    ]

    result_sp = analyzer.analyze_6ghz_power_modes(devices_sp)

    print(f"Total 6GHz Radios: {result_sp['radios_6ghz']}")
    print(f"SP Mode: {result_sp['sp_mode']}")
    print(f"Summary: {result_sp['summary']}")
    print(f"Recommendations: {len(result_sp['recommendations'])}")

    assert result_sp["radios_6ghz"] == 1, "Should detect 1 6GHz radio"
    assert result_sp["sp_mode"] == 1, "Should detect SP mode"
    assert result_sp["lpi_mode"] == 0, "No LPI mode"
    assert len(result_sp["recommendations"]) == 0, "No recommendations needed"
    assert result_sp["severity"] == "ok", "Should be ok (low retry, SP mode)"
    assert result_sp["summary"] == "✅ All 1 6GHz radios using Standard Power (optimal)"

    print("\n✅ Standard Power mode correctly identified as optimal")
    print("   No recommendations needed - excellent performance")

    # Test Case 3: VLP Mode Warning (Severely Limited)
    print("\n📊 Test Case 3: VLP Mode (Severely Limited)")
    print("=" * 70)

    devices_vlp = [
        {
            "name": "AP-Portable",
            "type": "uap",
            "model": "U6-Mesh",
            "mac": "aa:bb:cc:dd:ee:03",
            "radio_table": [
                {
                    "radio": "6e",
                    "channel": 5,
                    "ht": 80,
                    "tx_power": "low",
                    "tx_power_mode": "VLP",  # Very Low Power
                }
            ],
            "radio_table_stats": [
                {
                    "radio": "6e",
                    "tx_retries_pct": 18.5,
                    "num_sta": 1,
                }
            ]
        }
    ]

    result_vlp = analyzer.analyze_6ghz_power_modes(devices_vlp)

    print(f"Total 6GHz Radios: {result_vlp['radios_6ghz']}")
    print(f"VLP Mode: {result_vlp['vlp_mode']}")
    print(f"Severity: {result_vlp['severity']}")
    print(f"Recommendations: {len(result_vlp['recommendations'])}")

    assert result_vlp["radios_6ghz"] == 1, "Should detect 1 6GHz radio"
    assert result_vlp["vlp_mode"] == 1, "Should detect VLP mode"
    assert result_vlp["severity"] == "high", "Should be high severity (VLP is very limited)"
    assert len(result_vlp["recommendations"]) == 1, "Should have VLP warning"

    rec = result_vlp["recommendations"][0]
    print(f"\n⚠️  VLP Mode Warning:")
    print(f"   Type: {rec['type']}")
    print(f"   Message: {rec['message']}")
    print(f"   Priority: {rec['priority']}")

    assert rec["type"] == "6ghz_vlp_warning", "Should be VLP warning"
    assert rec["current_mode"] == "VLP", "Current should be VLP"
    assert rec["priority"] == "high", "Should be high priority"
    assert "portable devices" in rec["recommendation"]
    assert "SP (+36dBm) with AFC" in rec["recommendation"]

    print("\n✅ Correctly warns about VLP mode (+14dBm severely limited)")
    print("   Recommends switching to LPI or SP")

    # Test Case 4: Mixed LPI and SP
    print("\n📊 Test Case 4: Mixed LPI and SP Deployment")
    print("=" * 70)

    devices_mixed = [
        {
            "name": "U7 Pro - Main",
            "type": "uap",
            "model": "U7-Pro",
            "mac": "aa:bb:cc:dd:ee:04",
            "radio_table": [
                {"radio": "6e", "channel": 149, "ht": 160, "tx_power_mode": "SP"}
            ],
            "radio_table_stats": [
                {"radio": "6e", "tx_retries_pct": 3.2, "num_sta": 8}
            ]
        },
        {
            "name": "U7 Pro - Remote",
            "type": "uap",
            "model": "U7-Pro",
            "mac": "aa:bb:cc:dd:ee:05",
            "radio_table": [
                {"radio": "6e", "channel": 53, "ht": 160, "tx_power_mode": "LPI"}
            ],
            "radio_table_stats": [
                {"radio": "6e", "tx_retries_pct": 16.8, "num_sta": 4}
            ]
        },
        {
            "name": "U7 Pro - Basement",
            "type": "uap",
            "model": "U7-Pro",
            "mac": "aa:bb:cc:dd:ee:06",
            "radio_table": [
                {"radio": "6e", "channel": 101, "ht": 80, "tx_power_mode": "LPI"}
            ],
            "radio_table_stats": [
                {"radio": "6e", "tx_retries_pct": 11.2, "num_sta": 2}
            ]
        }
    ]

    result_mixed = analyzer.analyze_6ghz_power_modes(devices_mixed)

    print(f"Total 6GHz Radios: {result_mixed['radios_6ghz']}")
    print(f"SP Mode: {result_mixed['sp_mode']}")
    print(f"LPI Mode: {result_mixed['lpi_mode']}")
    print(f"Summary: {result_mixed['summary']}")
    print(f"Severity: {result_mixed['severity']}")
    print(f"Recommendations: {len(result_mixed['recommendations'])}")

    assert result_mixed["radios_6ghz"] == 3, "Should detect 3 6GHz radios"
    assert result_mixed["sp_mode"] == 1, "1 SP mode"
    assert result_mixed["lpi_mode"] == 2, "2 LPI mode"
    assert result_mixed["severity"] == "medium", "Should be medium (16.8% retry)"
    assert len(result_mixed["recommendations"]) == 1, "Should recommend SP for high-retry AP"

    # Find the recommendation for U7 Pro - Remote (16.8% retry)
    rec = result_mixed["recommendations"][0]
    assert rec["device"] == "U7 Pro - Remote", "Should flag the AP with 16.8% retry"
    assert rec["priority"] == "medium", "Should be medium priority (15-20% retry)"

    print(f"\n⚠️  Recommendations:")
    print(f"   {rec['device']}: {rec['current_mode']} → {rec['recommended_mode']}")
    print(f"   Reason: {rec['retry_pct']:.1f}% retry rate")

    print("\n✅ Correctly identifies mixed deployment")
    print("   Recommends AFC/SP only for AP with coverage issues")

    # Test Case 5: LPI with Good Performance (No Recommendation)
    print("\n📊 Test Case 5: LPI Mode with Good Performance")
    print("=" * 70)

    devices_lpi_good = [
        {
            "name": "U7 Pro - Small Office",
            "type": "uap",
            "model": "U7-Pro",
            "mac": "aa:bb:cc:dd:ee:07",
            "radio_table": [
                {"radio": "6e", "channel": 85, "ht": 80, "tx_power_mode": "LPI"}
            ],
            "radio_table_stats": [
                {"radio": "6e", "tx_retries_pct": 3.8, "num_sta": 3}
            ]
        }
    ]

    result_lpi_good = analyzer.analyze_6ghz_power_modes(devices_lpi_good)

    print(f"Total 6GHz Radios: {result_lpi_good['radios_6ghz']}")
    print(f"LPI Mode: {result_lpi_good['lpi_mode']}")
    print(f"Retry Rate: 3.8%")
    print(f"Recommendations: {len(result_lpi_good['recommendations'])}")

    assert result_lpi_good["radios_6ghz"] == 1, "Should detect 1 6GHz radio"
    assert result_lpi_good["lpi_mode"] == 1, "Should detect LPI mode"
    assert len(result_lpi_good["recommendations"]) == 0, "No recommendations (good performance)"
    assert result_lpi_good["severity"] == "ok", "Should be ok (low retry)"

    print("\n✅ LPI mode with good performance - no recommendation needed")
    print("   AFC/SP not required when coverage is adequate")

    # Test Case 6: No 6GHz Radios
    print("\n📊 Test Case 6: No 6GHz Radios (5GHz/2.4GHz Only)")
    print("=" * 70)

    devices_no_6ghz = [
        {
            "name": "U6-LR",
            "type": "uap",
            "model": "U6-LR",
            "mac": "aa:bb:cc:dd:ee:08",
            "radio_table": [
                {"radio": "ng", "channel": 6},  # 2.4GHz
                {"radio": "na", "channel": 44}  # 5GHz
            ],
            "radio_table_stats": []
        }
    ]

    result_no_6ghz = analyzer.analyze_6ghz_power_modes(devices_no_6ghz)

    print(f"Total 6GHz Radios: {result_no_6ghz['radios_6ghz']}")
    print(f"Summary: {result_no_6ghz['summary']}")

    assert result_no_6ghz["radios_6ghz"] == 0, "Should detect no 6GHz radios"
    assert result_no_6ghz["summary"] == "No 6GHz radios detected"
    assert len(result_no_6ghz["recommendations"]) == 0, "No recommendations"

    print("\n✅ Correctly handles networks without 6GHz")

    print("\n" + "=" * 70)
    print("✅ ALL TESTS PASSED!")
    print("=" * 70)
    print("\n🎯 KEY FINDINGS:")
    print("  • Power mode detection working correctly (LPI/VLP/SP)")
    print("  • AFC recommendations triggered by high retry rates")
    print("  • VLP mode warnings for severely limited power")
    print("  • Smart recommendations only when coverage issues present")
    print("  • Priority scaling based on retry rate severity")
    print("\n💡 REAL-WORLD IMPACT:")
    print("  • Dustin's network: LPI + 27.7% retry → Recommend AFC/SP")
    print("  • AFC enables +12dB power gain (~4x range)")
    print("  • Standard Power dramatically reduces retry rates")
    print("  • FREE AFC registration via FCC database")
    print("\n🎉 6GHz POWER MODE ANALYSIS WORKING!")


if __name__ == "__main__":
    test_6ghz_power_modes()
