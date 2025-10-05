#!/usr/bin/env python3
"""
CloudKey Authentication Test Suite Runner

This script runs all the available test scripts in sequence to verify
that the CSRF token manager is working correctly with CloudKey Gen2 devices.

Usage:
    python3 run_all_tests.py <controller_url> <username> <password> [site_name]

Example:
    python3 run_all_tests.py https://YOUR_CONTROLLER_IP admin YOUR_PASSWORD default
"""

import subprocess
import sys
import time

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

console = Console()


def normalize_url(host):
    """Normalize URL to include https:// if not present"""
    if not host.startswith(("http://", "https://")):
        return f"https://{host}"
    return host


def run_script(script_name, host, username, password, site="default"):
    """Run a test script with the provided credentials"""
    console.print(f"\n[bold cyan]Running {script_name}...[/bold cyan]")

    try:
        cmd = ["python3", script_name, host, username, password, site]
        result = subprocess.run(cmd, check=True)
        return result.returncode == 0
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Error running {script_name}: {e}[/red]")
        return False


def main(host, username, password, site="default"):
    """Run all test scripts in sequence"""
    # Normalize URL
    host = normalize_url(host)

    console.print(
        Panel(
            f"[bold]CloudKey Authentication Test Suite[/bold]\nHost: {host}\nSite: {site}",
            title="Test Runner",
            border_style="cyan",
        )
    )

    # List of test scripts to run
    test_scripts = [
        {
            "name": "CloudKey Gen2+ API Client Test",
            "script": "tests/cloudkey/test_cloudkey_gen2_client.py",
        }
    ]

    # Note: The following tests use an older API and need to be updated:
    # - csrf_diagnostic.py (uses old CSRFTokenManager API)
    # - verify_post_requests.py (uses old CSRFTokenManager API)
    # - audit_post_test.py (uses old CSRFTokenManager API)
    #
    # Use the CloudKey Gen2+ API Client for all CloudKey operations instead.

    results = {}

    # Run each test script
    for test in test_scripts:
        script_name = test["script"]
        test_name = test["name"]

        console.print(f"\n[bold]===== Running {test_name} =====\n[/bold]")

        # Run the script
        success = run_script(script_name, host, username, password, site)
        results[script_name] = success

        if not success:
            if not Confirm.ask(
                f"[yellow]Test {test_name} failed. Continue with next test?[/yellow]", default=True
            ):
                console.print("[yellow]Aborting test suite[/yellow]")
                break

        # Pause between tests
        if script_name != test_scripts[-1]["script"]:
            console.print("\n[dim]Waiting 3 seconds before next test...[/dim]")
            time.sleep(3)

    # Print summary
    console.print("\n[bold]===== Test Suite Summary =====\n[/bold]")

    all_success = True
    for script_name, success in results.items():
        status = "[green]✓ Passed[/green]" if success else "[red]× Failed[/red]"
        console.print(f"{script_name}: {status}")
        if not success:
            all_success = False

    if all_success:
        console.print("\n[green bold]SUCCESS! All tests passed.[/green bold]")
        console.print(
            "[green]Your CSRF token manager implementation is working correctly with CloudKey Gen2 devices.[/green]"
        )
    else:
        console.print("\n[yellow bold]WARNING: Some tests failed.[/yellow bold]")
        console.print("[yellow]Check the output above for details on the failing tests.[/yellow]")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        console.print("[red]Error: Missing required arguments[/red]")
        console.print(f"Usage: {sys.argv[0]} <controller_url> <username> <password> [site_name]")
        sys.exit(1)

    host = sys.argv[1]
    username = sys.argv[2]
    password = sys.argv[3]
    site = sys.argv[4] if len(sys.argv) > 4 else "default"

    main(host, username, password, site)
