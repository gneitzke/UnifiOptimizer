#!/usr/bin/env python3
"""
Keychain Helper for UniFi Network Optimizer
Securely stores and retrieves credentials using macOS Keychain
"""

import json
import shutil
import subprocess
from pathlib import Path

from rich.console import Console

console = Console()

# Keychain service name
SERVICE_NAME = "UniFi-Network-Optimizer"


def is_keychain_available():
    """Check if macOS Keychain is available"""
    return shutil.which("security") is not None


def save_credentials(profile_name, host, username, password, site="default"):
    """
    Save connection profile to keychain

    Args:
        profile_name: Name for this profile (e.g., "home", "work")
        host: Controller URL
        username: Username
        password: Password
        site: Site name (default: "default")

    Returns:
        bool: True if saved successfully
    """
    if not is_keychain_available():
        console.print("[yellow]Keychain not available (macOS only)[/yellow]")
        return False

    try:
        # Save password to keychain
        account = f"{profile_name}"

        # Check if item exists
        check_cmd = ["security", "find-generic-password", "-s", SERVICE_NAME, "-a", account]

        exists = subprocess.run(check_cmd, capture_output=True).returncode == 0

        if exists:
            # Delete existing item
            delete_cmd = ["security", "delete-generic-password", "-s", SERVICE_NAME, "-a", account]
            subprocess.run(delete_cmd, capture_output=True)

        # Add new item with password
        add_cmd = [
            "security",
            "add-generic-password",
            "-s",
            SERVICE_NAME,
            "-a",
            account,
            "-w",
            password,
            "-U",  # Update if exists
        ]

        result = subprocess.run(add_cmd, capture_output=True)

        if result.returncode != 0:
            console.print(f"[red]Failed to save to keychain: {result.stderr.decode()}[/red]")
            return False

        # Save profile metadata (host, username, site) to JSON
        profiles_file = Path.home() / ".unifi_optimizer_profiles.json"

        profiles = {}
        if profiles_file.exists():
            with open(profiles_file, "r") as f:
                profiles = json.load(f)

        profiles[profile_name] = {"host": host, "username": username, "site": site}

        with open(profiles_file, "w") as f:
            json.dump(profiles, f, indent=2)

        console.print(f"[green]✓ Profile '{profile_name}' saved securely[/green]")
        return True

    except Exception as e:
        console.print(f"[red]Error saving to keychain: {e}[/red]")
        return False


def get_credentials(profile_name):
    """
    Retrieve connection profile from keychain

    Args:
        profile_name: Name of saved profile

    Returns:
        dict: {'host': ..., 'username': ..., 'password': ..., 'site': ...} or None
    """
    if not is_keychain_available():
        return None

    try:
        # Get profile metadata
        profiles_file = Path.home() / ".unifi_optimizer_profiles.json"

        if not profiles_file.exists():
            return None

        with open(profiles_file, "r") as f:
            profiles = json.load(f)

        if profile_name not in profiles:
            return None

        profile = profiles[profile_name]

        # Get password from keychain
        account = f"{profile_name}"

        cmd = [
            "security",
            "find-generic-password",
            "-s",
            SERVICE_NAME,
            "-a",
            account,
            "-w",  # Output password only
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            return None

        password = result.stdout.strip()

        return {
            "host": profile["host"],
            "username": profile["username"],
            "password": password,
            "site": profile.get("site", "default"),
        }

    except Exception as e:
        console.print(f"[red]Error reading from keychain: {e}[/red]")
        return None


def list_profiles():
    """
    List all saved profiles

    Returns:
        dict: Profile metadata (without passwords)
    """
    try:
        profiles_file = Path.home() / ".unifi_optimizer_profiles.json"

        if not profiles_file.exists():
            return {}

        with open(profiles_file, "r") as f:
            return json.load(f)

    except Exception:
        return {}


def delete_profile(profile_name):
    """
    Delete a saved profile

    Args:
        profile_name: Name of profile to delete

    Returns:
        bool: True if deleted successfully
    """
    if not is_keychain_available():
        return False

    try:
        # Delete from keychain
        account = f"{profile_name}"

        cmd = ["security", "delete-generic-password", "-s", SERVICE_NAME, "-a", account]

        subprocess.run(cmd, capture_output=True)

        # Delete from profiles file
        profiles_file = Path.home() / ".unifi_optimizer_profiles.json"

        if profiles_file.exists():
            with open(profiles_file, "r") as f:
                profiles = json.load(f)

            if profile_name in profiles:
                del profiles[profile_name]

                with open(profiles_file, "w") as f:
                    json.dump(profiles, f, indent=2)

        console.print(f"[green]✓ Profile '{profile_name}' deleted[/green]")
        return True

    except Exception as e:
        console.print(f"[red]Error deleting profile: {e}[/red]")
        return False
