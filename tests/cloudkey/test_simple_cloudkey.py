#!/usr/bin/env python3
"""
CloudKey API Test Script

This simple script runs direct tests of the CloudKey API with detailed output.
"""
import sys
import json
import time
import traceback
import requests
from pathlib import Path

# Add parent directory to path to enable imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Try to use pretty printing if available
try:
    from rich.console import Console
    console = Console()
    def print_json(data):
        console.print_json(json.dumps(data))
except ImportError:
    console = None
    def print_json(data):
        print(json.dumps(data, indent=2))

def auth_to_controller(host, username=None, password=None, site='default'):
    """
    Authenticate to a UniFi controller
    """
    print(f"Authenticating to {host}...")
    
    # Normalize the host URL
    if not host.startswith(('http://', 'https://')):
        host = f"https://{host}"
        
    # Create session
    s = requests.Session()
    s.headers.update({'User-Agent': 'UniFi-EasyToolkit/1.0'})
    
    # Disable SSL warnings - controllers often have self-signed certificates
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        s.verify = False
    except ImportError:
        pass
        
    # Try modern login endpoint first
    login_url = f"{host}/api/auth/login"
    try:
        print(f"Trying modern login endpoint: {login_url}")
        r = s.post(login_url, json={
            "username": username or "admin",
            "password": password or "ubnt",
            "remember": True
        })
        
        if r.status_code == 200:
            print("✅ Modern login succeeded!")
            # The authentication tokens are handled in cookies automatically
            return s
        else:
            print(f"❌ Modern login failed with status {r.status_code}")
    except Exception as e:
        print(f"❌ Modern login attempt error: {e}")
    
    # Fall back to legacy login endpoint
    login_url = f"{host}/api/login"
    try:
        print(f"Trying legacy login endpoint: {login_url}")
        r = s.post(login_url, json={
            "username": username or "admin", 
            "password": password or "ubnt"
        })
        
        if r.status_code == 200:
            print("✅ Legacy login succeeded!")
            # The authentication tokens are handled in cookies automatically
            return s
        else:
            print(f"❌ Legacy login failed with status {r.status_code}")
            print(f"Response: {r.text}")
    except Exception as e:
        print(f"❌ Legacy login attempt error: {e}")
        traceback.print_exc()
    
    print("⚠️ All authentication attempts failed")
    return None

def test_api_endpoints(session, host, site='default'):
    """
    Test various API endpoints
    """
    if not host.startswith(('http://', 'https://')):
        host = f"https://{host}"
        
    # List of essential endpoints to test
    endpoints = [
        {"path": f"/api/self", "name": "Self Info"},
        {"path": f"/api/self/sites", "name": "Sites List"},
        {"path": f"/api/s/{site}/stat/device", "name": "Devices"},
        {"path": f"/api/s/{site}/stat/sta", "name": "Clients"},
        {"path": f"/api/s/{site}/rest/user", "name": "Users"},
        {"path": f"/api/s/{site}/list/wlanconf", "name": "WLAN Config"},
        {"path": f"/api/s/{site}/list/setting", "name": "Settings"},
        {"path": f"/api/s/{site}/stat/health", "name": "Health"}
    ]
    
    results = {"success": [], "failed": []}
    
    print("\n🔍 Testing API endpoints...")
    for endpoint in endpoints:
        try:
            url = f"{host}{endpoint['path']}"
            print(f"\nTesting: {endpoint['name']} ({url})")
            
            r = session.get(url)
            
            if r.status_code == 200:
                data = r.json()
                if "data" in data or "meta" in data:
                    print(f"✅ Success: {endpoint['name']}")
                    
                    if console:
                        # Sample output for rich console
                        if "data" in data and len(data["data"]) > 0:
                            sample = data["data"][0] if isinstance(data["data"], list) else data["data"]
                            console.print("[dim]Sample data:[/dim]")
                            print_json(sample)
                    
                    results["success"].append(endpoint["path"])
                else:
                    print(f"⚠️ Warning: {endpoint['name']} returned unexpected format")
                    print(f"Response: {data}")
                    results["failed"].append(endpoint["path"])
            else:
                print(f"❌ Failed: {endpoint['name']} - Status {r.status_code}")
                print(f"Response: {r.text}")
                results["failed"].append(endpoint["path"])
                
        except Exception as e:
            print(f"❌ Error testing {endpoint['name']}: {e}")
            traceback.print_exc()
            results["failed"].append(endpoint["path"])
    
    return results

