#!/usr/bin/env python3
"""
Quick test for API error handling
"""
from rich.console import Console
import requests
import sys
from getpass import getpass
from pathlib import Path

# Add parent directory to path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from api.client import api_get

console = Console()

def test_api():
    host = input("Controller host (e.g., 192.168.1.1): ")
    site = input("Site name [default]: ") or "default"
    username = input("Username: ")
    password = getpass("Password: ")
    
    # Create session
    s = requests.Session()
    
    # Format host
    if not host.startswith("http"):
        host = f"https://{host}"
    
    # Try to login
    console.print(f"[bold blue]Logging in to {host}...[/bold blue]")
    login_url = f"{host}/api/login"
    login_data = {"username": username, "password": password}
    
    try:
        r = s.post(login_url, json=login_data, verify=False)
        r.raise_for_status()
        console.print("[green]Login successful![/green]")
        
        # Try to get some data
        console.print(f"[bold blue]Testing API get from site {site}...[/bold blue]")
        
        # Test device list
        console.print("\n[bold]Testing device list:[/bold]")
        devices = api_get(s, f"{host}/api/s/{site}/stat/device", verify=False)
        console.print(f"[green]Found {len(devices)} devices[/green]")
        
        # Test client list
        console.print("\n[bold]Testing client list:[/bold]")
        clients = api_get(s, f"{host}/api/s/{site}/stat/sta", verify=False)
        console.print(f"[green]Found {len(clients)} clients[/green]")
        
        # Test site health
        console.print("\n[bold]Testing site health:[/bold]")
        health = api_get(s, f"{host}/api/s/{site}/stat/health", verify=False)
        console.print(f"[green]Got {len(health)} health metrics[/green]")
        
        # Test self sites list
        console.print("\n[bold]Testing sites list:[/bold]")
        sites = api_get(s, f"{host}/api/self/sites", verify=False)
        console.print(f"[green]Found {len(sites)} sites[/green]")
        
        console.print("\n[bold green]All API tests completed successfully![/bold green]")
        
    except requests.exceptions.RequestException as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        
        # Try to get more info about the error
        if hasattr(e, "response") and e.response is not None:
            try:
                error_data = e.response.json()
                if "meta" in error_data and "msg" in error_data["meta"]:
                    console.print(f"[red]API Error: {error_data['meta']['msg']}[/red]")
            except:
                console.print(f"[red]Status code: {e.response.status_code}[/red]")
                console.print(f"[red]Response: {e.response.text[:200]}...[/red]")
                
    except Exception as e:
        console.print(f"[red]Unexpected error: {str(e)}[/red]")

if __name__ == "__main__":
    test_api()
