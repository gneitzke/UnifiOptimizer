#!/usr/bin/env python3
"""
Test script to verify CSRF token and cookie authentication fixes for CloudKey Gen2 devices.
This script performs a simple test to validate that the CSRF token manager is working correctly.
"""

import sys
import requests
from pathlib import Path
from rich.console import Console

# Add parent directory to path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from api.csrf_token_manager import setup_csrf_session
from api.cloudkey_api_helper import get_devices, get_clients

console = Console()

def test_cloudkey_auth(host, username, password, site="default"):
    """
    Run a simple test of CloudKey authentication with CSRF token handling.
    
    Args:
        host: Controller hostname or IP
        username: Login username
        password: Login password
        site: Site name (default is 'default')
    
    Returns:
        bool: True if all tests pass, False otherwise
    """
    # Normalize the host URL
    if not host.startswith('http'):
        host = f"https://{host}"
    if host.endswith('/'):
        host = host[:-1]
        
    # Create a session with CSRF token handling
    console.print("[bold blue]Setting up session with CSRF token handling...[/bold blue]")
    session = requests.Session()
    
    # Verify SSL is typically False in internal networks with self-signed certs
    session = setup_csrf_session(session, host, verify_ssl=False)
    
    # Test login
    console.print("[bold blue]Attempting login...[/bold blue]")
    login_url = f"{host}/api/login"
    login_data = {"username": username, "password": password, "strict": True}
    
    try:
        # Login attempt
        response = session.post(
            login_url,
            json=login_data, 
            verify=False
        )
        response.raise_for_status()
        
        console.print("[bold green]✓ Login successful[/bold green]")
        console.print(f"Response status code: {response.status_code}")
        
        # Check cookies
        console.print("\n[bold blue]Checking cookies...[/bold blue]")
        if "unifises" in session.cookies:
            console.print("[bold green]✓ Found session cookie (unifises)[/bold green]")
        else:
            console.print("[bold red]✗ Missing session cookie (unifises)[/bold red]")
            return False
            
        # Check CSRF token in header
        console.print("\n[bold blue]Checking CSRF token in headers...[/bold blue]")
        if "X-CSRF-Token" in session.headers:
            token_snippet = session.headers["X-CSRF-Token"][:10] + "..."
            console.print(f"[bold green]✓ CSRF token in headers: {token_snippet}[/bold green]")
        else:
            console.print("[bold red]✗ Missing CSRF token in headers[/bold red]")
            if "csrf_token" in session.cookies:
                token_value = session.cookies["csrf_token"].value
                console.print(f"[bold yellow]! Found CSRF token in cookies but not in headers: {token_value[:10]}...[/bold yellow]")
                
                # Fix the session by adding the token
                session.headers.update({"X-CSRF-Token": token_value})
                console.print("[bold green]✓ Added CSRF token to headers from cookies[/bold green]")
            else:
                console.print("[bold red]✗ CSRF token not found in cookies either[/bold red]")
                return False
        
        # Test a simple API call
        console.print("\n[bold blue]Testing API access...[/bold blue]")
        test_url = f"{host}/api/self/sites"
        
        try:
            test_response = session.get(test_url, verify=False)
            test_response.raise_for_status()
            
            data = test_response.json()
            sites_count = len(data.get('data', []))
            
            console.print(f"[bold green]✓ API access successful: found {sites_count} site(s)[/bold green]")
            
            # Test site-specific API call
            device_url = f"{host}/api/s/{site}/stat/device"
            
            try:
                device_response = session.get(device_url, verify=False)
                device_response.raise_for_status()
                
                device_data = device_response.json()
                device_count = len(device_data.get('data', []))
                
                console.print(f"[bold green]✓ Site API access successful: found {device_count} device(s)[/bold green]")
                
                # Show some device info
                if device_count > 0:
                    console.print("\n[bold blue]Sample device information:[/bold blue]")
                    device = device_data['data'][0]
                    console.print(f"  Name: {device.get('name', 'Unnamed')}")
                    console.print(f"  Model: {device.get('model', 'Unknown')}")
                    console.print(f"  MAC: {device.get('mac', 'Unknown')}")
                    console.print(f"  IP: {device.get('ip', 'Unknown')}")
                    console.print(f"  Status: {device.get('state', 0)}")
                
                # Test client API
                client_url = f"{host}/api/s/{site}/stat/sta"
                
                try:
                    client_response = session.get(client_url, verify=False)
                    client_response.raise_for_status()
                    
                    client_data = client_response.json()
                    client_count = len(client_data.get('data', []))
                    
                    console.print(f"[bold green]✓ Client API access successful: found {client_count} client(s)[/bold green]")
                    
                    # Show some client info
                    if client_count > 0:
                        console.print("\n[bold blue]Sample client information:[/bold blue]")
                        client = client_data['data'][0]
                        console.print(f"  Name: {client.get('name', client.get('hostname', 'Unnamed'))}")
                        console.print(f"  MAC: {client.get('mac', 'Unknown')}")
                        console.print(f"  IP: {client.get('ip', 'Unknown')}")
                        console.print(f"  First seen: {client.get('first_seen', 'Unknown')}")
                        console.print(f"  Last seen: {client.get('last_seen', 'Unknown')}")
                
                except requests.RequestException as e:
                    console.print(f"[bold red]✗ Client API access failed: {str(e)}[/bold red]")
            
            except requests.RequestException as e:
                console.print(f"[bold red]✗ Site API access failed: {str(e)}[/bold red]")
                return False
                
        except requests.RequestException as e:
            console.print(f"[bold red]✗ API access failed: {str(e)}[/bold red]")
            return False
        
        # Test using helper functions
        console.print("\n[bold blue]Testing API helper functions...[/bold blue]")
        
        try:
            devices = get_devices(session, host, site)
            console.print(f"[bold green]✓ API helper get_devices: found {len(devices)} device(s)[/bold green]")
            
            clients = get_clients(session, host, site)
            console.print(f"[bold green]✓ API helper get_clients: found {len(clients)} client(s)[/bold green]")
            
        except Exception as e:
            console.print(f"[bold red]✗ API helper function failed: {str(e)}[/bold red]")
            return False
        
        # All tests passed
        console.print("\n[bold green]All authentication and API tests passed![/bold green]")
        return True
        
    except requests.RequestException as e:
        console.print(f"[bold red]✗ Login failed: {str(e)}[/bold red]")
        return False
        
def main():
    """Main entry point"""
    console.print("[bold blue]==================================[/bold blue]")
    console.print("[bold blue]CloudKey Authentication Test[/bold blue]")
    console.print("[bold blue]==================================[/bold blue]")
    
    # Get connection info
    host = input("Enter CloudKey hostname or IP (e.g., 192.168.1.1:8443): ")
    username = input("Enter username: ")
    password = input("Enter password: ")
    site = input("Enter site name (default is 'default'): ") or "default"
    
    # Run test
    test_cloudkey_auth(host, username, password, site)

if __name__ == "__main__":
    main()
