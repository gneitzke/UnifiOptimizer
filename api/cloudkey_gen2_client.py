#!/usr/bin/env python3
"""
CloudKey Gen2+ API Client
Handles the specific API paths and authentication for CloudKey Gen2+ devices
"""

import requests
import urllib3
import logging
from datetime import datetime
from rich.console import Console
from .csrf_token_manager import CSRFTokenManager
from .cloudkey_jwt_helper import extract_csrf_from_jwt

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

console = Console()


class CloudKeyGen2Client:
    """API client specifically for CloudKey Gen2+ devices"""
    
    def __init__(self, host, username, password, site='default', verify_ssl=False, verbose=False):
        """
        Initialize CloudKey Gen2+ API client
        
        Args:
            host: CloudKey hostname/IP (e.g., https://192.168.1.1)
            username: Login username
            password: Login password
            site: Site name (default: 'default')
            verify_ssl: Whether to verify SSL certificates
            verbose: Enable verbose error logging
        """
        self.host = host.rstrip('/')
        self.username = username
        self.password = password
        self.site = site
        self.verify_ssl = verify_ssl
        self.verbose = verbose
        
        # Create session with CSRF token management
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self.csrf_manager = CSRFTokenManager()
        self.csrf_manager.is_cloudkey_gen2 = True
        
        # Track if we're logged in
        self.logged_in = False
        
        # Track API errors for reporting
        self.api_errors = []
        self.failed_endpoints = set()
        
        # Setup file logging if verbose mode enabled
        self.logger = None
        if verbose:
            self._setup_verbose_logging()
    
    def _setup_verbose_logging(self):
        """Setup file logging for verbose mode"""
        # Create logger
        self.logger = logging.getLogger('CloudKeyGen2Client')
        self.logger.setLevel(logging.DEBUG)
        
        # Clear any existing handlers
        self.logger.handlers = []
        
        # Create file handler
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_filename = f'verbose_{timestamp}.log'
        file_handler = logging.FileHandler(log_filename, mode='w')
        file_handler.setLevel(logging.DEBUG)
        
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        
        # Add handler to logger
        self.logger.addHandler(file_handler)
        
        # Log initial message
        self.logger.info("="*80)
        self.logger.info("UniFi Network Optimizer - Verbose Log")
        self.logger.info("="*80)
        self.logger.info(f"Controller: {self.host}")
        self.logger.info(f"Username: {self.username}")
        self.logger.info(f"Site: {self.site}")
        self.logger.info(f"SSL Verification: {self.verify_ssl}")
        self.logger.info("="*80)
        
        console.print(f"[dim]📝 Verbose logging enabled: {log_filename}[/dim]")
    
    def login(self):
        """
        Login to CloudKey Gen2+
        
        CloudKey Gen2+ uses /api/auth/login endpoint
        
        Returns:
            bool: True if login successful
        """
        if self.logger:
            self.logger.info("Attempting login...")
        
        try:
            login_url = f"{self.host}/api/auth/login"
            login_data = {
                'username': self.username,
                'password': '***REDACTED***',  # Don't log password
                'remember': False
            }
            
            if self.logger:
                self.logger.info(f"POST {login_url}")
                self.logger.debug(f"Login data: {login_data}")
            
            response = self.session.post(
                login_url,
                json={'username': self.username, 'password': self.password, 'remember': False},
                verify=self.verify_ssl
            )
            
            if self.logger:
                self.logger.info(f"Login response: {response.status_code}")
            
            if response.status_code != 200:
                error_msg = f"Login failed: {response.status_code}"
                console.print(f"[red]{error_msg}[/red]")
                console.print(f"Response: {response.text}")
                if self.logger:
                    self.logger.error(error_msg)
                    self.logger.error(f"Response text: {response.text}")
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
            
            if self.logger:
                self.logger.info("✓ Login successful")
            
            if self.csrf_manager.token:
                sources = ', '.join(self.csrf_manager.token_sources)
                console.print(f"[green]✓ CSRF token obtained from: {sources}[/green]")
                if self.logger:
                    self.logger.info(f"CSRF token obtained from: {sources}")
            
            # Check if account is local-only (recommended for security)
            self._check_account_type()
            
            # Verify API access by testing a basic endpoint
            if not self._verify_api_access():
                return False
            
            return True
            
        except Exception as e:
            console.print(f"[red]Login error: {str(e)}[/red]")
            return False
    
    def _check_account_type(self):
        """
        Check if the logged-in account is local-only (recommended for security)
        Displays a warning if using a Ubiquiti Cloud account
        """
        try:
            # Get current user info
            response = self.session.get(
                f"{self.host}/api/users/self",
                verify=self.verify_ssl
            )
            
            if response.status_code == 200:
                user_data = response.json()
                
                # Check if this is a local-only account
                # Local accounts don't have 'cloud_access' or have it disabled
                is_cloud_account = user_data.get('cloud_access_granted', False)
                
                # Also check if user is logging in with Ubiquiti SSO
                is_sso = user_data.get('sso_account', False) or user_data.get('is_super', False)
                
                if is_cloud_account or is_sso:
                    console.print()
                    console.print("[yellow]⚠ WARNING: This account has cloud access enabled[/yellow]")
                    console.print("[yellow]  For security, it's recommended to use a local-only account[/yellow]")
                    console.print()
                    console.print("[dim]To create a local-only account:[/dim]")
                    console.print("[dim]  1. Go to Settings → Admins in UniFi Controller[/dim]")
                    console.print("[dim]  2. Add New Admin[/dim]")
                    console.print("[dim]  3. Choose 'Local Access Only' (NOT Ubiquiti Account)[/dim]")
                    console.print("[dim]  4. Grant 'Full Management' role[/dim]")
                    console.print()
                else:
                    if self.verbose:
                        console.print("[dim]✓ Using local-only account (recommended)[/dim]")
        
        except Exception as e:
            # Don't fail login if we can't check account type
            if self.verbose:
                console.print(f"[dim]Note: Could not verify account type: {e}[/dim]")
    
    def _verify_api_access(self):
        """
        Verify that the user has API access by testing a basic UniFi endpoint
        This catches read-only users or users without proper permissions early
        
        Returns:
            bool: True if API access is available, False otherwise
        """
        try:
            # Test with a simple endpoint that all admins should have access to
            test_url = self._get_api_url(f"s/{self.site}/self")
            
            if self.logger:
                self.logger.info("Verifying API access...")
                self.logger.info(f"Testing: {test_url}")
            
            response = self.session.get(test_url, verify=self.verify_ssl)
            
            if self.logger:
                self.logger.info(f"API access test response: {response.status_code}")
            
            # Check for common "no access" scenarios
            if response.status_code == 401:
                console.print()
                console.print("[red]✗ API Access Denied: Authentication failed[/red]")
                console.print("[yellow]Your credentials were accepted for login, but API access was denied.[/yellow]")
                console.print()
                console.print("[dim]Possible causes:[/dim]")
                console.print("[dim]  • Session expired immediately after login[/dim]")
                console.print("[dim]  • Controller authentication system issue[/dim]")
                console.print("[dim]  • Try logging in again[/dim]")
                console.print()
                
                if self.logger:
                    self.logger.error("API access test failed: 401 Unauthorized")
                    self.logger.error(f"Response: {response.text}")
                
                return False
            
            elif response.status_code == 403:
                console.print()
                console.print("[red]✗ API Access Denied: Insufficient Permissions[/red]")
                console.print("[yellow]Your account does not have the required permissions.[/yellow]")
                console.print()
                console.print("[dim]Required: Administrator or Full Management role[/dim]")
                console.print()
                console.print("[bold]To fix this:[/bold]")
                console.print("  1. Log into the UniFi Controller as an administrator")
                console.print("  2. Go to Settings → Admins")
                console.print(f"  3. Find user '{self.username}'")
                console.print("  4. Change role to 'Administrator' or 'Full Management'")
                console.print()
                console.print("[dim]Read-only accounts cannot use this tool.[/dim]")
                console.print()
                
                if self.logger:
                    self.logger.error("API access test failed: 403 Forbidden")
                    self.logger.error(f"Response: {response.text}")
                
                return False
            
            elif response.status_code == 404:
                # 404 on /self might just mean site doesn't exist or different API version
                # Try an alternative endpoint
                alt_test_url = self._get_api_url(f"s/{self.site}/stat/device")
                
                if self.logger:
                    self.logger.info(f"Testing alternative endpoint: {alt_test_url}")
                
                alt_response = self.session.get(alt_test_url, verify=self.verify_ssl)
                
                if self.logger:
                    self.logger.info(f"Alternative test response: {alt_response.status_code}")
                
                if alt_response.status_code == 403:
                    # Same 403 handling as above
                    console.print()
                    console.print("[red]✗ API Access Denied: Insufficient Permissions[/red]")
                    console.print("[yellow]Your account does not have the required permissions.[/yellow]")
                    console.print()
                    console.print("[dim]Required: Administrator or Full Management role[/dim]")
                    console.print()
                    console.print("[bold]To fix this:[/bold]")
                    console.print("  1. Log into the UniFi Controller as an administrator")
                    console.print("  2. Go to Settings → Admins")
                    console.print(f"  3. Find user '{self.username}'")
                    console.print("  4. Change role to 'Administrator' or 'Full Management'")
                    console.print()
                    console.print("[dim]Read-only accounts cannot use this tool.[/dim]")
                    console.print()
                    
                    if self.logger:
                        self.logger.error("API access test failed: 403 Forbidden (alternative endpoint)")
                    
                    return False
                
                elif alt_response.status_code in [200, 404]:
                    # 200 = success, 404 = endpoint exists but no data (still means we have access)
                    if self.verbose:
                        console.print("[dim]✓ API access verified[/dim]")
                    
                    if self.logger:
                        self.logger.info("✓ API access verified (alternative endpoint)")
                    
                    return True
            
            elif response.status_code == 200:
                # Success - user has API access
                if self.verbose:
                    console.print("[dim]✓ API access verified[/dim]")
                
                if self.logger:
                    self.logger.info("✓ API access verified")
                
                return True
            
            else:
                # Other error codes - log but don't fail (might be controller-specific)
                if self.verbose:
                    console.print(f"[dim]Note: API access test returned {response.status_code} (continuing anyway)[/dim]")
                
                if self.logger:
                    self.logger.warning(f"API access test returned unexpected code: {response.status_code}")
                
                return True  # Proceed anyway, will fail later if really no access
        
        except Exception as e:
            # Don't fail on network errors during verification
            if self.verbose:
                console.print(f"[dim]Note: Could not verify API access: {e}[/dim]")
            
            if self.logger:
                self.logger.warning(f"Could not verify API access: {e}")
            
            return True  # Proceed anyway, will fail later if really no access
    
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
        
        if self.logger:
            self.logger.info(f"GET {url}")
        
        try:
            response = self.session.get(url, verify=self.verify_ssl)
            
            if self.logger:
                self.logger.info(f"Response: {response.status_code}")
            
            # Update CSRF token from response if available
            self.csrf_manager.update_token(self.session, response)
            
            if response.status_code != 200:
                error_msg = f"GET {path} returned {response.status_code}"
                
                # Track the error
                self.failed_endpoints.add(path)
                error_detail = {
                    'method': 'GET',
                    'path': path,
                    'status_code': response.status_code,
                    'error': self._get_error_description(response.status_code),
                    'response_text': response.text[:200] if self.verbose else None
                }
                self.api_errors.append(error_detail)
                
                # Log to file
                if self.logger:
                    self.logger.error(f"✗ {error_msg}")
                    self.logger.error(f"  Error: {error_detail['error']}")
                    if response.text:
                        self.logger.error(f"  Response: {response.text[:500]}")
                
                # Show appropriate message based on verbose mode
                if self.verbose:
                    console.print(f"[red]✗ {error_msg}[/red]")
                    console.print(f"[red]  Error: {error_detail['error']}[/red]")
                    if response.text:
                        console.print(f"[dim]  Response: {response.text[:200]}[/dim]")
                else:
                    console.print(f"[yellow]⚠ {error_msg}[/yellow]")
                
                return None
            
            # Log success
            if self.logger:
                json_data = response.json()
                data_info = ""
                if isinstance(json_data, dict):
                    if 'data' in json_data:
                        data_len = len(json_data['data']) if isinstance(json_data['data'], list) else 'N/A'
                        data_info = f" (returned {data_len} items)"
                self.logger.info(f"✓ GET {path} successful{data_info}")
            
            return response.json()
            
        except Exception as e:
            error_msg = f"GET error for {path}: {str(e)}"
            
            # Track the error
            self.failed_endpoints.add(path)
            error_detail = {
                'method': 'GET',
                'path': path,
                'status_code': None,
                'error': str(e),
                'exception_type': type(e).__name__
            }
            self.api_errors.append(error_detail)
            
            # Log to file
            if self.logger:
                self.logger.error(f"✗ {error_msg}")
                self.logger.error(f"  Exception type: {type(e).__name__}")
                import traceback
                self.logger.error(f"  Traceback:\n{traceback.format_exc()}")
            
            if self.verbose:
                console.print(f"[red]✗ {error_msg}[/red]")
                import traceback
                console.print(f"[dim]{traceback.format_exc()}[/dim]")
            else:
                console.print(f"[red]⚠ {error_msg}[/red]")
            
            return None
    
    def _get_error_description(self, status_code):
        """Get human-readable error description"""
        error_descriptions = {
            401: "Unauthorized - Authentication failed or session expired",
            403: "Forbidden - Insufficient permissions",
            404: "Not Found - Endpoint does not exist",
            500: "Internal Server Error - Controller issue",
            502: "Bad Gateway - Controller not responding",
            503: "Service Unavailable - Controller overloaded"
        }
        return error_descriptions.get(status_code, f"HTTP Error {status_code}")
    
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
        
        if self.logger:
            self.logger.info(f"PUT {url}")
            self.logger.debug(f"  Data: {data}")
        
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
            
            if self.logger:
                self.logger.info(f"Response: {response.status_code}")
            
            # Update CSRF token from response if available
            self.csrf_manager.update_token(self.session, response)
            
            if response.status_code != 200:
                error_msg = f"PUT {path} returned {response.status_code}"
                console.print(f"[yellow]{error_msg}[/yellow]")
                console.print(f"Response: {response.text}")
                
                if self.logger:
                    self.logger.error(f"✗ {error_msg}")
                    self.logger.error(f"  Response: {response.text}")
                
                return None
            
            if self.logger:
                self.logger.info(f"✓ PUT {path} successful")
            
            return response.json()
            
        except Exception as e:
            error_msg = f"PUT error for {path}: {str(e)}"
            console.print(f"[red]{error_msg}[/red]")
            
            if self.logger:
                self.logger.error(f"✗ {error_msg}")
                import traceback
                self.logger.error(f"  Traceback:\n{traceback.format_exc()}")
            
            return None
    
    def get_error_summary(self):
        """
        Get summary of API errors encountered
        
        Returns:
            dict: Error summary with counts and details
        """
        if not self.api_errors:
            return None
        
        error_summary = {
            'total_errors': len(self.api_errors),
            'failed_endpoints': list(self.failed_endpoints),
            'errors_by_type': {},
            'critical_errors': []
        }
        
        # Categorize errors
        for error in self.api_errors:
            status = error.get('status_code', 'Exception')
            error_type = f"{status}" if status else error.get('exception_type', 'Unknown')
            
            if error_type not in error_summary['errors_by_type']:
                error_summary['errors_by_type'][error_type] = []
            error_summary['errors_by_type'][error_type].append(error)
            
            # Track critical errors (auth/permission issues)
            if status in [401, 403]:
                error_summary['critical_errors'].append(error)
        
        return error_summary
    
    def has_critical_errors(self):
        """Check if there are critical errors (auth/permission failures)"""
        return any(e.get('status_code') in [401, 403] for e in self.api_errors)
    
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
