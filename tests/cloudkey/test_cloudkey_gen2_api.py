#!/usr/bin/env python3
"""
CloudKey Gen2 API Test Script

This script specifically tests API connectivity to CloudKey Gen2 devices
with streamlined timeout handling and optimized API calls.
"""

import sys
import argparse
import requests
from pathlib import Path

try:
    from rich.console import Console
    from rich.table import Table
    console = Console()
except ImportError:
    print("Rich library not installed. Install with: pip install rich")
    sys.exit(1)

# Add parent directory to path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from api.cloudkey_compat import detect_cloudkey_generation
from api.cloudkey_jwt_helper import extract_csrf_token_from_cookie
from api.csrf_token_manager import get_csrf_token

# CloudKey Gen2 API endpoints to test
GEN2_API_ENDPOINTS = [
    {"path": "/api/system", "method": "GET", "description": "System Info"},
    {"path": "/api/backup", "method": "GET", "description": "Backup Settings"},
    {"path": "/api/users", "method": "GET", "description": "Users List"},
    {"path": "/api/self", "method": "GET", "description": "Current User Info"},
    {"path": "/api/firmware", "method": "GET", "description": "Firmware Info"},
    {"path": "/api/network", "method": "GET", "description": "Network Config"}
]

def test_endpoint(session, url, endpoint, timeout=5):
    """Test a specific API endpoint with timeout handling"""
    full_url = f"{url}{endpoint['path']}"
    method = endpoint['method'].lower()
    
    console.print(f"Testing [cyan]{endpoint['method']} {endpoint['path']}[/cyan] - {endpoint['description']}...")
    
    try:
        if method == 'get':
            response = session.get(full_url, timeout=timeout, verify=False)
        elif method == 'post':
            response = session.post(full_url, json={}, timeout=timeout, verify=False)
        else:
            return {
                "status": "error",
                "code": 0,
                "message": f"Unsupported method: {method}"
            }
        
        # Try to parse JSON response
        try:
            data = response.json()
            data_type = "JSON"
        except:
            data = response.text[:100] + "..." if len(response.text) > 100 else response.text
            data_type = "Text"
            
        result = {
            "status": "success" if response.status_code < 400 else "error",
            "code": response.status_code,
            "message": "OK" if response.status_code == 200 else response.reason,
            "data_type": data_type,
            "data": data
        }
        
        # Display result
        if result["status"] == "success":
            console.print(f"  [green]✓ {response.status_code} {result['message']}[/green]")
        else:
            console.print(f"  [red]✗ {response.status_code} {result['message']}[/red]")
            
        return result
            
    except requests.exceptions.Timeout:
        console.print(f"  [yellow]⚠ Timeout after {timeout} seconds[/yellow]")
        return {
            "status": "timeout",
            "code": 0,
            "message": f"Request timed out after {timeout} seconds"
        }
    except requests.exceptions.RequestException as e:
        console.print(f"  [red]✗ Request error: {str(e)}[/red]")
        return {
            "status": "error",
            "code": 0,
            "message": str(e)
        }

def authenticate_gen2(url, username, password, timeout=5):
    """Authenticate with the CloudKey Gen2 controller"""
    session = requests.Session()
    session.verify = False  # Ignore SSL verification for internal devices
    
    # Get CSRF token first for Gen2
    console.print("\n[bold blue]Getting CSRF token...[/bold blue]")
    csrf_token = get_csrf_token(url, session)
    
    if not csrf_token:
        console.print("[red]Failed to get CSRF token[/red]")
        return None
        
    console.print(f"[green]Got CSRF token: {csrf_token[:10]}...[/green]")
    
    # Prepare login request
    login_url = f"{url}/api/login"
    login_data = {"username": username, "password": password}
    headers = {"X-CSRF-Token": csrf_token}
    
    # Attempt login
    console.print("\n[bold blue]Logging in...[/bold blue]")
    try:
        response = session.post(
            login_url, 
            json=login_data, 
            headers=headers, 
            timeout=timeout,
            verify=False
        )
        
        if response.status_code == 200:
            console.print("[green]Login successful[/green]")
            
            # Check for additional CSRF token in cookies
            cookie_csrf = extract_csrf_token_from_cookie(session)
            if cookie_csrf:
                console.print(f"[green]Got additional CSRF token from cookie: {cookie_csrf[:10]}...[/green]")
                # Update session headers with this token
                session.headers.update({"X-CSRF-Token": cookie_csrf})
            
            return session
        else:
            console.print(f"[red]Login failed: {response.status_code} {response.reason}[/red]")
            try:
                error_data = response.json()
                if 'meta' in error_data and 'msg' in error_data['meta']:
                    console.print(f"[red]Error message: {error_data['meta']['msg']}[/red]")
            except:
                pass
            return None
    except requests.exceptions.RequestException as e:
        console.print(f"[red]Login error: {str(e)}[/red]")
        return None

