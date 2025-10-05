#!/usr/bin/env python3
"""
UniFi CloudKey API Diagnostic Tool

This script helps diagnose and troubleshoot API issues with UniFi CloudKey controllers.
It will perform test API calls with detailed logging to help identify issues.
"""
import json
import sys
import traceback
from pathlib import Path
from pprint import pprint

try:
    from rich.console import Console
    console = Console()
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    print("Rich library not available, using standard output")

# Add parent directory to path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from api.cloudkey_compat import detect_cloudkey_generation
from api.cloudkey_api_helper import get_devices, get_sites, get_clients
from api.csrf_token_manager import get_csrf_token
from utils.helpers import normalize_host_url

import requests
import time
import urllib3
import logging

# Disable SSL warnings for self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("cloudkey_api_debug.log"),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("cloudkey_api_diagnostic")

# API diagnostic tests to run
# Note: {site} will be replaced with the actual site name
DIAGNOSTIC_TESTS = [
    {
        "name": "System Info",
        "endpoint": "/api/system",
        "method": "GET",
        "expected_keys": ["timezone", "version", "hardware"]
    },
    {
        "name": "Login Check",
        "endpoint": "/api/login",
        "method": "POST",
        "payload": {"username": "{username}", "password": "{password}", "remember": True},
        "expected_status": 200
    },
    {
        "name": "Sites List",
        "endpoint": "/api/self/sites",
        "method": "GET",
        "auth_required": True,
        "expected_keys": ["data"]
    },
    {
        "name": "Device List",
        "endpoint": "/api/s/{site}/stat/device",
        "method": "GET",
        "auth_required": True,
        "expected_keys": ["data"]
    },
    {
        "name": "Client List",
        "endpoint": "/api/s/{site}/stat/sta",
        "method": "GET",
        "auth_required": True,
        "expected_keys": ["data"]
    },
    {
        "name": "WLAN Config",
        "endpoint": "/api/s/{site}/rest/wlanconf",
        "method": "GET",
        "auth_required": True,
        "expected_keys": ["data"]
    }
]

def print_header():
    """Print diagnostic header"""
    if HAS_RICH:
        console.print("[bold blue]====================================[/bold blue]")
        console.print("[bold blue]UniFi CloudKey API Diagnostic Tool[/bold blue]")
        console.print("[bold blue]====================================[/bold blue]")
    else:
        print("====================================")
        print("UniFi CloudKey API Diagnostic Tool")
        print("====================================")

def print_result(test_name, success, message, details=None):
    """Print test result with formatting"""
    if HAS_RICH:
        status = "[bold green]PASSED[/bold green]" if success else "[bold red]FAILED[/bold red]"
        console.print(f"[bold]{test_name}[/bold]: {status} - {message}")
        if details and not success:
            console.print("[dim]Details:[/dim]")
            console.print_json(json.dumps(details))
    else:
        status = "✅ PASSED" if success else "❌ FAILED"
        print(f"{test_name}: {status} - {message}")
        if details and not success:
            print("Details:")
            pprint(details)

def authenticate(session, url, username, password):
    """Authenticate with the UniFi Controller"""
    logger.info("Attempting authentication")
    login_url = f"{url}/api/login"

    # Detect CloudKey generation
    generation = detect_cloudkey_generation(url, session)
    logger.info(f"Detected CloudKey generation: {generation}")

    # Handle CSRF requirements for Gen2+
    headers = {}
    if generation >= 2:
        logger.info("CloudKey Gen2+ detected, getting CSRF token")
        csrf_token = get_csrf_token(url, session)
        if csrf_token:
            headers = {"X-CSRF-Token": csrf_token}
            logger.info("CSRF token obtained successfully")
        else:
            logger.warning("Failed to get CSRF token")

    # Attempt login
    login_data = {"username": username, "password": password, "remember": True}

    try:
        logger.debug(f"Sending login request to {login_url}")
        response = session.post(login_url, json=login_data, headers=headers, verify=False)
        logger.debug(f"Login response status: {response.status_code}")

        # Log headers and cookies for debugging
        logger.debug("Response Headers:")
        for k, v in response.headers.items():
            logger.debug(f"  {k}: {v}")

        logger.debug("Cookies:")
        for cookie in session.cookies:
            logger.debug(f"  {cookie.name}: {cookie.value[:10]}...")

        if response.status_code == 200:
            # Check if we got a session cookie
            if "unifises" in session.cookies:
                logger.info("Authentication successful")

                # If Gen2+, update CSRF token from cookie if needed
                if generation >= 2:
                    csrf_cookie = session.cookies.get("csrf_token")
                    if csrf_cookie:
                        csrf_value = csrf_cookie.value
                        session.headers.update({"X-CSRF-Token": csrf_value})
                        logger.info("Updated CSRF token from cookie")

                return True, "Authentication successful"
            else:
                logger.error("No session cookie received after successful login response")
                return False, "No session cookie received"
        else:
            logger.error(f"Authentication failed with status code {response.status_code}")
            error_msg = "Unknown error"
            try:
                error_data = response.json()
                error_msg = error_data.get("meta", {}).get("msg", "Unknown error")
            except:
                pass
            return False, f"Authentication failed: {error_msg}"

    except Exception as e:
        logger.error(f"Authentication exception: {str(e)}")
        logger.error(traceback.format_exc())
        return False, f"Authentication error: {str(e)}"

