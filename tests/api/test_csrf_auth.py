#!/usr/bin/env python3
"""
CloudKey CSRF Token Test Script
This script tests the CSRF token handling for CloudKey Gen2 controllers
"""

import requests
import sys
import time
from pathlib import Path
from rich.console import Console

# Add parent directory to path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from api.cloudkey_api_helper import get_devices, get_clients
from api.csrf_token_manager import csrf_manager, setup_csrf_session

console = Console()

def test_csrf_auth(host, username, password, site="default"):
    """
    Test CSRF token authentication with the CloudKey Gen2.
    
    This test function:
    1. Authenticates with the controller
    2. Tests API calls with CSRF token handling
    3. Tests token refresh
    
    Args:
        host: Controller hostname or IP
        username: Login username
        password: Login password
        site: Site name (default is 'default')
    
    Returns:
        bool: True if all tests pass, False otherwise
    """
    # Make sure host is properly formatted
    if not host.startswith('http'):
        host = f"https://{host}"
    if host.endswith('/'):
        host = host[:-1]
    
    console.print("\n[bold blue]CSRF Authentication Test[/bold blue]")
    console.print(f"Testing controller: {host}")
    
    # 1. Test setup_csrf_session and login
    console.print("\n[bold]Test 1: Create session with CSRF handling[/bold]")
    session = setup_csrf_session()
    
    if session:
        console.print("[green]✓ Session created with CSRF handling[/green]")
    else:
        console.print("[red]✗ Failed to create session[/red]")
        return False
    
    # 2. Test login with CSRF handling
    console.print("\n[bold]Test 2: Login with CSRF handling[/bold]")
    login_url = f"{host}/api/login"
    login_data = {"username": username, "password": password}
    
    try:
        login_response = session.post(login_url, json=login_data, verify=False)
        login_response.raise_for_status()
        console.print("[green]✓ Login successful[/green]")
        
        # Check if CSRF token was detected
        if csrf_manager.token:
            console.print(f"[green]✓ CSRF token detected: {csrf_manager.token[:10]}...[/green]")
            console.print(f"  Token sources: {', '.join(csrf_manager.token_sources)}")
        else:
            console.print("[yellow]! No CSRF token detected. This may be normal for non-Gen2 controllers[/yellow]")
        
    except requests.exceptions.HTTPError as e:
        console.print(f"[red]✗ Login failed with HTTP error: {e}[/red]")
        try:
            error_json = login_response.json()
            console.print(f"  Error message: {error_json.get('meta', {}).get('msg', 'Unknown error')}")
        except:
            console.print(f"  Status code: {login_response.status_code}")
        return False
    except Exception as e:
        console.print(f"[red]✗ Login failed with error: {e}[/red]")
        return False
    
    # 3. Test GET request with CSRF handling
    console.print("\n[bold]Test 3: GET request with CSRF handling[/bold]")
    sites_url = f"{host}/api/self/sites"
    
    try:
        sites_response = session.get(sites_url, verify=False)
        sites_response.raise_for_status()
        
        sites_data = sites_response.json()
        sites_count = len(sites_data.get('data', []))
        
        console.print(f"[green]✓ GET request successful: retrieved {sites_count} sites[/green]")
    except Exception as e:
        console.print(f"[red]✗ GET request failed: {e}[/red]")
        return False
    
    # 4. Test POST request with CSRF handling
    console.print("\n[bold]Test 4: POST request with CSRF handling[/bold]")
    stat_url = f"{host}/api/s/{site}/stat/report/daily.site"
    
    # Prepare request data (date range for last 7 days)
    now = int(time.time())
    week_ago = now - (7 * 24 * 60 * 60)
    
    stat_data = {
        "attrs": ["bytes", "wan-tx_bytes", "wan-rx_bytes", "wlan_bytes", "num_sta", "lan-num_sta", "wlan-num_sta", "time"],
        "start": week_ago,
        "end": now
    }
    
    try:
        stat_response = session.post(stat_url, json=stat_data, verify=False)
        stat_response.raise_for_status()
        
        stat_result = stat_response.json()
        data_points = len(stat_result.get('data', []))
        
        console.print(f"[green]✓ POST request successful: retrieved {data_points} data points[/green]")
    except Exception as e:
        console.print(f"[red]✗ POST request failed: {e}[/red]")
        return False
    
    # 5. Test API helper functions
    console.print("\n[bold]Test 5: API helper functions with CSRF handling[/bold]")
    
    try:
        # Get devices
        devices = get_devices(session, host, site)
        console.print(f"[green]✓ Retrieved {len(devices)} devices[/green]")
        
        # Get clients
        clients = get_clients(session, host, site)
        console.print(f"[green]✓ Retrieved {len(clients)} clients[/green]")
    except Exception as e:
        console.print(f"[red]✗ API helper functions failed: {e}[/red]")
        return False
    
    # 6. Test CSRF token refresh
    console.print("\n[bold]Test 6: CSRF token refresh[/bold]")
    
    # Save the current token
    original_token = csrf_manager.token
    console.print(f"Original token: {original_token[:10]}...")
    
    # Make a new login request to force token refresh
    try:
        refresh_response = session.post(login_url, json=login_data, verify=False)
        refresh_response.raise_for_status()
        
        # Check if token was refreshed
        if csrf_manager.token != original_token:
            console.print(f"[green]✓ CSRF token refreshed: {csrf_manager.token[:10]}...[/green]")
        else:
            console.print("[yellow]! CSRF token remained the same after refresh attempt[/yellow]")
    except Exception as e:
        console.print(f"[red]✗ Token refresh failed: {e}[/red]")
        return False
    
    # 7. Test another request after token refresh
    console.print("\n[bold]Test 7: API request after token refresh[/bold]")
    
    try:
        after_refresh_response = session.get(f"{host}/api/s/{site}/rest/setting/mgmt", verify=False)
        after_refresh_response.raise_for_status()
        
        console.print("[green]✓ Request after token refresh successful[/green]")
    except Exception as e:
        console.print(f"[red]✗ Request after token refresh failed: {e}[/red]")
        return False
    
    # 8. Deliberately try with invalid CSRF token
    console.print("\n[bold]Test 8: Request with invalid CSRF token (should fail)[/bold]")
    
    # Save the current token and replace it with an invalid one
    valid_token = csrf_manager.token
    csrf_manager.token = "invalid_csrf_token_for_testing"
    session.headers.update({"X-CSRF-Token": csrf_manager.token})
    
    try:
        # This request should fail with a 403 error
        invalid_response = session.post(stat_url, json=stat_data, verify=False)
        
        if invalid_response.status_code == 403:
            console.print("[green]✓ Request with invalid token correctly failed with 403 error[/green]")
        else:
            console.print(f"[yellow]! Request with invalid token returned status {invalid_response.status_code} (expected 403)[/yellow]")
    except Exception as e:
        console.print(f"[yellow]! Request with invalid token failed with exception: {e}[/yellow]")
    
    # Restore the valid token
    csrf_manager.token = valid_token
    session.headers.update({"X-CSRF-Token": csrf_manager.token})
    
    # 9. Final verification request
    console.print("\n[bold]Test 9: Final verification request[/bold]")
    
    try:
        final_response = session.get(f"{host}/api/s/{site}/stat/health", verify=False)
        final_response.raise_for_status()
        
        health_data = final_response.json()
        health_count = len(health_data.get('data', []))
        
        console.print(f"[green]✓ Final request successful: retrieved {health_count} health items[/green]")
    except Exception as e:
        console.print(f"[red]✗ Final request failed: {e}[/red]")
        return False
    
    # All tests passed
    console.print("\n[bold green]All CSRF authentication tests passed![/bold green]")
    return True

def main():
    """Main entry point"""
    console.print("[bold blue]==================================[/bold blue]")
    console.print("[bold blue]CloudKey CSRF Token Authentication Test[/bold blue]")
    console.print("[bold blue]==================================[/bold blue]")
    
    # Get connection info
    host = input("Enter CloudKey hostname or IP (e.g., 192.168.1.1:8443): ")
    username = input("Enter username: ")
    password = input("Enter password: ")
    site = input("Enter site name (default is 'default'): ") or "default"
    
    # Run test
    test_csrf_auth(host, username, password, site)

if __name__ == "__main__":
    main()
