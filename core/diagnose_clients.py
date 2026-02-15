#!/usr/bin/env python3
"""
UniFi Client Health Diagnostics
Comprehensive client signal, power, and disconnection analysis
"""

import argparse
import os
import sys
from getpass import getpass

from rich.console import Console
from rich.panel import Panel

# Add parent directory to path so we can import from api/ and core/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.cloudkey_gen2_client import CloudKeyGen2Client
from core.client_health import analyze_client_health
from utils.keychain import (
    get_credentials,
    is_keychain_available,
    save_credentials,
)

console = Console()


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="UniFi Client Health Diagnostics - Signal, Power, and Disconnection Analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run client health diagnostics
  python3 diagnose_clients.py --host https://YOUR_CONTROLLER_IP --username admin

# Show detailed problem analysis for each client
  python3 diagnose_clients.py --host https://YOUR_CONTROLLER_IP --username admin --detailed
        """,
    )

    parser.add_argument("--host", help="Controller URL")
    parser.add_argument("--username", help="Username")
    parser.add_argument("--password", help="Password (will prompt if not provided)")
    parser.add_argument("--site", default="default", help="Site name (default: default)")
    parser.add_argument("--profile", help="Use saved profile")
    parser.add_argument("--detailed", action="store_true", help="Show detailed per-client analysis")

    args = parser.parse_args()

    # Get connection details from profile or arguments
    used_profile = False  # Track if credentials came from a profile

    if args.profile:
        console.print(f"[cyan]Loading profile '{args.profile}'...[/cyan]")
        creds = get_credentials(args.profile)

        if not creds:
            console.print(f"[red]Profile '{args.profile}' not found[/red]")
            return 1

        host = creds["host"]
        username = creds["username"]
        password = creds["password"]
        site = creds.get("site", args.site)
        used_profile = True
        console.print(f"[green]✓ Loaded profile '{args.profile}'[/green]")
    elif not args.host and not args.username and is_keychain_available():
        # Try to auto-load default profile
        creds = get_credentials("default")
        if creds:
            console.print(f"[cyan]Loading default profile...[/cyan]")
            host = creds["host"]
            username = creds["username"]
            password = creds["password"]
            site = creds.get("site", args.site)
            used_profile = True
            console.print(f"[green]✓ Loaded default profile[/green]")
        else:
            # No default profile, require arguments
            console.print("[red]Error: --host and --username are required (or use --profile)[/red]")
            console.print(
                "[dim]Tip: Save a default profile using optimize_network.py --save-profile default[/dim]"
            )
            return 1
    else:
        if not args.host or not args.username:
            console.print("[red]Error: --host and --username are required (or use --profile)[/red]")
            return 1

        host = args.host
        username = args.username
        password = args.password if args.password else getpass("Password: ")
        site = args.site

    # Print banner
    console.print()
    console.print(
        Panel(
            "[bold cyan]UniFi Client Health Diagnostics[/bold cyan]\n"
            f"Controller: {host}\n"
            f"Site: {site}",
            style="cyan",
        )
    )
    console.print()

    # Create client with retry for login failures
    client = None
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        try:
            if attempt > 1:
                console.print(f"\n[yellow]Attempt {attempt}/{max_attempts}[/yellow]")
                password = getpass("Password: ")

            client = CloudKeyGen2Client(args.host, args.username, password, args.site)

            if client.login():
                # Login successful - auto-save profile ONLY if manually entered
                if not used_profile and attempt == 1 and is_keychain_available():
                    # Auto-save as 'default' profile
                    profile_name = "default"
                    save_credentials(profile_name, host, username, password, site)
                    console.print(f"[dim]✓ Credentials saved as profile '{profile_name}'[/dim]")

                break  # Continue with diagnostics
            else:
                console.print("[red]Failed to login to controller[/red]")
                if attempt < max_attempts:
                    console.print(
                        "[yellow]Please check your username and password and try again[/yellow]"
                    )
                else:
                    console.print("[red]Maximum login attempts reached[/red]")
                    return 1

        except KeyboardInterrupt:
            console.print("\n[yellow]Login cancelled by user[/yellow]")
            return 1
        except Exception as e:
            console.print(f"[red]Error connecting to controller: {e}[/red]")
            if attempt >= max_attempts:
                return 1

    if not client:
        console.print("[red]Failed to create client connection[/red]")
        return 1

    # Run diagnostics
    try:
        analysis = analyze_client_health(client, site)

        # Additional detailed output if requested
        if args.detailed:
            console.print("\n[bold]Detailed Client Analysis[/bold]")

            # Show all health scores
            scores = analysis["health_scores"]
            if scores:
                console.print(f"\nShowing all {len(scores)} clients sorted by health score:")

                from rich.table import Table

                table = Table(show_header=True, header_style="bold magenta")
                table.add_column("#", style="dim", width=4)
                table.add_column("Hostname", style="cyan", width=20)
                table.add_column("IP", style="white", width=15)
                table.add_column("Grade", style="white", width=6)
                table.add_column("Score", style="white", width=8)
                table.add_column("Signal", style="white", width=8)
                table.add_column("Disconnects", style="red", width=12)
                table.add_column("Roams", style="yellow", width=8)

                for i, client in enumerate(scores, 1):
                    grade_color = {
                        "A": "green",
                        "B": "blue",
                        "C": "yellow",
                        "D": "magenta",
                        "F": "red",
                    }.get(client["grade"], "white")

                    table.add_row(
                        str(i),
                        client["hostname"][:20],
                        client["ip"],
                        f"[{grade_color}]{client['grade']}[/{grade_color}]",
                        f"{client.get('score', client.get('health_score', 0))}/100",
                        f"{client.get('signal_quality', client.get('signal_score', 0))}/100",
                        str(client.get("disconnect_count", 0)) if client.get("disconnect_count", 0) > 0 else "-",
                        str(client.get("roam_count", 0)) if client.get("roam_count", 0) > 0 else "-",
                    )

                console.print(table)

        console.print("\n[green]✓ Client health diagnostics complete![/green]")
        return 0

    except Exception as e:
        console.print(f"\n[red]Error during diagnostics: {e}[/red]")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Diagnostics cancelled by user[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        import traceback

        traceback.print_exc()
        sys.exit(1)
