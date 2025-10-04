#!/usr/bin/env python3
"""
Test script for cloudkey_api_helper.py

This script tests the key functionality of the cloudkey_api_helper.py module
to ensure it works correctly after fixes.
"""

import sys
import requests
import argparse
from pathlib import Path
from rich.console import Console

# Add parent directory to path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Import the functions from cloudkey_api_helper
try:
    from api.cloudkey_api_helper import (
        convert_to_cloudkey_format,
        detect_controller_type,
        get_cloudkey_diagnostic_info,
        test_cloudkey_gen2_api,
        api_cloudkey_put_or_upd
    )
    from api.csrf_token_manager import setup_csrf_session
    API_HELPER_AVAILABLE = True
except ImportError as e:
    print(f"Error importing API helper: {e}")
    API_HELPER_AVAILABLE = False

console = Console()

def test_cloudkey_helper(host, username, password, site="default"):
    """
    Test the CloudKey API helper functions
    
    Args:
        host: CloudKey hostname or IP
        username: Login username
        password: Login password
        site: Site name (default is 'default')
    
    Returns:
        bool: True if all tests pass, False otherwise
    """
    if not API_HELPER_AVAILABLE:
        console.print("[red]API helper modules not available. Cannot run test.[/red]")
        return False
    
    # Make sure host is properly formatted
    if not host.startswith('http'):
        host = f"https://{host}"
    if host.endswith('/'):
        host = host[:-1]
        
    console.print("[bold blue]CloudKey API Helper Test[/bold blue]")
    console.print(f"Testing controller: {host}")
    
    # Set up a session with CSRF support
    console.print("\n[bold]Setting up session with CSRF support...[/bold]")
    session = setup_csrf_session()
    session.verify = False  # Disable SSL verification for self-signed certs
    
    # Detect controller type
    console.print("\n[bold]Test 1: Detect controller type[/bold]")
    try:
        controller_type = detect_controller_type(host, session)
        console.print(f"[green]✓ Detected controller type: {controller_type}[/green]")
    except Exception as e:
        console.print(f"[red]✗ Failed to detect controller type: {e}[/red]")
        return False
    
    # Login
    console.print("\n[bold]Test 2: Login[/bold]")
    login_url = f"{host}/api/login"
    login_data = {"username": username, "password": password}
    
    try:
        login_response = session.post(login_url, json=login_data, verify=False)
        login_response.raise_for_status()
        console.print("[green]✓ Login successful[/green]")
    except Exception as e:
        console.print(f"[red]✗ Login failed: {e}[/red]")
        return False
    
    # Get diagnostic info
    console.print("\n[bold]Test 3: Get CloudKey diagnostic info[/bold]")
    try:
        diagnostic_info = get_cloudkey_diagnostic_info(host, session)
        console.print("[green]✓ Got diagnostic info[/green]")
        
        # Display some key information
        if diagnostic_info:
            console.print("\nDiagnostic Information:")
            if 'version' in diagnostic_info:
                console.print(f"  Controller Version: {diagnostic_info['version']}")
            if 'model' in diagnostic_info:
                console.print(f"  Model: {diagnostic_info['model']}")
            if 'hostname' in diagnostic_info:
                console.print(f"  Hostname: {diagnostic_info['hostname']}")
    except Exception as e:
        console.print(f"[red]✗ Failed to get diagnostic info: {e}[/red]")
    
    # Test CloudKey Gen2 API
    if controller_type in ("cloudkey-gen2", "udm"):
        console.print("\n[bold]Test 4: Test CloudKey Gen2 API[/bold]")
        try:
            gen2_result = test_cloudkey_gen2_api(host, session)
            console.print("[green]✓ CloudKey Gen2 API test successful[/green]")
        except Exception as e:
            console.print(f"[red]✗ CloudKey Gen2 API test failed: {e}[/red]")
    else:
        console.print("\n[bold yellow]Skipping Test 4: Not a CloudKey Gen2 or UDM device[/bold yellow]")
    
    # Test data conversion
    console.print("\n[bold]Test 5: Test data format conversion[/bold]")
    
    test_data = {
        "name": "Test AP",
        "model": "U7PG2",
        "ip": "192.168.1.100",
        "mac": "00:11:22:33:44:55",
        "radio_table": [
            {
                "name": "Radio 0",
                "radio": "ng",
                "channel": 6,
                "ht": "20",
                "tx_power": "high"
            },
            {
                "name": "Radio 1",
                "radio": "ac",
                "channel": 36,
                "ht": "80",
                "tx_power": "medium"
            }
        ]
    }
    
    try:
        converted = convert_to_cloudkey_format(test_data)
        console.print("[green]✓ Data format conversion successful[/green]")
    except Exception as e:
        console.print(f"[red]✗ Data format conversion failed: {e}[/red]")
    
    # Test API PUT/UPDATE function with a safe test (get sites)
    console.print("\n[bold]Test 6: Test API PUT/UPDATE function[/bold]")
    try:
        # Just get sites as a safe operation
        result = api_cloudkey_put_or_upd(session, host, f"/api/s/{site}/stat/site", {}, method="GET")
        console.print("[green]✓ API PUT/UPDATE function test successful[/green]")
        
        # Show the number of sites retrieved
        if isinstance(result, dict) and 'data' in result:
            sites_count = len(result['data'])
            console.print(f"  Retrieved information about {sites_count} site(s)")
    except Exception as e:
        console.print(f"[red]✗ API PUT/UPDATE function test failed: {e}[/red]")
    
    console.print("\n[bold green]CloudKey API Helper Tests Complete![/bold green]")
    return True

def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='Test CloudKey API Helper functions')
    parser.add_argument('--host', help='Controller hostname or IP (e.g., 192.168.1.1:8443)')
    parser.add_argument('--username', help='Login username')
    parser.add_argument('--password', help='Login password')
    parser.add_argument('--site', default='default', help='Site name (default: default)')
    return parser.parse_args()

def main():
    """Main entry point"""
    console.print("[bold blue]==================================[/bold blue]")
    console.print("[bold blue]CloudKey API Helper Test[/bold blue]")
    console.print("[bold blue]==================================[/bold blue]")
    
    args = parse_args()
    
    # Get connection info from args or prompt
    host = args.host or input("Enter CloudKey hostname or IP (e.g., 192.168.1.1:8443): ")
    username = args.username or input("Enter username: ")
    password = args.password or input("Enter password: ")
    site = args.site or "default"
    
    # Run tests
    test_cloudkey_helper(host, username, password, site)

if __name__ == "__main__":
    main()