def run_gen2_tests(url, username, password):
    """Run all CloudKey Gen2 API tests"""
    # Normalize URL
    if not url.startswith('http'):
        url = f"https://{url}"
    if url.endswith('/'):
        url = url[:-1]
    
    # Detect CloudKey generation
    console.print("[bold blue]Detecting CloudKey generation...[/bold blue]")
    session = requests.Session()
    session.verify = False  # Ignore SSL verification for internal devices
    
    try:
        generation = detect_cloudkey_generation(url, session)
        console.print(f"[green]Detected CloudKey generation: {generation}[/green]")
        
        if generation < 2:
            console.print("[yellow]This script is designed for CloudKey Gen2+ devices.[/yellow]")
            proceed = input("Do you want to continue anyway? (y/n): ").lower() == 'y'
            if not proceed:
                return
    except Exception as e:
        console.print(f"[red]Error detecting CloudKey generation: {str(e)}[/red]")
        console.print("[yellow]Proceeding with tests assuming Gen2+ device...[/yellow]")
    
    # Authenticate
    session = authenticate_gen2(url, username, password)
    if not session:
        return
    
    # Run tests on all endpoints
    console.print("\n[bold blue]Running Gen2 API tests...[/bold blue]")
    
    results = []
    for endpoint in GEN2_API_ENDPOINTS:
        result = test_endpoint(session, url, endpoint)
        results.append({
            "endpoint": endpoint,
            "result": result
        })
    
    # Display results summary
    console.print("\n[bold blue]Test Results Summary:[/bold blue]")
    
    table = Table(show_header=True)
    table.add_column("Endpoint", style="cyan")
    table.add_column("Method", style="magenta")
    table.add_column("Description")
    table.add_column("Status", style="green")
    table.add_column("Code")
    
    for item in results:
        endpoint = item["endpoint"]
        result = item["result"]
        
        status_style = "green" if result["status"] == "success" else "red"
        
        table.add_row(
            endpoint["path"],
            endpoint["method"],
            endpoint["description"],
            f"[{status_style}]{result['status'].upper()}[/{status_style}]",
            str(result["code"])
        )
    
    console.print(table)
    
    # Calculate success rate
    success_count = sum(1 for r in results if r["result"]["status"] == "success")
    success_rate = (success_count / len(GEN2_API_ENDPOINTS)) * 100
    
    console.print(f"\n[bold]Success Rate: {success_rate:.1f}%[/bold] ({success_count}/{len(GEN2_API_ENDPOINTS)} endpoints)")
    
    if success_rate == 100:
        console.print("[bold green]All tests passed successfully![/bold green]")
    elif success_rate >= 80:
        console.print("[bold green]Most tests passed![/bold green]")
    elif success_rate >= 50:
        console.print("[bold yellow]Some tests failed.[/bold yellow]")
    else:
        console.print("[bold red]Many tests failed. Check your CloudKey configuration.[/bold red]")

def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='Test CloudKey Gen2 API')
    parser.add_argument('--host', help='CloudKey hostname or IP (e.g., 192.168.1.1)')
    parser.add_argument('--username', help='Login username')
    parser.add_argument('--password', help='Login password')
    return parser.parse_args()

def main():
    """Main entry point"""
    console.print("[bold blue]==================================[/bold blue]")
    console.print("[bold blue]CloudKey Gen2 API Test[/bold blue]")
    console.print("[bold blue]==================================[/bold blue]")
    
    # Parse arguments
    args = parse_args()
    
    # Get connection info from args or prompt
    host = args.host or input("Enter CloudKey hostname or IP (e.g., 192.168.1.1): ")
    username = args.username or input("Enter username: ")
    password = args.password or input("Enter password: ")
    
    # Run the tests
    run_gen2_tests(host, username, password)

if __name__ == "__main__":
    main()