def test_csrf_tokens(session, host):
    """
    Test CSRF token handling
    """
    if not host.startswith(('http://', 'https://')):
        host = f"https://{host}"
        
    print("\n🔒 Testing CSRF token handling...")
    
    # Check for CSRF token in cookies
    csrf_token = None
    for cookie in session.cookies:
        if cookie.name in ('csrf_token', 'X-CSRF-Token', 'unifises', 'csrf'):
            print(f"✓ Found potential CSRF cookie: {cookie.name}={cookie.value[:10]}...")
            csrf_token = cookie.value
    
    if not csrf_token:
        print("⚠️ No CSRF token found in cookies. Controller may not require it.")
    
    # Try a simple GET request to check headers
    try:
        print("\nChecking response headers for CSRF information...")
        r = session.get(f"{host}/api/self")
        
        csrf_headers = [h for h in r.headers.keys() if 'csrf' in h.lower()]
        if csrf_headers:
            print(f"✓ Found CSRF headers: {csrf_headers}")
        else:
            print("⚠️ No CSRF headers found in response")
            
    except Exception as e:
        print(f"❌ Error checking CSRF headers: {e}")
    
    return csrf_token is not None

def check_session_timeout(session, host):
    """
    Check session timeout behavior
    """
    if not host.startswith(('http://', 'https://')):
        host = f"https://{host}"
        
    print("\n⏱️ Testing session timeout behavior...")
    
    # Make an initial request to establish baseline
    try:
        r1 = session.get(f"{host}/api/self")
        if r1.status_code != 200:
            print(f"❌ Initial request failed with status {r1.status_code}")
            return False
            
        print(f"✓ Initial request succeeded")
        
        # Wait for 2 minutes
        print("Waiting for 30 seconds to test session persistence...")
        time.sleep(30)
        
        # Make another request
        r2 = session.get(f"{host}/api/self")
        if r2.status_code == 200:
            print(f"✓ Session still valid after 30 seconds")
            return True
        else:
            print(f"❌ Session expired after 30 seconds (status {r2.status_code})")
            return False
            
    except Exception as e:
        print(f"❌ Error during session timeout test: {e}")
        return False

def main():
    """
    Main function for testing CloudKey API
    """
    print("\n📡 CloudKey API Test Script\n")
    
    if len(sys.argv) < 2:
        print("Usage: python simple_cloudkey_test.py <controller_ip> [username] [password] [site]")
        print("Example: python simple_cloudkey_test.py 192.168.1.1 admin password123 default")
        sys.exit(1)
    
    host = sys.argv[1]
    username = sys.argv[2] if len(sys.argv) > 2 else "admin"
    password = sys.argv[3] if len(sys.argv) > 3 else "ubnt"
    site = sys.argv[4] if len(sys.argv) > 4 else "default"
    
    # Create session and authenticate
    session = auth_to_controller(host, username, password, site)
    
    if not session:
        print("\n❌ Authentication failed. Cannot proceed with tests.")
        sys.exit(1)
    
    # Run tests
    endpoint_results = test_api_endpoints(session, host, site)
    csrf_supported = test_csrf_tokens(session, host)
    session_persistent = check_session_timeout(session, host)
    
    # Print summary
    print("\n📊 Test Summary:")
    print(f"✓ Authentication: Successful")
    print(f"✓ API Endpoints: {len(endpoint_results['success'])}/{len(endpoint_results['success']) + len(endpoint_results['failed'])} successful")
    print(f"✓ CSRF Protection: {'Detected' if csrf_supported else 'Not detected or not required'}")
    print(f"✓ Session Persistence: {'Session maintained' if session_persistent else 'Session expired quickly'}")
    
    if endpoint_results['failed']:
        print("\n⚠️ Failed endpoints:")
        for endpoint in endpoint_results['failed']:
            print(f"  - {endpoint}")
    
    print("\n✅ Tests completed!")

if __name__ == "__main__":
    main()
