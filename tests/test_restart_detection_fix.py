"""
Test suite for restart detection logic fix
Verifies that client disconnect events are NOT counted as device restarts
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.network_health_analyzer import NetworkHealthAnalyzer


class MockClient:
    """Mock UniFi client for testing"""

    def __init__(self, events_data):
        self.events_data = events_data

    def get(self, endpoint):
        """Return mock event data"""
        if "stat/event" in endpoint:
            return {"data": self.events_data}
        return {}


def test_client_disconnects_not_counted_as_restarts():
    """
    Test: Client disconnect events should NOT be counted as device restarts

    Scenario: AP "Front yard" has 40 client disconnections but only 1 actual restart
    Expected: restart_count = 1, not 44
    """
    print("\n" + "=" * 70)
    print("TEST 1: Client Disconnects NOT Counted as Device Restarts")
    print("=" * 70)

    # Simulate 40 client disconnect events + 1 actual AP restart
    mock_events = []

    # Add 40 client disconnect events (should be IGNORED)
    for i in range(40):
        mock_events.append({
            "time": 1696500000 + (i * 3600),  # Spread over time
            "ap": "aa:bb:cc:dd:ee:ff",  # Front yard MAC
            "msg": f"User[11:22:33:44:55:{i:02x}] disconnected from Front yard",
            "key": "EVT_WU_Disconnected",
        })

    # Add 1 actual AP restart event (should be COUNTED)
    mock_events.append({
        "time": 1696550000,
        "ap": "aa:bb:cc:dd:ee:ff",
        "msg": "Front yard was restarted",
        "key": "EVT_AP_RestartedUnknown",
    })

    mock_client = MockClient(mock_events)
    analyzer = NetworkHealthAnalyzer(mock_client, site="default")

    result = analyzer._get_device_restart_events("aa:bb:cc:dd:ee:ff", lookback_days=7)

    print(f"\n📊 Event Log Summary:")
    print(f"   Total events in log: {len(mock_events)}")
    print(f"   Client disconnects: 40")
    print(f"   Actual AP restarts: 1")

    print(f"\n🔍 Restart Detection Result:")
    print(f"   Detected restart count: {result['restart_count']}")
    print(f"   Expected restart count: 1")

    assert result["restart_count"] == 1, (
        f"Expected 1 restart, got {result['restart_count']}. "
        "Client disconnects are being counted as device restarts!"
    )

    print(f"\n✅ PASS: Client disconnects correctly ignored")
    print(f"   Only actual device restarts are counted")


def test_actual_device_restart_detected():
    """
    Test: Actual device restart events ARE properly detected
    """
    print("\n" + "=" * 70)
    print("TEST 2: Actual Device Restarts ARE Detected")
    print("=" * 70)

    mock_events = [
        {
            "time": 1696500000,
            "ap": "aa:bb:cc:dd:ee:ff",
            "msg": "Access Point was restarted",
            "key": "EVT_AP_RestartedUnknown",
        },
        {
            "time": 1696510000,
            "ap": "aa:bb:cc:dd:ee:ff",
            "msg": "Access Point rebooted",
            "key": "EVT_AP_Restarted",
        },
        {
            "time": 1696520000,
            "ap": "aa:bb:cc:dd:ee:ff",
            "msg": "Access Point upgraded to firmware 6.5.55",
            "key": "EVT_AP_Upgraded",
        },
    ]

    mock_client = MockClient(mock_events)
    analyzer = NetworkHealthAnalyzer(mock_client, site="default")

    result = analyzer._get_device_restart_events("aa:bb:cc:dd:ee:ff", lookback_days=7)

    print(f"\n📊 Events:")
    for event in mock_events:
        print(f"   - {event['key']}: {event['msg']}")

    print(f"\n🔍 Detection Result:")
    print(f"   Restart count: {result['restart_count']}")
    print(f"   Manual restart: {result['manual_restart']}")

    assert result["restart_count"] == 3, f"Expected 3 restarts, got {result['restart_count']}"
    assert result["manual_restart"] is True, "Upgrade should be detected as manual restart"

    print(f"\n✅ PASS: All device restart events detected correctly")
    print(f"   Upgrade correctly identified as manual restart")


def test_mixed_events():
    """
    Test: Mixed client and device events - only device restarts counted
    """
    print("\n" + "=" * 70)
    print("TEST 3: Mixed Events - Only Device Restarts Counted")
    print("=" * 70)

    mock_events = [
        # Client events (should be ignored)
        {
            "time": 1696500000,
            "ap": "aa:bb:cc:dd:ee:ff",
            "msg": "User[11:22:33:44:55:66] disconnected from AP",
            "key": "EVT_WU_Disconnected",
        },
        {
            "time": 1696501000,
            "ap": "aa:bb:cc:dd:ee:ff",
            "msg": "User[11:22:33:44:55:77] connected to AP",
            "key": "EVT_WU_Connected",
        },
        # Device restart (should be counted)
        {
            "time": 1696502000,
            "ap": "aa:bb:cc:dd:ee:ff",
            "msg": "AP was restarted",
            "key": "EVT_AP_RestartedUnknown",
        },
        # More client events (should be ignored)
        {
            "time": 1696503000,
            "ap": "aa:bb:cc:dd:ee:ff",
            "msg": "User[11:22:33:44:55:88] disconnected from AP",
            "key": "EVT_WU_Disconnected",
        },
    ]

    mock_client = MockClient(mock_events)
    analyzer = NetworkHealthAnalyzer(mock_client, site="default")

    result = analyzer._get_device_restart_events("aa:bb:cc:dd:ee:ff", lookback_days=7)

    print(f"\n📊 Event Breakdown:")
    print(f"   Client disconnects: 2")
    print(f"   Client connects: 1")
    print(f"   Device restarts: 1")

    print(f"\n🔍 Detection Result:")
    print(f"   Restart count: {result['restart_count']}")

    assert result["restart_count"] == 1, (
        f"Expected 1 restart, got {result['restart_count']}. "
        "Client events are being counted!"
    )

    print(f"\n✅ PASS: Only device restart counted, client events ignored")


if __name__ == "__main__":
    try:
        test_client_disconnects_not_counted_as_restarts()
        test_actual_device_restart_detected()
        test_mixed_events()

        print("\n" + "=" * 70)
        print("🎉 ALL TESTS PASSED!")
        print("=" * 70)
        print("\n✅ Restart detection logic is working correctly:")
        print("   - Client disconnects are NOT counted as device restarts")
        print("   - Actual device restart events ARE detected properly")
        print("   - Manual/upgrade restarts are identified correctly")
        print("\n")

    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}\n")
        sys.exit(1)
