#!/usr/bin/env python3
"""
CloudKey Gen2 Specific Test

This script is specifically designed to test POST requests on CloudKey Gen2 devices.
It uses a different approach for API endpoints and payload formatting.
"""

import sys
import time
import json
import requests
import urllib3
from rich.console import Console
from rich.panel import Panel
import re
from pathlib import Path

# Add parent directory to path to enable imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Suppress SSL warnings for self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

console = Console()

def normalize_url(url):
    """Normalize URL to include https:// if not present"""
    if not url.startswith(('http://', 'https://')):
        return f"https://{url}"
    return url

def extract_token_from_cookie(cookies):
    """Extract auth token from cookies"""
    for cookie in cookies:
        if cookie.name == 'TOKEN':
            return cookie.value
    return None

def test_cloudkey_gen2(host, username, password, site='default'):
    """Test CloudKey Gen2 API"""
    host = normalize_url(host)
    
    console.print(Panel(f"[bold]CloudKey Gen2 API Test[/bold]\nHost: {host}\nSite: {site}", 
                  title="CloudKey Gen2", border_style="green"))
    
    # Step 1: Create session
    console.print("[bold cyan]Step 1: Creating session...[/bold cyan]")
    session = requests.Session()
    session.verify = False
    
    # Step 2: Authenticate
    console.print("\n[bold cyan]Step 2: Authenticating...[/bold cyan]")
    try:
        login_url = f"{host}/api/auth/login"
        login_data = {
            "username": username,
            "password": password,
            "remember": True
        }
        
        console.print(f"Trying login endpoint: [yellow]{login_url}[/yellow]")
        resp = session.post(login_url, json=login_data)
        
        if resp.status_code == 200:
            console.print("[green]✓ Authentication successful![/green]")
            # Extract token for debugging
            token = extract_token_from_cookie(session.cookies)
            if token:
                console.print(f"[dim]Token found: {token[:10]}...[/dim]")
            
            # Print cookies for debugging
            console.print("\nCookies:")
            for cookie in session.cookies:
                console.print(f"  [dim]{cookie.name}: {cookie.value[:10]}...[/dim]")
        else:
            console.print(f"[red]✗ Authentication failed with status code {resp.status_code}[/red]")
            console.print(f"Response: {resp.text[:200]}...")
            return False
    except Exception as e:
        console.print(f"[red]✗ Authentication error: {str(e)}[/red]")
        return False
    
    # Step 3: Test GET requests
    console.print("\n[bold cyan]Step 3: Testing GET requests...[/bold cyan]")
    get_endpoints = [
        {"url": f"{host}/api/self", "name": "Self Info"},
        {"url": f"{host}/api/self/sites", "name": "Sites"},
        {"url": f"{host}/api/s/{site}/stat/device", "name": "Devices"},
        {"url": f"{host}/api/s/{site}/stat/sta", "name": "Clients"},
        {"url": f"{host}/api/s/{site}/stat/health", "name": "Health Stats"}
    ]
    
    for endpoint in get_endpoints:
        try:
            console.print(f"\nTesting GET [yellow]{endpoint['name']}[/yellow]...")
            resp = session.get(endpoint["url"])
            
            if resp.status_code == 200:
                data = resp.json()
                console.print(f"[green]✓ Success: {endpoint['name']}[/green]")
                
                # Print summary of data
                if "data" in data and isinstance(data["data"], list):
                    console.print(f"  Items: {len(data['data'])}")
                    if len(data['data']) > 0:
                        sample = data['data'][0]
                        # Print sample keys
                        console.print(f"  Sample keys: {', '.join(list(sample.keys())[:5])}...")
                        
                        # For devices, print model names
                        if endpoint["name"] == "Devices" and "model" in sample:
                            models = [d.get("model", "unknown") for d in data["data"][:5]]
                            console.print(f"  Models: {', '.join(models)}")
                        
                        # For clients, print hostnames/names
                        if endpoint["name"] == "Clients" and "hostname" in sample:
                            hostnames = [c.get("hostname", c.get("name", "unknown")) for c in data["data"][:5]]
                            console.print(f"  Hostnames: {', '.join(hostnames)}")
                else:
                    console.print(f"  Data format: {list(data.keys())}")
            else:
                console.print(f"[red]✗ Failed: {endpoint['name']} - Status {resp.status_code}[/red]")
                console.print(f"  Response: {resp.text[:100]}...")
        except Exception as e:
            console.print(f"[red]✗ Error testing {endpoint['name']}: {str(e)}[/red]")
    
    # Step 4: Test POST requests
    console.print("\n[bold cyan]Step 4: Testing POST requests (Gen2 specific)...[/bold cyan]")
    
    # Test listing clients with specific attributes
    try:
        console.print("\nTesting POST client list with attributes...")
        
        payload = {
            "attrs": ["hostname", "ip", "mac", "name", "noted", "oui", "is_wired", "last_seen", 
                      "network_id", "radio_name", "rssi", "signal", "satisfaction"]
        }
        
        endpoint = f"{host}/api/s/{site}/stat/sta"
        resp = session.post(endpoint, json=payload)
        
        if resp.status_code == 200:
            data = resp.json()
            console.print(f"[green]✓ Success: Client list with attributes[/green]")
            
            if "data" in data and isinstance(data["data"], list):
                console.print(f"  Clients: {len(data['data'])}")
                if len(data['data']) > 0:
                    # Check if all requested attributes are present
                    sample = data['data'][0]
                    missing_attrs = [attr for attr in payload["attrs"] if attr not in sample]
                    
                    if missing_attrs:
                        console.print(f"  [yellow]Note: Some requested attributes are missing: {', '.join(missing_attrs)}[/yellow]")
                    else:
                        console.print(f"  [green]All requested attributes are present[/green]")
                        
                    # Show RSSI for wireless clients
                    wireless_clients = [c for c in data["data"] if not c.get("is_wired", False)]
                    if wireless_clients:
                        rssi_values = [c.get("rssi", "N/A") for c in wireless_clients[:3]]
                        console.print(f"  Sample RSSI values: {rssi_values}")
        else:
            console.print(f"[red]✗ Failed: Client list with attributes - Status {resp.status_code}[/red]")
            console.print(f"  Response: {resp.text[:100]}...")
    except Exception as e:
        console.print(f"[red]✗ Error testing client list with attributes: {str(e)}[/red]")
    
    # Test 2: Device configuration
    try:
        console.print("\nTesting device listing with config...")
        
        # First get a device ID
        device_resp = session.get(f"{host}/api/s/{site}/stat/device")
        device_data = device_resp.json()
        
        if "data" in device_data and len(device_data["data"]) > 0:
            device_id = device_data["data"][0]["_id"]
            
            # Now try to get config for this device
            console.print(f"  Got device ID: {device_id}")
            
            config_endpoint = f"{host}/api/s/{site}/rest/device/{device_id}"
            config_resp = session.get(config_endpoint)
            
            if config_resp.status_code == 200:
                config_data = config_resp.json()
                console.print(f"[green]✓ Success: Device configuration[/green]")
                
                if "data" in config_data and len(config_data["data"]) > 0:
                    config = config_data["data"][0]
                    console.print(f"  Model: {config.get('model', 'unknown')}")
                    console.print(f"  Version: {config.get('version', 'unknown')}")
                    
                    # Check for radio configs
                    if "radio_table" in config:
                        console.print("  Radio configuration:")
                        for radio in config["radio_table"]:
                            console.print(f"    - {radio.get('name', 'unknown')}: "
                                        f"Channel {radio.get('channel', 'auto')}, "
                                        f"TX Power {radio.get('tx_power', 'auto')}")
                else:
                    console.print("  [yellow]No configuration data found[/yellow]")
            else:
                console.print(f"[red]✗ Failed: Device configuration - Status {config_resp.status_code}[/red]")
                console.print(f"  Response: {config_resp.text[:100]}...")
        else:
            console.print("[yellow]No devices found to test configuration endpoint[/yellow]")
    except Exception as e:
        console.print(f"[red]✗ Error testing device configuration: {str(e)}[/red]")
    
    # Step 5: Test CSRF token handling
    console.print("\n[bold cyan]Step 5: Testing CSRF token handling...[/bold cyan]")
    
    # Check if there's a CSRF token in the cookies
    csrf_token = None
    for cookie in session.cookies:
        if re.match(r'(csrf|xsrf)', cookie.name, re.IGNORECASE):
            csrf_token = cookie.value
            console.print(f"[green]✓ Found CSRF token in cookie: {cookie.name}[/green]")
            break
    
    if not csrf_token:
        console.print("[yellow]No CSRF token found in cookies. Controller may not require it.[/yellow]")
    
    # Summary
    console.print("\n[bold green]===== Test Summary =====[/bold green]")
    console.print("[green]✓ Authentication: Successful[/green]")
    console.print("[green]✓ GET API Endpoints: Tested[/green]")
    console.print("[green]✓ POST API Endpoints: Tested[/green]")
    console.print(f"[green]✓ CSRF Protection: {'Detected' if csrf_token else 'Not detected or not required'}[/green]")
    
    return True

def main():
    """Main function"""
    if len(sys.argv) < 2:
        console.print("[bold red]Error: Missing arguments[/bold red]")
        console.print("Usage: python cloudkey_gen2_test.py <host> [username] [password] [site]")
        console.print("Example: python cloudkey_gen2_test.py 192.168.1.1 admin ubnt default")
        sys.exit(1)
    
    host = sys.argv[1]
    username = sys.argv[2] if len(sys.argv) > 2 else "admin"
    password = sys.argv[3] if len(sys.argv) > 3 else "ubnt"
    site = sys.argv[4] if len(sys.argv) > 4 else "default"
    
    # Run the test
    success = test_cloudkey_gen2(host, username, password, site)
    
    if not success:
        console.print("\n[bold red]Test failed![/bold red]")
        sys.exit(1)
    else:
        console.print("\n[bold green]Test completed successfully![/bold green]")
        sys.exit(0)

if __name__ == "__main__":
    main()
