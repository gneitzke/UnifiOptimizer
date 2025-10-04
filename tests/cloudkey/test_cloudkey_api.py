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

try:
    from api.cloudkey_compat import test_cloudkey_gen2_api
    COMPAT_MODULE_AVAILABLE = True
except ImportError:
    COMPAT_MODULE_AVAILABLE = False
    console.print("[yellow]cloudkey_compat.py module not found, using internal implementation[/yellow]")

def create_session(verify_ssl=False):
    """Create a requests session with proper headers"""
    session = requests.Session()
    session.verify = verify_ssl
    session.headers.update({
        "User-Agent": "UniFi-Audit/1.0",
        "Content-Type": "application/json",
        "Accept": "application/json"
    })
    return session

def test_cloudkey_api(s, host, site="default"):
    """
    Test CloudKey Gen2 API connectivity and endpoints.
    
    Args:
        s: Authenticated session object
        host: Controller hostname/IP
        site: Site name (default: "default")
        
    Returns:
        dict: Results of API tests
    """
    # Normalize host URL
    if not host.startswith(('http://', 'https://')):
        host = f'https://{host}'
    if host.endswith('/'):
        host = host[:-1]
    
    # Define critical endpoints to test
    endpoints = [
        {"name": "System Info", "url": f"{host}/api/system", "critical": True},
        {"name": "Self", "url": f"{host}/api/self", "critical": True},
        {"name": "Available Sites", "url": f"{host}/api/self/sites", "critical": True},
        {"name": "Site Health", "url": f"{host}/api/s/{site}/stat/health", "critical": True},
        {"name": "Devices", "url": f"{host}/api/s/{site}/stat/device", "critical": True},
        {"name": "Clients", "url": f"{host}/api/s/{site}/stat/sta", "critical": True},
        {"name": "Rogue APs", "url": f"{host}/api/s/{site}/stat/rogueap", "critical": False},
        {"name": "Network Settings", "url": f"{host}/api/s/{site}/rest/networkconf", "critical": False},
        {"name": "Settings", "url": f"{host}/api/s/{site}/get/setting", "critical": False},
    ]
    
    results = {
        "success": False,
        "endpoints_tested": len(endpoints),
        "endpoints_available": 0,
        "critical_endpoints_total": len([e for e in endpoints if e["critical"]]),
        "critical_endpoints_available": 0,
        "endpoint_results": {}
    }
    
    console.print(f"[bold]Testing CloudKey API connectivity on {host}...[/bold]")
    
    # Test each endpoint
    for endpoint in endpoints:
        name = endpoint["name"]
        url = endpoint["url"].replace("{site}", site)
        critical = endpoint["critical"]
        
        try:
            console.print(f"[dim]Testing {name} endpoint...[/dim]")
            r = s.get(url, timeout=5)
            
            # Process result
            if r.status_code == 200:
                results["endpoints_available"] += 1
                if critical:
                    results["critical_endpoints_available"] += 1
                
                # Extract some metadata from response
                data_count = 0
                try:
                    data = r.json()
                    if "data" in data and isinstance(data["data"], list):
                        data_count = len(data["data"])
                except:
                    pass
                
                console.print(f"[green]✓ {name}: Success ({data_count} items)[/green]")
                results["endpoint_results"][name] = {
                    "status": "Success",
                    "status_code": 200,
                    "data_count": data_count,
                    "url": url
                }
            else:
                console.print(f"[yellow]⚠ {name}: Failed ({r.status_code})[/yellow]")
                results["endpoint_results"][name] = {
                    "status": "Failed",
                    "status_code": r.status_code,
                    "url": url
                }
        except Exception as e:
            console.print(f"[red]× {name}: Error - {str(e)}[/red]")
            results["endpoint_results"][name] = {
                "status": "Error",
                "error": str(e),
                "url": url
            }
    
    # Determine overall success
    results["success"] = (results["critical_endpoints_available"] == results["critical_endpoints_total"])
    
    # Calculate success percentage
    results["success_rate"] = round((results["endpoints_available"] / results["endpoints_tested"]) * 100)
    
    # Display result summary
    if results["success"]:
        console.print(f"[bold green]API Test Successful: {results['success_rate']}% of endpoints accessible[/bold green]")
    else:
        console.print(f"[bold yellow]API Test Issues: Only {results['success_rate']}% of endpoints accessible[/bold yellow]")
        console.print(f"[yellow]{results['critical_endpoints_available']}/{results['critical_endpoints_total']} critical endpoints available[/yellow]")
    
    return results

def main():
    """Run CloudKey API tests from command line"""
    parser = argparse.ArgumentParser(description="Test CloudKey Gen2 API connectivity")
    parser.add_argument("host", help="Controller hostname or IP")
    parser.add_argument("-u", "--username", help="Controller username")
    parser.add_argument("-p", "--password", help="Controller password")
    parser.add_argument("-s", "--site", default="default", help="Site name (default: 'default')")
    parser.add_argument("--verify-ssl", action="store_true", help="Verify SSL certificates")
    args = parser.parse_args()
    
    # Normalize host URL
    host = args.host
    if not host.startswith(('http://', 'https://')):
        host = f'https://{host}'
    
    # Use the compat module if available
    if COMPAT_MODULE_AVAILABLE:
        from api.cloudkey_compat import setup_cloudkey_session
        console.print(f"[bold]Testing CloudKey API using cloudkey_compat module[/bold]")
        
        # Set up a session
        session, controller_type, base_url = setup_cloudkey_session(
            host, 
            args.username, 
            args.password, 
            args.site
        )
        
        if not session:
            console.print("[bold red]Failed to create authenticated session[/bold red]")
            return 1
            
        # Run the test
        results = test_cloudkey_gen2_api(session, base_url, args.site)
        
    else:
        # Create a session
        session = create_session(args.verify_ssl)
        
        # Login if credentials provided
        if args.username and args.password:
            login_url = f"{host}/api/login"
            login_data = {
                "username": args.username,
                "password": args.password,
                "strict": True
            }
            
            try:
                r = session.post(login_url, json=login_data)
                if r.status_code != 200:
                    console.print(f"[bold red]Login failed: {r.status_code}[/bold red]")
                    return 1
            except Exception as e:
                console.print(f"[bold red]Login error: {str(e)}[/bold red]")
                return 1
        
        # Run tests
        results = test_cloudkey_api(session, host, args.site)
    
    return 0 if results["success"] else 1

if __name__ == "__main__":
    sys.exit(main())