def run_diagnostic_test(session, url, test, username, password):
    """Run a single diagnostic test"""
    logger.info(f"Running diagnostic test: {test['name']}")

    # Replace payload placeholders if any
    if "payload" in test:
        payload = {}
        for k, v in test["payload"].items():
            if isinstance(v, str):
                if "{username}" in v:
                    v = v.replace("{username}", username)
                if "{password}" in v:
                    v = v.replace("{password}", password)
            payload[k] = v
    else:
        payload = None

    # Build the full URL
    full_url = f"{url}{test['endpoint']}"

    try:
        logger.debug(f"Sending {test['method']} request to {full_url}")

        # Make the request
        if test["method"] == "GET":
            response = session.get(full_url, verify=False, timeout=15)
        elif test["method"] == "POST":
            response = session.post(full_url, json=payload, verify=False, timeout=15)
        else:
            logger.error(f"Unsupported method: {test['method']}")
            return False, f"Unsupported method: {test['method']}", None

        # Log response details
        logger.debug(f"Response status code: {response.status_code}")

        # Check status code
        if "expected_status" in test and response.status_code != test["expected_status"]:
            logger.error(f"Expected status {test['expected_status']} but got {response.status_code}")
            return False, f"Expected status {test['expected_status']} but got {response.status_code}", response

        # Try to parse response as JSON
        try:
            data = response.json()

            # Check for expected keys
            if "expected_keys" in test:
                missing_keys = []
                for key in test["expected_keys"]:
                    if key not in data:
                        missing_keys.append(key)

                if missing_keys:
                    logger.error(f"Missing expected keys: {', '.join(missing_keys)}")
                    return False, f"Missing expected keys: {', '.join(missing_keys)}", data

            logger.info(f"Test {test['name']} passed")
            return True, "Test passed", data
        except ValueError as e:
            logger.error(f"Response is not valid JSON: {str(e)}")
            return False, "Response is not valid JSON", response.text

    except Exception as e:
        logger.error(f"Test exception: {str(e)}")
        logger.error(traceback.format_exc())
        return False, f"Test error: {str(e)}", None

def run_diagnostics(url, username, password, site="default"):
    """Run all diagnostic tests"""
    url = normalize_host_url(url)
    logger.info(f"Starting diagnostics for {url} (site: {site})")

    session = requests.Session()

    # First, authenticate
    auth_success, auth_message = authenticate(session, url, username, password)
    print_result("Authentication", auth_success, auth_message)

    if not auth_success:
        return

    # Replace {site} placeholder in test endpoints
    diagnostic_tests = []
    for test in DIAGNOSTIC_TESTS:
        test_copy = test.copy()
        if "endpoint" in test_copy:
            test_copy["endpoint"] = test["endpoint"].replace("{site}", site)
        diagnostic_tests.append(test_copy)

    # Run each diagnostic test
    results = []
    all_passed = True

    for test in diagnostic_tests:
        # Skip the login test since we've already tested authentication
        if test["name"] == "Login Check":
            continue

        # Skip tests requiring auth if auth failed
        if test.get("auth_required", False) and not auth_success:
            continue

        success, message, details = run_diagnostic_test(session, url, test, username, password)
        print_result(test["name"], success, message, details)

        if not success:
            all_passed = False

        results.append({
            "test": test["name"],
            "success": success,
            "message": message,
        })

        # Brief delay to avoid rate limiting
        time.sleep(0.5)

    # Summary
    if HAS_RICH:
        console.print("\n[bold]Diagnostic Summary[/bold]")
        if all_passed:
            console.print("[bold green]All tests passed! Your CloudKey API appears to be working correctly.[/bold green]")
        else:
            console.print("[bold yellow]Some tests failed. See above for details.[/bold yellow]")
    else:
        print("\nDiagnostic Summary")
        if all_passed:
            print("All tests passed! Your CloudKey API appears to be working correctly.")
        else:
            print("Some tests failed. See above for details.")

    # Save log file location
    logger.info("Diagnostic complete")
    if HAS_RICH:
        console.print(f"\nDetailed logs written to [blue]cloudkey_api_debug.log[/blue]")
    else:
        print("\nDetailed logs written to cloudkey_api_debug.log")

def main():
    """Main entry point"""
    print_header()

    # Get connection details
    url = input("Enter UniFi Controller URL (e.g. https://192.168.1.1:8443): ")
    username = input("Enter username: ")
    password = input("Enter password: ")
    site = input("Enter site name [default]: ").strip() or "default"

    # Run diagnostics
    run_diagnostics(url, username, password, site)

if __name__ == "__main__":
    main()
