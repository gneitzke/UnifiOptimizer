"""
Test verbose mode functionality

This test verifies that verbose logging is working correctly for all HTTP methods.
"""

import sys
import os
from io import StringIO
from unittest.mock import Mock, patch, MagicMock

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.cloudkey_gen2_client import CloudKeyGen2Client


def test_verbose_get_request():
    """Test verbose logging for GET requests"""
    print("\n🧪 Testing verbose GET request logging...")

    # Create client with verbose=True
    client = CloudKeyGen2Client(
        host="https://192.168.1.1",
        username="test",
        password="test",
        site="default",
        verify_ssl=False,
        verbose=True
    )

    # Mock the session and response
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.cookies = {}  # Add cookies for CSRF manager
    mock_response.json.return_value = {
        "data": [
            {"device_id": "abc123", "name": "Test AP", "model": "U6-Pro"},
            {"device_id": "def456", "name": "Test AP 2", "model": "U6-LR"}
        ]
    }

    client.session = Mock()
    client.session.get.return_value = mock_response
    client.logged_in = True

    # Mock CSRF manager to avoid cookie issues
    client.csrf_manager = Mock()
    client.csrf_manager.token = None
    client.csrf_manager.update_token = Mock()

    # Capture console output
    with patch('api.cloudkey_gen2_client.console') as mock_console:
        result = client.get("s/default/stat/device")

        # Verify verbose logging was called
        assert mock_console.print.called, "Console print should be called in verbose mode"

        # Check that we logged the request
        calls = [str(call) for call in mock_console.print.call_args_list]
        request_logged = any("GET Request" in str(call) for call in calls)
        assert request_logged, "GET request should be logged in verbose mode"

        # Check that we logged the response (look for the arrow or word "Response")
        response_logged = any(("Response" in str(call) or "Data items" in str(call) or "bold green" in str(call)) for call in calls)
        assert response_logged, f"Response should be logged in verbose mode. Calls: {calls}"

        print("   ✅ GET request verbose logging works")

    return True


def test_verbose_put_request():
    """Test verbose logging for PUT requests"""
    print("\n🧪 Testing verbose PUT request logging...")

    # Create client with verbose=True
    client = CloudKeyGen2Client(
        host="https://192.168.1.1",
        username="test",
        password="test",
        site="default",
        verify_ssl=False,
        verbose=True
    )

    # Mock the session and response
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.cookies = {}  # Add cookies for CSRF manager
    mock_response.json.return_value = {
        "meta": {"rc": "ok"},
        "data": [{"device_id": "abc123", "state": 1}]
    }

    client.session = Mock()
    client.session.put.return_value = mock_response
    client.logged_in = True

    # Mock CSRF manager to avoid cookie issues
    client.csrf_manager = Mock()
    client.csrf_manager.token = "test_token_12345678901234567890"
    client.csrf_manager.update_token = Mock()

    test_data = {
        "radio_table": [
            {"channel": 36, "ht": "80", "name": "ra0"}
        ]
    }

    # Capture console output
    with patch('api.cloudkey_gen2_client.console') as mock_console:
        result = client.put("s/default/rest/device/abc123", test_data)

        # Verify verbose logging was called
        assert mock_console.print.called, "Console print should be called in verbose mode"

        # Check that we logged the request with payload
        calls = [str(call) for call in mock_console.print.call_args_list]
        request_logged = any("PUT Request" in str(call) for call in calls)
        assert request_logged, "PUT request should be logged in verbose mode"

        # Check that payload was logged
        payload_logged = any("Payload" in str(call) for call in calls)
        assert payload_logged, "PUT payload should be logged in verbose mode"

        print("   ✅ PUT request verbose logging works")

    return True


