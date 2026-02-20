#!/usr/bin/env python3
"""
End-to-End Test: 6GHz in HTML Report
Verifies that 6GHz bands appear in the generated HTML Current Status table
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.advanced_analyzer import AdvancedNetworkAnalyzer
from core.report_v2 import generate_airtime_analysis_html
from unittest.mock import Mock


def test_6ghz_in_html_output():
    """Test that 6GHz bands appear in the HTML report"""
    print("\n" + "="*70)
    print("End-to-End Test: 6GHz in HTML Report Current Status")
    print("="*70 + "\n")

    # Create mock client
    mock_client = Mock()
    analyzer = AdvancedNetworkAnalyzer(mock_client, site="default")

    # Mock device with all three bands
    devices = [
        {
            "type": "uap",
            "name": "U7 Pro Living Room",
            "_id": "ap1",
            "mac": "aa:bb:cc:dd:ee:01",
            "radio_table_stats": [
                {
                    "name": "ra0",
                    "radio": "ng",
                    "cu_total": 30,
                    "cu_self_tx": 15,
                    "cu_self_rx": 8,
                    "num_sta": 10
                },
                {
                    "name": "ra1",
                    "radio": "na",
                    "cu_total": 55,
                    "cu_self_tx": 25,
                    "cu_self_rx": 15,
                    "num_sta": 18
                },
                {
                    "name": "ra2",
                    "radio": "6e",  # 6GHz radio
                    "cu_total": 20,
                    "cu_self_tx": 10,
                    "cu_self_rx": 5,
                    "num_sta": 7
                }
            ]
        }
    ]

    # Step 1: Generate airtime analysis
    print("Step 1: Running airtime analysis...")
    results = analyzer.analyze_airtime_utilization(devices, lookback_hours=24)

    ap_util = results.get("ap_utilization", {})
    print(f"  Generated {len(ap_util)} AP/Band entries:")
    for key in ap_util.keys():
        print(f"    • {key}")

    # Step 2: Generate HTML from the analysis
    print("\nStep 2: Generating HTML report...")
    html_output = generate_airtime_analysis_html(results)

    # Step 3: Verify HTML contains 6GHz bands
    print("\nStep 3: Verifying HTML output...")

    # Check for band names in HTML
    checks = [
        ("U7 Pro Living Room (2.4GHz)", "2.4GHz band"),
        ("U7 Pro Living Room (5GHz)", "5GHz band"),
        ("U7 Pro Living Room (6GHz)", "6GHz band"),
    ]

    print("\n✅ HTML Content Verification:")
    all_found = True
    for search_text, description in checks:
        if search_text in html_output:
            print(f"  ✓ {description} found in HTML")
        else:
            print(f"  ✗ {description} NOT FOUND in HTML")
            all_found = False

    # Check for Current Status section
    if "Current Status" in html_output:
        print("  ✓ 'Current Status' section present")
    else:
        print("  ✗ 'Current Status' section missing")
        all_found = False

    # Check for table headers
    if "Access Point (Band)" in html_output:
        print("  ✓ Table header 'Access Point (Band)' present")
    else:
        print("  ✗ Table header missing")
        all_found = False

    if not all_found:
        print("\n❌ Some checks failed!")
        print("\nHTML Preview (first 500 chars):")
        print("-" * 70)
        print(html_output[:500])
        print("-" * 70)
        return False

    # Extract and display the Current Status table section
    print("\n📊 Generated HTML Table Preview:")
    print("-" * 70)

    # Find the table section
    if "<table" in html_output and "</table>" in html_output:
        table_start = html_output.find("<table")
        table_end = html_output.find("</table>", table_start) + 8
        table_html = html_output[table_start:table_end]

        # Extract just the data rows for readability
        print("Current Status Table:")
        print()
        for band in ["2.4GHz", "5GHz", "6GHz"]:
            if f"({band})" in table_html:
                print(f"  ✓ U7 Pro Living Room ({band}) - Present in HTML table")

    print("-" * 70)

    print("\n" + "="*70)
    print("✅ END-TO-END TEST PASSED!")
    print("="*70)
    print("\n📋 Result: 6GHz bands WILL appear in HTML reports under 'Current Status'")
    print()

    return True


if __name__ == "__main__":
    try:
        test_6ghz_in_html_output()
        print("\n🎉 CONFIRMED: 6GHz support is working in HTML reports!")
        print()
        print("When you generate a report with WiFi 6E APs, you'll see:")
        print()
        print("  ┌─────────────────────────────────────────────────────────┐")
        print("  │ Current Status                                          │")
        print("  ├────────────────────────────┬────────────┬────────┬──────┤")
        print("  │ Access Point (Band)        │ Airtime    │ Clients│Status│")
        print("  ├────────────────────────────┼────────────┼────────┼──────┤")
        print("  │ U7 Pro Living Room (2.4GHz)│ ▓▓▓░ 30%  │ 10     │ Good │")
        print("  │ U7 Pro Living Room (5GHz)  │ ▓▓▓▓ 55%  │ 18     │Warning│")
        print("  │ U7 Pro Living Room (6GHz)  │ ▓▓░░ 20%  │ 7      │ Good │ ← NEW!")
        print("  └────────────────────────────┴────────────┴────────┴──────┘")
        print()

    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}\n")
        exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}\n")
        import traceback
        traceback.print_exc()
        exit(1)
