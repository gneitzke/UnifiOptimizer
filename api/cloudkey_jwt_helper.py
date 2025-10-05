#!/usr/bin/env python3
"""
CloudKey Gen2 JWT Token Helper
Functions for extracting data from JWT tokens in CloudKey Gen2
"""

import base64
import json

from rich.console import Console

console = Console()


def parse_jwt(token):
    """
    Parse a JWT token and extract its payload

    Args:
        token: JWT token string

    Returns:
        dict: Decoded JWT payload or None if invalid
    """
    if not token:
        return None

    try:
        # JWT format: header.payload.signature
        parts = token.split(".")
        if len(parts) != 3:
            return None

        # Decode the payload (middle part)
        # Add padding if needed
        payload = parts[1]
        padding = "=" * (4 - len(payload) % 4)
        payload = payload + padding

        # Base64 decode and parse JSON
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception as e:
        console.print(f"[yellow]Warning: Error parsing JWT token: {str(e)}[/yellow]")
        return None


def extract_csrf_from_jwt(token):
    """
    Extract CSRF token from JWT payload if present

    Args:
        token: JWT token string

    Returns:
        str: CSRF token or None if not found
    """
    payload = parse_jwt(token)
    if not payload:
        return None

    # Check common locations for CSRF token
    csrf = payload.get("csrf")
    if csrf:
        return csrf

    # Check for csrf in custom claims
    csrf = payload.get("csrfToken")
    if csrf:
        return csrf

    # Check any non-standard fields that might contain csrf token
    for key, value in payload.items():
        if "csrf" in key.lower() and isinstance(value, str):
            return value

    return None


def extract_csrf_from_cookie(cookies):
    """
    Extract CSRF token from cookies

    Args:
        cookies: Cookies object from requests

    Returns:
        str: CSRF token or None if not found
    """
    if not cookies:
        return None

    # Look for common CSRF cookie names
    csrf_cookies = ["csrf", "csrfToken", "X-CSRF-Token", "X-CSRF", "_csrf"]
    for name in csrf_cookies:
        if name in cookies:
            return cookies[name]

    # Search for any cookie containing 'csrf'
    for name, value in cookies.items():
        if "csrf" in name.lower():
            return value

    # Check for JWT token cookie and try to extract CSRF from it
    jwt_cookies = ["TOKEN", "AUTH_TOKEN", "session"]
    for name in jwt_cookies:
        if name in cookies:
            csrf = extract_csrf_from_jwt(cookies[name])
            if csrf:
                return csrf

    return None


def extract_username_from_jwt(token):
    """
    Extract username from JWT token

    Args:
        token: JWT token string

    Returns:
        str: Username or None if not found
    """
    payload = parse_jwt(token)
    if not payload:
        return None

    # Check common fields for username
    for field in ["username", "user", "sub", "email", "preferred_username"]:
        if field in payload and payload[field]:
            return payload[field]

    return None


def parse_cookie_for_credentials(cookies):
    """
    Parse cookies to extract potentially useful authentication data

    Args:
        cookies: Cookies object from requests

    Returns:
        dict: Extracted authentication data
    """
    result = {"username": None, "csrf_token": None, "session_id": None, "has_jwt": False}

    if not cookies:
        return result

    # Extract CSRF token
    result["csrf_token"] = extract_csrf_from_cookie(cookies)

    # Look for session identifier
    for name in ["SESSION", "JSESSIONID", "session_id", "sid"]:
        if name in cookies:
            result["session_id"] = cookies[name]
            break

    # Check for JWT and extract username
    jwt_cookies = ["TOKEN", "AUTH_TOKEN", "session", "access_token", "id_token"]
    for name in jwt_cookies:
        if name in cookies:
            result["has_jwt"] = True
            username = extract_username_from_jwt(cookies[name])
            if username:
                result["username"] = username
            break

    return result
