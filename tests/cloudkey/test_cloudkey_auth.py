#!/usr/bin/env python3
"""
Test script to verify CSRF token and cookie authentication fixes for CloudKey Gen2 devices.
This script performs a simple test to validate that the CSRF token manager is working correctly.
"""

import sys
import time
import requests
from rich.console import Console
from api.csrf_token_manager import setup_csrf_session
from api import cloudkey_api_helper as api

console = Console()

def test_cloudkey_auth(host, username, password, site="default"):
    """
    Run a simple test of CloudKey authentication with CSRF token handling.
    
    Args:
        host: Controller hostname or IP
        username: Login username
        password: Login password
        site: Site name (default: 'default')
        
    Returns:
        bool: True if all tests passed, False otherwise
    """
    console.print("\n[bold]===== Testing CloudKey Authentication with CSRF Token Fixes =====\n[/bold]")
    
    # Normalize host URL
    if not host.startswith(('http://', 'https://')):
        host = f'https://{host}'
    if host.endswith('/'):
        host = host[:-1]
    
    # Step 1: Create a session with CSRF token handling
    console.print("\n[bold cyan]Step 1: Creating session with CSRF token handling...[/bold cyan]")
    s = setup_csrf_session()
    if s:
        console.print("[green]✓ Session created successfully[/green]")
    else:
        console.print("[red]× Failed to create session[/red]")
        return False
    
    # Step 2: Login to the controller
    console.print("\n[bold cyan]Step 2: Logging in to controller...[/bold cyan]")
    login_url = f"{host}/api/login"
    login_data = {
        "username": username,
        "password": password,
        "strict": True
    }
    
    try:
        r = s.post(login_url, json=login_data, verify=False)
        if r.status_code == 200:
            console.print(f"[green]✓ Login successful (Status: {r.status_code})[/green]")
        else:
            console.print(f"[red]× Login failed (Status: {r.status_code})[/red]")
            console.print(f"[yellow]Response: {r.text[:200]}[/yellow]")
            return False
    except Exception as e:
        console.print(f"[red]× Login exception: {str(e)}[/red]")
        return False
    
    # Step 3: Test a GET request that doesn't need CSRF
    console.print("\n[bold cyan]Step 3: Testing basic GET request...[/bold cyan]")
    try:
        status_url = f"{host}/api/s/{site}/stat/health"
        r = s.get(status_url, verify=False)
        
        if r.status_code == 200:
            console.print(f"[green]✓ GET request successful (Status: {r.status_code})[/green]")
            data = r.json()
            if "data" in data and len(data["data"]) > 0:
                console.print(f"[green]✓ Health data retrieved: {len(data['data'])} items[/green]")
            else:
                console.print("[yellow]⚠ Health data empty or missing[/yellow]")
        else:
            console.print(f"[red]× GET request failed (Status: {r.status_code})[/red]")
            return False
    except Exception as e:
        console.print(f"[red]× GET request exception: {str(e)}[/red]")
        return False
    
    # Step 4: Test a POST request that needs CSRF token
    console.print("\n[bold cyan]Step 4: Testing POST request with CSRF token...[/bold cyan]")
    try:
        # Use the site info endpoint for a simple test
        site_url = f"{host}/api/s/{site}/rest/setting/site"
        r = s.get(site_url, verify=False)
        
        if r.status_code == 200:
            data = r.json()
            site_data = data.get("data", [{}])[0]
            site_id = site_data.get("_id", "")
            desc = site_data.get("desc", "Test Site")
            
            # Now try to update the site description with a POST request (will need CSRF)
            update_url = f"{host}/api/s/{site}/rest/setting/site/{site_id}"
            
            # Create updated site data - just change description temporarily
            updated_site = site_data.copy()
            updated_site["desc"] = f"{desc} [CSRF Test {int(time.time())}]"
            
            # Send the POST request which requires CSRF token
            r = s.put(update_url, json=updated_site, verify=False)
            
            if r.status_code == 200:
                console.print(f"[green]✓ POST request with CSRF token successful (Status: {r.status_code})[/green]")
                
                # Verify the change was applied
                r = s.get(site_url, verify=False)
                if r.status_code == 200:
                    data = r.json()
                    new_site_data = data.get("data", [{}])[0]
                    updated_desc = new_site_data.get("desc", "")
                    
                    if "[CSRF Test" in updated_desc:
                        console.print(f"[green]✓ Site description successfully updated: '{updated_desc}'[/green]")
                        
                        # Reset the description to original
                        new_site_data["desc"] = desc
                        r = s.put(update_url, json=new_site_data, verify=False)
                        if r.status_code == 200:
                            console.print("[green]✓ Site description reset to original[/green]")
                    else:
                        console.print(f"[yellow]⚠ Site description did not update as expected: '{updated_desc}'[/yellow]")
            else:
                console.print(f"[red]× POST request failed (Status: {r.status_code})[/red]")
                console.print(f"[yellow]Response: {r.text[:200]}[/yellow]")
                return False
        else:
            console.print(f"[red]× Could not retrieve site info (Status: {r.status_code})[/red]")
            return False
    except Exception as e:
        console.print(f"[red]× POST test error: {str(e)}[/red]")
        return False
    
    # Step 5: Test API helper function
    console.print("\n[bold cyan]Step 5: Testing API helper function with CSRF token...[/bold cyan]")
    try:
        # Find a device to test with
        devices_url = f"{host}/api/s/{site}/stat/device"
        r = s.get(devices_url, verify=False)
        
        if r.status_code == 200:
            data = r.json()
            devices = data.get("data", [])
            
            # Find an access point to test with
            for device in devices:
                if device.get("type") == "uap":
                    device_id = device.get("_id", "")
                    device_mac = device.get("mac", "")
                    device_name = device.get("name", "Unknown AP")
                    
                    if device_id:
                        console.print(f"[dim]Testing with device: {device_name} ({device_id})[/dim]")
                        
                        # Test API helper function to get device details
                        device_details = api.get_device_details(s, host, device_id, site)
                        
                        if device_details:
                            console.print("[green]✓ API helper function successfully retrieved device details[/green]")
                            
                            # Verify the device details
                            if device_details.get("_id") == device_id and device_details.get("mac") == device_mac:
                                console.print("[green]✓ Device details match expected values[/green]")
                            else:
                                console.print("[yellow]⚠ Device details don't match expected values[/yellow]")
                        else:
                            console.print("[red]× API helper function failed to retrieve device details[/red]")
                            return False
                        
                        break
            else:
                console.print("[yellow]⚠️ No suitable access points found for testing[/yellow]")
                # Not a critical failure if no APs available
        else:
            console.print(f"[red]× Could not retrieve devices (Status: {r.status_code})[/red]")
            return False
    except Exception as e:
        console.print(f"[red]× API helper test error: {str(e)}[/red]")
        return False
    
    console.print("\n[bold green]✅ All CloudKey authentication tests passed successfully![/bold green]")
    return True

if __name__ == "__main__":
    # Parse command line arguments
    if len(sys.argv) < 4:
        console.print("[bold red]Error: Missing required arguments[/bold red]")
        console.print("Usage: python test_cloudkey_auth.py <host> <username> <password> [site]")
        sys.exit(1)
    
    host = sys.argv[1]
    username = sys.argv[2]
    password = sys.argv[3]
    site = sys.argv[4] if len(sys.argv) > 4 else "default"
    
    success = test_cloudkey_auth(host, username, password, site)
    sys.exit(0 if success else 1)
