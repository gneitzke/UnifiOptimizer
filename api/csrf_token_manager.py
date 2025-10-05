#!/usr/bin/env python3
"""
CloudKey CSRF Token Manager
This module provides functions for managing CSRF tokens and cookie sessions
for CloudKey Gen2 controllers.
"""

import time

import requests
import urllib3
from rich.console import Console

# Import JWT helper for CloudKey Gen2 support
try:
    from api.cloudkey_jwt_helper import extract_csrf_from_cookie, extract_csrf_from_jwt
except ImportError:
    # Fallback implementations if module not available
    def extract_csrf_from_jwt(token):
        return None

    def extract_csrf_from_cookie(cookies):
        return None


# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

console = Console()


class CSRFTokenManager:
    """CloudKey CSRF token management for secure API communication."""

    def __init__(self):
        """Initialize the CSRF token manager."""
        self.token = None
        self.last_update = 0
        self.cookie_csrf = None
        self.token_sources = []
        self.is_cloudkey_gen2 = False

    def update_token(self, session, response=None):
        """
        Update the CSRF token from a response or session cookies.

        Args:
            session: The requests session object
            response: Optional response object to extract token from

        Returns:
            str: The current CSRF token
        """
        # Save the current token state for comparison
        old_token = self.token
        token_updated = False
        self.token_sources = []

        # Try to extract from response if provided
        if response is not None:
            # Check for X-CSRF-Token header
            csrf_header = response.headers.get("X-CSRF-Token")
            if csrf_header:
                self.token = csrf_header
                self.token_sources.append("X-CSRF-Token header")
                token_updated = True

            # Check for CSRF token in cookies
            cookie_csrf = extract_csrf_from_cookie(response.cookies)
            if cookie_csrf:
                self.cookie_csrf = cookie_csrf
                if not token_updated:
                    self.token = cookie_csrf
                    self.token_sources.append("Cookie")
                    token_updated = True

        # If no token yet, try to extract from session cookies
        if not token_updated and session.cookies:
            cookie_csrf = extract_csrf_from_cookie(session.cookies)
            if cookie_csrf:
                self.cookie_csrf = cookie_csrf
                self.token = cookie_csrf
                self.token_sources.append("Session cookie")
                token_updated = True

        # Update timestamp if token was updated
        if token_updated and self.token != old_token:
            self.last_update = time.time()

        return self.token

    def apply_token(self, session, method, url, **kwargs):
        """
        Apply the CSRF token to a request.

        Args:
            session: The requests session object
            method: HTTP method (GET, POST, etc.)
            url: Request URL
            **kwargs: Additional request arguments

        Returns:
            dict: Updated kwargs with CSRF token
        """
        # Only apply for non-GET requests
        if method.upper() != "GET" and self.token:
            # Ensure headers dictionary exists
            if "headers" not in kwargs:
                kwargs["headers"] = {}

            # Add CSRF token header
            kwargs["headers"]["X-CSRF-Token"] = self.token

        return kwargs


# Global CSRF token manager instance
csrf_manager = CSRFTokenManager()


def setup_csrf_session():
    """
    Create a requests session with CSRF token handling.

    Returns:
        requests.Session: Session with CSRF token handling
    """
    session = requests.Session()

    # Disable SSL verification warnings
    session.verify = False

    # Store the original request method
    original_request = session.request

    # Create a wrapper that injects the CSRF token
    def csrf_aware_request(method, url, **kwargs):
        # Apply the CSRF token to the request
        kwargs = csrf_manager.apply_token(session, method, url, **kwargs)

        # Make the request
        response = original_request(method, url, **kwargs)

        # Update the token from the response
        csrf_manager.update_token(session, response)

        return response

    # Replace the request method with our wrapper
    session.request = csrf_aware_request

    return session
