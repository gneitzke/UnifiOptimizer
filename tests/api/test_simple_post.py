#!/usr/bin/env python3
"""
Simple POST Test
A focused test to get a successful POST with 200 status code
"""

import sys
import requests
import json
import time
from rich.console import Console
from pathlib import Path

# Add parent directory to path to enable imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from api.csrf_token_manager import csrf_manager, setup_csrf_session

console = Console()

def test_simple_post(host, username, password, site="default"):
    """
    Test a simple POST request to get a 200 status code
    
    Args:
        host: Controller hostname or IP
        username: Login username
        password: Login password
        site: Site name (default: 'default')
    """
    console.print("[bold]===== Simple CloudKey POST Test =====\n[/bold]")
    
    # Normalize host URL
    if not host.startswith(('http://', 'https://')):
        host = f"https://{host}"
    
    # Create session with SSL verification disabled for self-signed certs
    s = requests.Session()
    s.verify = False
    
    # Suppress SSL warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    # Step 1: Login
    console.print("[bold cyan]Step 1: Authenticating...[/bold cyan]")
    try:
        login_url = f"{host}/api/auth/login"
        r = s.post(login_url, json={"username": username, "password": password}, timeout=5)
        
        if r.status_code == 200:
            console.print(f"[green]✓ Authentication successful (status {r.status_code})[/green]")
        else:
            console.print(f"[red]× Authentication failed (status {r.status_code})[/red]")
            return
    except Exception as e:
        # Try legacy login endpoint if modern login fails
        try:
            console.print("[yellow]Modern login failed, trying legacy login...[/yellow]")
            login_url = f"{host}/api/login"
            r = s.post(login_url, json={"username": username, "password": password}, timeout=5)
            
            if r.status_code == 200:
                console.print(f"[green]✓ Legacy authentication successful (status {r.status_code})[/green]")
            else:
                console.print(f"[red]× Legacy authentication failed (status {r.status_code})[/red]")
                console.print(f"Response: {r.text}")
                return
        except Exception as e2:
            console.print(f"[red]× Authentication error: {e2}[/red]")
            return
    
    # Step 2: Check for CSRF token
    console.print("\n[bold cyan]Step 2: Checking CSRF token...[/bold cyan]")
    csrf_token = None
    
    # Check for CSRF token in cookies
    for cookie in s.cookies:
        if cookie.name in ['csrf_token', 'X-CSRF-Token', 'unifises', 'csrf']:
            console.print(f"[green]✓ Found potential CSRF cookie: {cookie.name}[/green]")
            csrf_token = cookie.value
            break
    
    # Get CSRF token from /api/self response headers if not in cookies
    if not csrf_token:
        try:
            r = s.get(f"{host}/api/self")
            csrf_headers = [h for h in r.headers.keys() if 'csrf' in h.lower()]
            
            if csrf_headers:
                console.print(f"[green]✓ Found CSRF headers: {csrf_headers}[/green]")
                csrf_token = r.headers.get(csrf_headers[0])
            else:
                console.print("[yellow]No CSRF token found in headers[/yellow]")
        except Exception as e:
            console.print(f"[yellow]Error checking CSRF headers: {e}[/yellow]")
    
    if not csrf_token:
        console.print("[yellow]No CSRF token found, controller may not require it[/yellow]")
    
    # Setup CSRF token if found
    if csrf_token:
        s.headers.update({'X-CSRF-Token': csrf_token})
        console.print(f"[green]✓ Added X-CSRF-Token header[/green]")
    
    # Step 3: Test Simple POST (Client List with Attributes)
    console.print("\n[bold cyan]Step 3: Testing POST request...[/bold cyan]")
    try:
        # Define POST payload
        payload = {
            "attrs": ["name", "hostname", "ip", "mac", "rx_bytes", "tx_bytes", 
                    "signal", "rssi", "tx_rate", "rx_rate", "uptime"]
        }
        
        # Try POST request
        post_url = f"{host}/api/s/{site}/stat/sta"
        console.print(f"POST URL: {post_url}")
        console.print(f"Payload: {json.dumps(payload)}")
        
        r = s.post(post_url, json=payload)
        
        # Check response
        if r.status_code == 200:
            console.print(f"[green]✓ POST successful! Status: {r.status_code}[/green]")
            
            try:
                data = r.json()
                if 'data' in data:
                    console.print(f"[green]✓ Received {len(data['data'])} client records[/green]")
                    
                    # Show sample data
                    if len(data['data']) > 0:
                        console.print("\nSample client data:")
                        sample = data['data'][0]
                        
                        # Check if all requested attributes were returned
                        attrs_requested = set(payload['attrs'])
                        attrs_received = set(sample.keys())
                        attrs_missing = attrs_requested - attrs_received
                        
                        if attrs_missing:
                            console.print(f"[yellow]Note: {len(attrs_missing)} requested attributes not found: {', '.join(attrs_missing)}[/yellow]")
                        
                        # Pretty print sample data
                        console.print(json.dumps(sample, indent=2))
                else:
                    console.print(f"[yellow]Response didn't contain expected 'data' field[/yellow]")
                    console.print(f"Response: {r.text[:200]}")
            except Exception as e:
                console.print(f"[yellow]Error parsing JSON response: {e}[/yellow]")
                console.print(f"Raw response: {r.text[:200]}")
        else:
            console.print(f"[red]× POST failed with status {r.status_code}[/red]")
            console.print(f"Response: {r.text[:200]}")
            
            # If 401/403, try with CSRF-enhanced session
            if r.status_code in [401, 403]:
                console.print("\n[yellow]Authentication error. Trying CSRF manager...[/yellow]")
                
                # Create new session with CSRF manager
                csrf_session = setup_csrf_session(host, username, password)
                
                if csrf_session:
                    console.print("[green]✓ CSRF-enhanced session created successfully[/green]")
                    
                    # Retry POST with CSRF session
                    try:
                        r2 = csrf_session.post(post_url, json=payload)
                        
                        if r2.status_code == 200:
                            console.print(f"[green]✓ CSRF-enhanced POST successful! Status: {r2.status_code}[/green]")
                            console.print(f"Response contains {len(r2.json().get('data', []))} records")
                        else:
                            console.print(f"[red]× CSRF-enhanced POST failed with status {r2.status_code}[/red]")
                            console.print(f"Response: {r2.text[:200]}")
                    except Exception as e:
                        console.print(f"[red]× Error with CSRF-enhanced POST: {e}[/red]")
                else:
                    console.print("[red]× Failed to create CSRF-enhanced session[/red]")
    except Exception as e:
        console.print(f"[red]× Error during POST test: {e}[/red]")

def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        console.print("[bold red]Error: Missing required arguments[/bold red]")
        console.print("Usage: python simple_post_test.py <host> [username] [password] [site]")
        console.print("Example: python simple_post_test.py 192.168.1.1 admin ubnt default")
        sys.exit(1)
    
    # Parse command line args
    host = sys.argv[1]
    username = sys.argv[2] if len(sys.argv) > 2 else "admin"
    password = sys.argv[3] if len(sys.argv) > 3 else "ubnt"
    site = sys.argv[4] if len(sys.argv) > 4 else "default"
    
    # Run test
    test_simple_post(host, username, password, site)

if __name__ == "__main__":
    main()