def test_verbose_post_request():
    """Test verbose logging for POST requests"""
    print("\n🧪 Testing verbose POST request logging...")

    # Create client with verbose=True
    client = CloudKeyGen2Client(
        host="https://192.168.1.1",
        username="test",
        password="test",
        site="default",
        verify_ssl=False,
        verbose=True
    )

    # Mock the session and response
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.cookies = {}  # Add cookies for CSRF manager
    mock_response.json.return_value = {
        "meta": {"rc": "ok"},
        "data": []
    }

    client.session = Mock()
    client.session.post.return_value = mock_response
    client.logged_in = True

    # Mock CSRF manager to avoid cookie issues
    client.csrf_manager = Mock()
    client.csrf_manager.token = "test_token_12345678901234567890"
    client.csrf_manager.update_token = Mock()

    test_data = {
        "cmd": "set-locate",
        "mac": "aa:bb:cc:dd:ee:ff"
    }

    # Capture console output
    with patch('api.cloudkey_gen2_client.console') as mock_console:
        result = client.post("s/default/cmd/devmgr", test_data)

        # Verify verbose logging was called
        assert mock_console.print.called, "Console print should be called in verbose mode"

        # Check that we logged the request with payload
        calls = [str(call) for call in mock_console.print.call_args_list]
        request_logged = any("POST Request" in str(call) for call in calls)
        assert request_logged, "POST request should be logged in verbose mode"

        # Check that payload was logged
        payload_logged = any("Payload" in str(call) for call in calls)
        assert payload_logged, "POST payload should be logged in verbose mode"

        print("   ✅ POST request verbose logging works")

    return True


def test_non_verbose_mode():
    """Test that verbose logging doesn't happen when verbose=False"""
    print("\n🧪 Testing non-verbose mode (should not log details)...")

    # Create client with verbose=False (default)
    client = CloudKeyGen2Client(
        host="https://192.168.1.1",
        username="test",
        password="test",
        site="default",
        verify_ssl=False,
        verbose=False
    )

    # Mock the session and response
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.cookies = {}  # Add cookies for CSRF manager
    mock_response.json.return_value = {"data": []}

    client.session = Mock()
    client.session.get.return_value = mock_response
    client.logged_in = True

    # Mock CSRF manager to avoid cookie issues
    client.csrf_manager = Mock()
    client.csrf_manager.token = None
    client.csrf_manager.update_token = Mock()

    # Capture console output
    with patch('api.cloudkey_gen2_client.console') as mock_console:
        result = client.get("s/default/stat/device")

        # In non-verbose mode, console.print should not be called
        # (only logger is used, not console)
        # If it is called, it should only be for errors, not for normal requests
        if mock_console.print.called:
            calls = [str(call) for call in mock_console.print.call_args_list]
            # Make sure it's not logging request/response details
            assert not any("GET Request" in str(call) for call in calls), \
                "GET request details should not be logged in non-verbose mode"

        print("   ✅ Non-verbose mode doesn't show request details")

    return True


def test_verbose_error_handling():
    """Test verbose logging for errors"""
    print("\n🧪 Testing verbose error logging...")

    # Create client with verbose=True
    client = CloudKeyGen2Client(
        host="https://192.168.1.1",
        username="test",
        password="test",
        site="default",
        verify_ssl=False,
        verbose=True
    )

    # Mock the session to raise an exception
    client.session = Mock()
    client.session.get.side_effect = Exception("Connection timeout")
    client.logged_in = True

    # Capture console output
    with patch('api.cloudkey_gen2_client.console') as mock_console:
        result = client.get("s/default/stat/device")

        # Should return None on error
        assert result is None, "Should return None on error"

        # Verify error logging was called
        assert mock_console.print.called, "Console print should be called for errors"

        # Check that error details were logged
        calls = [str(call) for call in mock_console.print.call_args_list]
        error_logged = any("Failed" in str(call) or "Error" in str(call) for call in calls)
        assert error_logged, "Error should be logged in verbose mode"

        print("   ✅ Error logging works in verbose mode")

    return True


def run_tests():
    """Run all verbose mode tests"""
    print("\n" + "="*70)
    print("🧪 VERBOSE MODE TEST SUITE")
    print("="*70)

    tests = [
        test_verbose_get_request,
        test_verbose_put_request,
        test_verbose_post_request,
        test_non_verbose_mode,
        test_verbose_error_handling,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            if test():
                passed += 1
        except AssertionError as e:
            failed += 1
            print(f"   ❌ FAILED: {e}")
        except Exception as e:
            failed += 1
            print(f"   ❌ ERROR: {e}")

    print("\n" + "="*70)
    print(f"📊 RESULTS: {passed} passed, {failed} failed")
    print("="*70 + "\n")

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
