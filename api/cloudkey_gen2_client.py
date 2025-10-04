#!/usr/bin/env python3
"""
CloudKey Gen2+ API Client
Handles the specific API paths and authentication for CloudKey Gen2+ devices
"""

import requests
import urllib3
from rich.console import Console
from .csrf_token_manager import CSRFTokenManager
from .cloudkey_jwt_helper import extract_csrf_from_jwt

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

console = Console()


class CloudKeyGen2Client:
    """API client specifically for CloudKey Gen2+ devices"""
    
    def __init__(self, host, username, password, site='default', verify_ssl=False):
        """
        Initialize CloudKey Gen2+ API client
        
        Args:
            host: CloudKey hostname/IP (e.g., https://192.168.1.1)
            username: Login username
            password: Login password
            site: Site name (default: 'default')
            verify_ssl: Whether to verify SSL certificates
        """
        self.host = host.rstrip('/')
        self.username = username
        self.password = password
        self.site = site
        self.verify_ssl = verify_ssl
        
        # Create session with CSRF token management
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self.csrf_manager = CSRFTokenManager()
        self.csrf_manager.is_cloudkey_gen2 = True
        
        # Track if we're logged in
        self.logged_in = False
        
    def login(self):
        """
        Login to CloudKey Gen2+
        
        CloudKey Gen2+ uses /api/auth/login endpoint
        
        Returns:
            bool: True if login successful
        """
        try:
            login_url = f"{self.host}/api/auth/login"
            login_data = {
                'username': self.username,
                'password': self.password,
                'remember': False
            }
            
            response = self.session.post(
                login_url,
                json=login_data,
                verify=self.verify_ssl
            )
            
            if response.status_code != 200:
                console.print(f"[red]Login failed: {response.status_code}[/red]")
                console.print(f"Response: {response.text}")
                return False
            
            # Extract CSRF token from response
            self.csrf_manager.update_token(self.session, response)
            
            # Also try to extract from JWT if available
            token_cookie = self.session.cookies.get('TOKEN')
            if token_cookie:
                csrf_from_jwt = extract_csrf_from_jwt(token_cookie)
                if csrf_from_jwt:
                    self.csrf_manager.token = csrf_from_jwt
                    self.csrf_manager.token_sources.append("JWT payload")
            
            self.logged_in = True
            console.print(f"[green]✓ Logged in to CloudKey Gen2+[/green]")
            
            if self.csrf_manager.token:
                sources = ', '.join(self.csrf_manager.token_sources)
                console.print(f"[green]✓ CSRF token obtained from: {sources}[/green]")
            
            return True
            
        except Exception as e:
            console.print(f"[red]Login error: {str(e)}[/red]")
            return False
    
    def _get_api_url(self, path):
        """
        Get the full API URL for CloudKey Gen2+
        
        CloudKey Gen2+ uses /proxy/network/ prefix for UniFi Network API
        
        Args:
            path: API path (e.g., /api/s/default/stat/device)
            
        Returns:
            str: Full URL with correct prefix
        """
        # Remove leading slash if present
        path = path.lstrip('/')
        
        # CloudKey Gen2+ uses /proxy/network/ prefix for UniFi Network API
        if path.startswith('api/'):
            return f"{self.host}/proxy/network/{path}"
        else:
            return f"{self.host}/proxy/network/api/{path}"
    
    def get(self, path):
        """
        Perform GET request to CloudKey Gen2+ API
        
        Args:
            path: API path (e.g., s/default/stat/device)
            
        Returns:
            dict: JSON response data or None on error
        """
        if not self.logged_in:
            if not self.login():
                return None
        
        url = self._get_api_url(path)
        
        try:
            response = self.session.get(url, verify=self.verify_ssl)
            
            # Update CSRF token from response if available
            self.csrf_manager.update_token(self.session, response)
            
            if response.status_code != 200:
                console.print(f"[yellow]GET {path} returned {response.status_code}[/yellow]")
                return None
            
            return response.json()
            
        except Exception as e:
            console.print(f"[red]GET error for {path}: {str(e)}[/red]")
            return None
    
    def put(self, path, data):
        """
        Perform PUT request to CloudKey Gen2+ API with CSRF token
        
        Args:
            path: API path (e.g., s/default/rest/device/{id})
            data: Data to send in request body
            
        Returns:
            dict: JSON response data or None on error
        """
        if not self.logged_in:
            if not self.login():
                return None
        
        url = self._get_api_url(path)
        
        # Prepare headers with CSRF token
        headers = {'Content-Type': 'application/json'}
        if self.csrf_manager.token:
            headers['X-CSRF-Token'] = self.csrf_manager.token
        
        try:
            response = self.session.put(
                url,
                json=data,
                headers=headers,
                verify=self.verify_ssl
            )
            
            # Update CSRF token from response if available
            self.csrf_manager.update_token(self.session, response)
            
            if response.status_code != 200:
                console.print(f"[yellow]PUT {path} returned {response.status_code}[/yellow]")
                console.print(f"Response: {response.text}")
                return None
            
            return response.json()
            
        except Exception as e:
            console.print(f"[red]PUT error for {path}: {str(e)}[/red]")
            return None
    
    def post(self, path, data):
        """
        Perform POST request to CloudKey Gen2+ API with CSRF token
        
        Args:
            path: API path
            data: Data to send in request body
            
        Returns:
            dict: JSON response data or None on error
        """
        if not self.logged_in:
            if not self.login():
                return None
        
        url = self._get_api_url(path)
        
        # Prepare headers with CSRF token
        headers = {'Content-Type': 'application/json'}
        if self.csrf_manager.token:
            headers['X-CSRF-Token'] = self.csrf_manager.token
        
        try:
            response = self.session.post(
                url,
                json=data,
                headers=headers,
                verify=self.verify_ssl
            )
            
            # Update CSRF token from response if available
            self.csrf_manager.update_token(self.session, response)
            
            if response.status_code != 200:
                console.print(f"[yellow]POST {path} returned {response.status_code}[/yellow]")
                console.print(f"Response: {response.text}")
                return None
            
            return response.json()
            
        except Exception as e:
            console.print(f"[red]POST error for {path}: {str(e)}[/red]")
            return None


# Convenience functions for common operations
def get_devices(client):
    """Get all devices from CloudKey Gen2+"""
    response = client.get(f's/{client.site}/stat/device')
    if response and 'data' in response:
        return response['data']
    return []


def update_device(client, device_id, settings):
    """Update device settings on CloudKey Gen2+"""
    path = f's/{client.site}/rest/device/{device_id}'
    return client.put(path, settings)


def get_wlans(client):
    """Get all WLANs from CloudKey Gen2+"""
    response = client.get(f's/{client.site}/rest/wlanconf')
    if response and 'data' in response:
        return response['data']
    return []
