#!/usr/bin/env python3
"""
CloudKey API Endpoint Test Script

This script tests all CloudKey API endpoints systematically to identify which ones
are working correctly and which ones may be causing issues.
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress
from rich.table import Table

# Try to use the rich library, fall back to simple printing if not available
try:
    console = Console()
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    print("Note: Install 'rich' package for better output")

# Add parent directory to path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from api.cloudkey_compat import detect_cloudkey_generation
from api.cloudkey_jwt_helper import extract_csrf_token_from_cookie, get_auth_cookies
from api.csrf_token_manager import get_csrf_token
from utils.helpers import normalize_host_url

# Common CloudKey API endpoints to test
ENDPOINTS = [
    # System Info
    {"path": "/api/system", "method": "GET", "description": "System Information"},
    {"path": "/api/system/info", "method": "GET", "description": "Detailed System Info"},

    # Network Configuration
    {"path": "/api/s/default/rest/networkconf", "method": "GET", "description": "Network Configuration"},
    {"path": "/api/s/default/rest/setting/mgmt", "method": "GET", "description": "Management Settings"},

    # Sites
    {"path": "/api/self/sites", "method": "GET", "description": "Available Sites"},
    {"path": "/api/stat/sites", "method": "GET", "description": "Site Statistics"},

    # Devices
    {"path": "/api/s/default/stat/device", "method": "GET", "description": "Device List"},
    {"path": "/api/s/default/stat/device-basic", "method": "GET", "description": "Basic Device Info"},

    # Clients
    {"path": "/api/s/default/stat/sta", "method": "GET", "description": "Client List"},
    {"path": "/api/s/default/rest/user", "method": "GET", "description": "User List"},

    # Health
    {"path": "/api/s/default/stat/health", "method": "GET", "description": "Health Stats"},
    {"path": "/api/s/default/stat/rogueap", "method": "GET", "description": "Rogue AP Detection"},

    # Dashboard
    {"path": "/api/s/default/stat/dashboard", "method": "GET", "description": "Dashboard Stats"},
    {"path": "/api/s/default/stat/report/daily.site", "method": "GET", "description": "Daily Site Report"},

    # Configuration
    {"path": "/api/s/default/rest/wlanconf", "method": "GET", "description": "WLAN Configuration"},
    {"path": "/api/s/default/rest/firewallgroup", "method": "GET", "description": "Firewall Groups"},
]

def print_header():
    """Print the script header"""
    if HAS_RICH:
        console.print(Panel.fit(
            "[bold blue]CloudKey API Endpoint Tester[/bold blue]",
            title="UniFi Audit Tool",
            subtitle="API Diagnostics"
        ))
    else:
        print("=" * 60)
        print("CloudKey API Endpoint Tester")
        print("=" * 60)

def authenticate(url, username, password):
    """Authenticate with the UniFi Controller"""
    login_url = f"{url}/api/login"

    # First get the CSRF token if needed
    session = requests.Session()

    # Try to detect the CloudKey generation
    generation = detect_cloudkey_generation(url, session)
    console.print(f"[bold]Detected CloudKey Generation:[/bold] {generation}")

    if generation >= 2:
        # For CloudKey Gen2+, we need to get a CSRF token first
        csrf_token = get_csrf_token(url, session)
        headers = {"X-CSRF-Token": csrf_token}
    else:
        headers = {}

    # Now attempt to login
    login_data = {"username": username, "password": password, "remember": True}

    try:
        response = session.post(login_url, json=login_data, headers=headers)
        response.raise_for_status()

        # Check for JWT cookies or other auth tokens
        if "unifises" in session.cookies:
            if HAS_RICH:
                console.print("[green]✅ Authentication successful[/green]")
            else:
                print("✅ Authentication successful")

            if generation >= 2:
                # Make sure we have the CSRF token for future requests
                csrf = extract_csrf_token_from_cookie(session)
                if csrf:
                    session.headers.update({"X-CSRF-Token": csrf})
            return session
        else:
            if HAS_RICH:
                console.print("[red]❌ Authentication failed: No session cookie received[/red]")
            else:
                print("❌ Authentication failed: No session cookie received")
            return None
    except requests.RequestException as e:
        if HAS_RICH:
            console.print(f"[red]❌ Authentication error: {str(e)}[/red]")
        else:
            print(f"❌ Authentication error: {str(e)}")
        return None

def test_endpoint(session, url, endpoint):
    """Test a specific API endpoint"""
    full_url = f"{url}{endpoint['path']}"
    method = endpoint['method'].lower()

    try:
        if method == 'get':
            response = session.get(full_url, timeout=10)
        elif method == 'post':
            response = session.post(full_url, json={}, timeout=10)
        else:
            return {
                "status": "error",
                "code": 0,
                "message": f"Unsupported method: {method}"
            }

        # Try to parse JSON response
        try:
            data = response.json()
        except:
            data = None

        return {
            "status": "success" if response.status_code < 400 else "error",
            "code": response.status_code,
            "message": "OK" if response.status_code == 200 else response.reason,
            "data": data,
            "headers": dict(response.headers)
        }
    except requests.RequestException as e:
        return {
            "status": "error",
            "code": 0,
            "message": str(e)
        }

def run_tests(url, username, password):
    """Run tests on all endpoints"""
    url = normalize_host_url(url)

    # Authenticate first
    session = authenticate(url, username, password)
    if not session:
        return

    # Create results table
    if HAS_RICH:
        table = Table(title="API Endpoint Test Results")
        table.add_column("Endpoint", style="cyan")
        table.add_column("Method", style="magenta")
        table.add_column("Description", style="blue")
        table.add_column("Status", style="green")
        table.add_column("Code", style="yellow")
        table.add_column("Message", style="white")

    results = []

    # Run tests with progress bar
    if HAS_RICH:
        with Progress() as progress:
            task = progress.add_task("[green]Testing endpoints...", total=len(ENDPOINTS))

            for endpoint in ENDPOINTS:
                result = test_endpoint(session, url, endpoint)

                # Add to table
                status_style = "green" if result["status"] == "success" else "red"
                table.add_row(
                    endpoint["path"],
                    endpoint["method"],
                    endpoint["description"],
                    f"[{status_style}]{result['status'].upper()}[/{status_style}]",
                    str(result["code"]),
                    result["message"]
                )

                # Store complete result
                results.append({
                    "endpoint": endpoint,
                    "result": result
                })

                # Update progress
                progress.update(task, advance=1)
                time.sleep(0.2)  # Slight delay to avoid rate limiting
    else:
        print("\nTesting endpoints...")
        for i, endpoint in enumerate(ENDPOINTS):
            print(f"  [{i+1}/{len(ENDPOINTS)}] {endpoint['path']}...", end="", flush=True)
            result = test_endpoint(session, url, endpoint)
            status_text = "✅ OK" if result["status"] == "success" else f"❌ {result['message']}"
            print(f" {status_text}")

            # Store result
            results.append({
                "endpoint": endpoint,
                "result": result
            })
            time.sleep(0.2)  # Slight delay to avoid rate limiting

    # Display results table
    if HAS_RICH:
        console.print("\n")
        console.print(table)

    # Calculate success rate
    success_count = sum(1 for r in results if r["result"]["status"] == "success")
    success_rate = (success_count / len(ENDPOINTS)) * 100

    if HAS_RICH:
        color = "green" if success_rate > 80 else "yellow" if success_rate > 50 else "red"
        console.print(f"\n[bold {color}]Success Rate: {success_rate:.1f}% ({success_count}/{len(ENDPOINTS)} endpoints)[/bold {color}]")
    else:
        print(f"\nSuccess Rate: {success_rate:.1f}% ({success_count}/{len(ENDPOINTS)} endpoints)")

    # Ask if user wants to save detailed results
    save = input("\nSave detailed results to file? (y/n): ").lower().strip() == "y"
    if save:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"api_test_results_{timestamp}.json"

        with open(filename, "w") as f:
            json.dump({
                "url": url,
                "timestamp": timestamp,
                "success_rate": success_rate,
                "results": results
            }, f, indent=2)

        if HAS_RICH:
            console.print(f"[green]Results saved to {filename}[/green]")
        else:
            print(f"Results saved to {filename}")

def main():
    """Main entry point"""
    print_header()

    # Get connection details
    url = input("Enter UniFi Controller URL (e.g. https://192.168.1.1:8443): ")
    username = input("Enter username: ")
    password = input("Enter password: ")

    # Run the tests
    run_tests(url, username, password)

if __name__ == "__main__":
    main()
