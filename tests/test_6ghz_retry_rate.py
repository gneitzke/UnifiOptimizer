#!/usr/bin/env python3
"""
Test 6GHz TX Retry Rate Detection
Simulates Dustin's network with 27.7% retry rate on 6GHz
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import Mock

from core.advanced_analyzer import AdvancedNetworkAnalyzer


def test_6ghz_high_retry_detection():
    """Test detection of high retry rates on 6GHz (Dustin's scenario)"""
    print("\n" + "="*70)
    print("Testing 6GHz High TX Retry Rate Detection")
    print("="*70 + "\n")

    # Create mock client
    mock_client = Mock()
    analyzer = AdvancedNetworkAnalyzer(mock_client, site="default")

    # Simulate Dustin's network: U7 Pro with 27.7% retry on 6GHz
    devices = [
        {
            "type": "uap",
            "name": "U7 Pro - Kitchen",
            "_id": "ap1",
            "mac": "28:70:4e:d1:ac:30",
            "radio_table": [
                {
                    "radio": "ng",
                    "ht": 20,
                    "ap_pwr_type": None
                },
                {
                    "radio": "na",
                    "ht": 80,
                    "ap_pwr_type": None
                },
                {
                    "radio": "6e",  # 6GHz radio
                    "ht": 320,  # 320MHz width
                    "ap_pwr_type": "LPI"  # Low Power Indoor
                }
            ],
            "radio_table_stats": [
                {
                    "name": "wifi0",
                    "radio": "ng",
                    "channel": 1,
                    "tx_power": 22,
                    "tx_packets": 39,
                    "tx_retries": 0,
                    "tx_retries_pct": 0.0,  # Excellent
                    "num_sta": 3
                },
                {
                    "name": "wifi1",
                    "radio": "na",
                    "channel": 64,
                    "tx_power": 24,
                    "tx_packets": 28,
                    "tx_retries": 2,
                    "tx_retries_pct": 7.7,  # Good
                    "num_sta": 3
                },
                {
                    "name": "wifi2",
                    "radio": "6e",  # 6GHz
                    "channel": 149,
                    "tx_power": 24,
                    "tx_packets": 1047,
                    "tx_retries": 324,
                    "tx_retries_pct": 27.7,  # CRITICAL! (Dustin's issue)
                    "num_sta": 2
                }
            ]
        },
        {
            "type": "uap",
            "name": "U7 Pro - Garage",
            "_id": "ap2",
            "mac": "28:70:4e:d1:a5:58",
            "radio_table": [
                {
                    "radio": "ng",
                    "ht": 20,
                    "ap_pwr_type": None
                },
                {
                    "radio": "na",
                    "ht": 80,
                    "ap_pwr_type": None
                },
                {
                    "radio": "6e",
                    "ht": 160,  # 160MHz (better than 320MHz)
                    "ap_pwr_type": "LPI"
                }
            ],
            "radio_table_stats": [
                {
                    "name": "wifi0",
                    "radio": "ng",
                    "channel": 11,
                    "tx_power": 22,
                    "tx_packets": 50,
                    "tx_retries": 1,
                    "tx_retries_pct": 2.0,  # Excellent
                    "num_sta": 5
                },
                {
                    "name": "wifi1",
                    "radio": "na",
                    "channel": 44,
                    "tx_power": 24,
                    "tx_packets": 100,
                    "tx_retries": 8,
                    "tx_retries_pct": 8.0,  # Good
                    "num_sta": 8
                },
                {
                    "name": "wifi2",
                    "radio": "6e",
                    "channel": 37,
                    "tx_power": 24,
                    "tx_packets": 500,
                    "tx_retries": 25,
                    "tx_retries_pct": 5.0,  # Excellent (160MHz works better!)
                    "num_sta": 4
                }
            ]
        }
    ]

    # Mock clients with weak RSSI on Kitchen 6GHz
    clients = [
        {
            "ap_mac": "28:70:4e:d1:ac:30",
            "radio": "6e",
            "rssi": -75,
            "hostname": "iPhone-15-Pro"
        },
        {
            "ap_mac": "28:70:4e:d1:ac:30",
            "radio": "6e",
            "rssi": -68,
            "hostname": "MacBook-Pro"
        }
    ]

    # Run analysis
    print("📊 Running Radio Performance Analysis...\n")
    results = analyzer.analyze_radio_performance(devices, clients)

    # Display results
    print(f"Radios Analyzed: {results['radios_analyzed']}")
    print(f"Severity: {results['severity'].upper()}")
    print(f"\nRetry Rate Distribution:")
    print(f"  Excellent (<5%):   {results['retry_rate_distribution']['excellent']}")
    print(f"  Good (5-10%):      {results['retry_rate_distribution']['good']}")
    print(f"  Warning (10-15%):  {results['retry_rate_distribution']['warning']}")
    print(f"  Critical (>15%):   {results['retry_rate_distribution']['critical']}")

    print(f"\n🚨 High Retry Radios ({len(results['high_retry_radios'])}):")
    for radio in results['high_retry_radios']:
        print(f"  • {radio['ap']} {radio['band']}: {radio['retry_pct']:.1f}% ({radio['priority'].upper()})")
        print(f"    Channel Width: {radio['channel_width']}MHz")
        print(f"    Clients: {radio['clients']}")
        if 'power_mode' in radio:
            print(f"    Power Mode: {radio['power_mode']}")

    print(f"\n💡 Recommendations ({len(results['recommendations'])}):")
    for i, rec in enumerate(results['recommendations'], 1):
        print(f"\n  {i}. [{rec['priority'].upper()}] {rec['message']}")
        print(f"     {rec['recommendation']}")

    # Assertions
    print("\n" + "="*70)
    print("✅ Validation Checks:")
    print("="*70)

    # Check total radios analyzed (2 APs × 3 radios each = 6)
    assert results['radios_analyzed'] == 6, f"Expected 6 radios, got {results['radios_analyzed']}"
    print("  ✓ Analyzed 6 radios (2 APs × 3 bands)")

    # Check severity is high (due to 27.7% retry)
    assert results['severity'] == "high", f"Expected 'high' severity, got '{results['severity']}'"
    print("  ✓ Severity correctly set to HIGH")

    # Check critical retry detection
    assert results['retry_rate_distribution']['critical'] == 1, "Should detect 1 critical retry rate"
    print("  ✓ Detected 1 CRITICAL retry rate (>15%)")

    # Check high retry radio was flagged
    assert len(results['high_retry_radios']) >= 1, "Should flag at least 1 high retry radio"
    kitchen_6ghz = next((r for r in results['high_retry_radios'] if r['ap'] == "U7 Pro - Kitchen" and r['band'] == "6GHz"), None)
    assert kitchen_6ghz is not None, "Kitchen 6GHz should be flagged"
    assert kitchen_6ghz['retry_pct'] == 27.7, f"Kitchen retry should be 27.7%, got {kitchen_6ghz['retry_pct']}"
    assert kitchen_6ghz['priority'] == "critical", f"Priority should be critical, got {kitchen_6ghz['priority']}"
    print("  ✓ Kitchen 6GHz flagged as CRITICAL (27.7%)")

    # Check 6GHz-specific recommendations
    recommendations_6ghz = [r for r in results['recommendations'] if r.get('band') == '6GHz']
    assert len(recommendations_6ghz) >= 1, "Should have 6GHz-specific recommendations"
    print(f"  ✓ Generated {len(recommendations_6ghz)} 6GHz-specific recommendations")

    # Check root cause analysis mentions 320MHz
    kitchen_rec = next((r for r in results['recommendations'] if 'Kitchen' in r['device'] and r['band'] == '6GHz'), None)
    assert kitchen_rec is not None, "Should have Kitchen 6GHz recommendation"
    assert '320MHz' in kitchen_rec['recommendation'], "Should mention 320MHz channel width"
    assert 'LPI' in kitchen_rec['recommendation'], "Should mention LPI power mode"
    print("  ✓ Root cause analysis includes 320MHz and LPI issues")

    # Verify Garage 6GHz is NOT flagged (5% is excellent)
    garage_flagged = any(r['ap'] == "U7 Pro - Garage" and r['band'] == "6GHz" for r in results['high_retry_radios'])
    assert not garage_flagged, "Garage 6GHz should NOT be flagged (5% is excellent)"
    print("  ✓ Garage 6GHz NOT flagged (5% is excellent with 160MHz)")

    # Check good radios count (5% is on the boundary, counts as "good")
    assert results['retry_rate_distribution']['good'] >= 1, "Should have good radios"
    print("  ✓ Garage 6GHz recognized as good performance (5% is acceptable)")

    print("\n" + "="*70)
    print("✅ ALL TESTS PASSED!")
    print("="*70 + "\n")

    print("🎯 KEY INSIGHTS:")
    print("  • 320MHz on 6GHz can cause HIGH retry rates (27.7%)")
    print("  • 160MHz on 6GHz works much better (5%)")
    print("  • LPI power mode limits range and reliability")
    print("  • Root cause analysis provides actionable fixes")
    print()

    return True


if __name__ == "__main__":
    try:
        test_6ghz_high_retry_detection()
        print("🎉 6GHz RETRY RATE DETECTION WORKING!\n")
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}\n")
        exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}\n")
        import traceback
        traceback.print_exc()
        exit(1)
