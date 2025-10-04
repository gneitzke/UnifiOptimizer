#!/usr/bin/env python3
"""
CloudKey Audit POST Test

This script specifically tests the audit functionality with POST requests
to verify the CSRF token manager fixes. It performs a simple audit change
and verifies that it completes successfully.

Usage:
    python3 audit_post_test.py <controller_url> <username> <password> [site_name]
"""
import sys
import os
import json
import time
import argparse
import requests
from getpass import getpass
from rich.console import Console
from rich.panel import Panel

# Add parent directory to Python path for proper imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# Import from the main modules
from api.client import api_get, api_post
from api.csrf_token_manager import setup_csrf_session

# Initialize console
console = Console()

def login(controller, username, password):
    """Login to the controller and return session"""
    # Create session with CSRF token handling
    session = setup_csrf_session()
    
    # Login to controller
    login_url = f"{controller}/api/login"
    login_data = {
        "username": username,
        "password": password,
        "remember": False
    }
    
    console.print(f"Logging in to {login_url}...")
    response = session.post(login_url, json=login_data, verify=False)
    
    if response.status_code != 200:
        console.print(f"[red]Login failed: {response.status_code}[/red]")
        console.print(response.text)
        return None
        
    console.print("[green]Login successful[/green]")
    return session

def get_site_id(controller, session, site_name=None):
    """Get site ID from site name or use default site"""
    sites_url = f"{controller}/api/self/sites"
    
    console.print("Getting site information...")
    response = session.get(sites_url, verify=False)
    
    if response.status_code != 200:
        console.print(f"[red]Failed to get sites: {response.status_code}[/red]")
        console.print(response.text)
        return None
    
    sites = response.json().get('data', [])
    
    if not sites:
        console.print("[red]No sites found[/red]")
        return None
    
    if site_name:
        # Find site with matching name
        for site in sites:
            if site.get('name') == site_name or site.get('desc') == site_name:
                return site.get('name')
                
        console.print(f"[red]Site '{site_name}' not found[/red]")
        return None
    else:
        # Use default site
        return sites[0].get('name')

def get_access_point(controller, session, site_id):
    """Get an access point to modify"""
    devices_url = f"{controller}/api/s/{site_id}/stat/device"
    
    console.print("Getting access points...")
    response = session.get(devices_url, verify=False)
    
    if response.status_code != 200:
        console.print(f"[red]Failed to get devices: {response.status_code}[/red]")
        console.print(response.text)
        return None
    
    devices = response.json().get('data', [])
    
    # Filter for access points only
    aps = [device for device in devices if device.get('type') == 'uap']
    
    if not aps:
        console.print("[red]No access points found[/red]")
        return None
    
    # Return the first AP
    return aps[0]

def update_ap_name(controller, session, site_id, ap):
    """Update an access point name to test POST request with CSRF"""
    ap_id = ap.get('_id')
    current_name = ap.get('name')
    
    if not ap_id:
        console.print("[red]Access point has no ID[/red]")
        return False
    
    # Create a unique test name with timestamp
    new_name = f"Test AP {int(time.time())}"
    update_url = f"{controller}/api/s/{site_id}/rest/device/{ap_id}"
    
    update_data = {
        "name": new_name
    }
    
    console.print(f"Updating AP name from '{current_name}' to '{new_name}'...")
    response = session.put(update_url, json=update_data, verify=False)
    
    if response.status_code != 200:
        console.print(f"[red]Failed to update AP: {response.status_code}[/red]")
        console.print(response.text)
        return False
    
    console.print(f"[green]Successfully updated AP name to '{new_name}'[/green]")
    
    # Verify the update worked
    response = session.get(f"{controller}/api/s/{site_id}/stat/device/{ap_id}", verify=False)
    
    if response.status_code != 200:
        console.print("[red]Failed to verify update[/red]")
        return False
    
    updated_name = response.json().get('data', [{}])[0].get('name')
    
    if updated_name == new_name:
        console.print("[green]Verified update was successful[/green]")
        return True
    else:
        console.print(f"[red]Update verification failed. Name is '{updated_name}'[/red]")
        return False
    
def main():
    """Main function"""
    parser = argparse.ArgumentParser(description="Test CloudKey audit POST requests with CSRF tokens")
    parser.add_argument("controller", nargs="?", help="Controller URL")
    parser.add_argument("username", nargs="?", help="Username")
    parser.add_argument("password", nargs="?", help="Password")
    parser.add_argument("site", nargs="?", help="Site name (optional)")
    
    args = parser.parse_args()
    
    # Get controller details
    controller = args.controller if args.controller else input("Controller URL: ")
    username = args.username if args.username else input("Username: ")
    password = args.password if args.password else getpass("Password: ")
    site_name = args.site if args.site else None
    
    # Strip trailing slash from controller URL
    if controller.endswith('/'):
        controller = controller[:-1]
    
    console.print(Panel(f"[bold]CloudKey Audit POST Test[/bold]\nController: {controller}"))
    
    # Login to controller
    session = login(controller, username, password)
    if not session:
        sys.exit(1)
    
    # Get site ID
    site_id = get_site_id(controller, session, site_name)
    if not site_id:
        sys.exit(1)
    console.print(f"Using site: {site_id}")
    
    # Get an access point
    ap = get_access_point(controller, session, site_id)
    if not ap:
        sys.exit(1)
    
    # Update the AP name
    success = update_ap_name(controller, session, site_id, ap)
    
    if success:
        console.print("\n[bold green]Audit POST test completed successfully![/bold green]")
    else:
        console.print("\n[bold red]Audit POST test failed![/bold red]")
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Test interrupted[/yellow]")
    except Exception as e:
        console.print(f"\n[bold red]Test error: {str(e)}[/bold red]")
        import traceback
        traceback.print_exc()
