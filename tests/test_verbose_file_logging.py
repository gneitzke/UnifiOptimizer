#!/usr/bin/env python3
"""
Test verbose logging to file
Verifies that API request/response details are written to log file
"""

import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import Mock
from api.cloudkey_gen2_client import CloudKeyGen2Client


def test_verbose_file_logging():
    """Test that verbose mode writes details to log file"""
    print("\n" + "=" * 70)
    print("Testing Verbose File Logging")
    print("=" * 70 + "\n")

    # Create client with verbose=True
    client = CloudKeyGen2Client(
        host="https://192.168.1.1",
        username="test",
        password="test",
        site="default",
        verify_ssl=False,
        verbose=True
    )

    # Get the log filename
    log_filename = None
    if client.logger and client.logger.handlers:
        handler = client.logger.handlers[0]
        if hasattr(handler, 'baseFilename'):
            log_filename = handler.baseFilename  # type: ignore
            print(f"Log file: {log_filename}")

    # Mock a successful GET response with data
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.cookies = {}
    mock_response.json.return_value = {
        "meta": {"rc": "ok"},
        "data": [
            {
                "device_id": "abc123",
                "name": "Test AP",
                "model": "U6-Pro",
                "ip": "192.168.1.5",
                "state": 1,
                "adopted": True
            },
            {
                "device_id": "def456",
                "name": "Test AP 2",
                "model": "U6-LR",
                "ip": "192.168.1.6",
                "state": 1,
                "adopted": True
            }
        ]
    }

    client.session = Mock()
    client.session.get.return_value = mock_response
    client.logged_in = True
    client.csrf_manager = Mock()
    client.csrf_manager.token = "test_token_1234567890"
    client.csrf_manager.update_token = Mock()

    # Make a GET request
    print("\n1. Making GET request...")
    result = client.get("s/default/stat/device")
    assert result is not None
    assert len(result["data"]) == 2
    print("   ✓ GET request successful")

    # Mock a successful PUT response
    mock_put_response = Mock()
    mock_put_response.status_code = 200
    mock_put_response.cookies = {}
    mock_put_response.json.return_value = {
        "meta": {"rc": "ok"},
        "data": [{"device_id": "abc123", "state": 1, "channel": 36}]
    }

    client.session.put.return_value = mock_put_response

    # Make a PUT request
    print("\n2. Making PUT request...")
    put_data = {"radio_table": [{"channel": 36, "ht": "80"}]}
    result = client.put("s/default/rest/device/abc123", put_data)
    assert result is not None
    print("   ✓ PUT request successful")

    # Mock a successful POST response
    mock_post_response = Mock()
    mock_post_response.status_code = 200
    mock_post_response.cookies = {}
    mock_post_response.json.return_value = {
        "meta": {"rc": "ok"},
        "data": []
    }

    client.session.post.return_value = mock_post_response

    # Make a POST request
    print("\n3. Making POST request...")
    post_data = {"cmd": "set-locate", "mac": "aa:bb:cc:dd:ee:ff"}
    result = client.post("s/default/cmd/devmgr", post_data)
    assert result is not None
    print("   ✓ POST request successful")

    # Give logger time to flush
    time.sleep(0.1)

    # Read and verify log file contents
    if log_filename:
        print(f"\n4. Checking log file: {log_filename}")
        with open(log_filename, 'r') as f:
            log_contents = f.read()

        # Check for GET request details
        assert "→ GET Request" in log_contents, "GET request marker not found in log"
        assert "s/default/stat/device" in log_contents, "GET path not found in log"
        print("   ✓ GET request details logged")

        # Check for GET response details
        assert "← Response (200)" in log_contents, "GET response marker not found in log"
        assert "Data items: 2" in log_contents, "GET response data count not found in log"
        assert '"device_id": "abc123"' in log_contents, "GET response data not found in log"
        print("   ✓ GET response data logged with JSON")

        # Check for PUT request details
        assert "→ PUT Request" in log_contents, "PUT request marker not found in log"
        assert '"radio_table"' in log_contents, "PUT payload not found in log"
        assert '"channel": 36' in log_contents, "PUT payload details not found in log"
        print("   ✓ PUT request payload logged with JSON")

        # Check for PUT response details
        put_response_count = log_contents.count("← Response (200)")
        assert put_response_count >= 2, "PUT response marker not found in log"
        print("   ✓ PUT response data logged")

        # Check for POST request details
        assert "→ POST Request" in log_contents, "POST request marker not found in log"
        assert '"cmd": "set-locate"' in log_contents, "POST payload not found in log"
        print("   ✓ POST request payload logged with JSON")

        # Check for POST response details
        post_response_count = log_contents.count("← Response (200)")
        assert post_response_count >= 3, "POST response marker not found in log"
        print("   ✓ POST response data logged")

        # Check for CSRF token (should be truncated)
        assert "CSRF Token:" in log_contents, "CSRF token not found in log"
        assert "test_token_1234567890" not in log_contents, "Full CSRF token should not be logged"
        assert "test_token_12345678" in log_contents, "Truncated CSRF token not found in log"
        print("   ✓ CSRF token logged (truncated for security)")

        # Show sample of log file
        print("\n5. Sample log file contents:")
        print("-" * 70)
        lines = log_contents.split('\n')
        # Show first few lines and some response lines
        for i, line in enumerate(lines[:40]):
            print(line)
        print("-" * 70)

    print("\n" + "=" * 70)
    print("✅ All verbose file logging tests passed!")
    print("=" * 70 + "\n")
    return True


if __name__ == "__main__":
    try:
        test_verbose_file_logging()
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)
