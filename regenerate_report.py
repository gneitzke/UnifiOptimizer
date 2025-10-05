#!/usr/bin/env python3
"""
Quick HTML Report Generator

Regenerates HTML report from the last analysis without re-running analysis.
Useful after fixing HTML template issues.
"""

import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console

console = Console()


def find_latest_analysis_cache():
    """Find the most recent analysis cache file"""
    cache_dir = Path(".")
    cache_files = list(cache_dir.glob("analysis_cache_*.json"))

    if not cache_files:
        return None

    # Sort by modification time, most recent first
    cache_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return cache_files[0]


def regenerate_report():
    """Regenerate HTML report from cached analysis data"""

    console.print("\n[bold cyan]HTML Report Regenerator[/bold cyan]\n")

    # For now, just run a fresh analysis
    # In future, we could cache analysis results
    console.print("[yellow]Running fresh analysis to generate report...[/yellow]\n")

    from api.cloudkey_gen2_client import CloudKeyGen2Client
    from core.html_report_generator import generate_html_report
    from core.html_report_generator_share import generate_html_report as generate_share_html_report
    from core.optimize_network import analyze_network
    from utils.keychain import get_credentials

    # Load credentials
    creds = get_credentials("default")
    if not creds:
        console.print("[red]Error: No saved credentials. Run optimizer.py first.[/red]")
        return False

    # Connect
    console.print(f"[yellow]Connecting to {creds['host']}...[/yellow]")
    client = CloudKeyGen2Client(
        creds["host"],
        creds["username"],
        creds["password"],
        site=creds.get("site", "default"),
        verify_ssl=False,
    )
    console.print("[green]✓[/green] Connected\n")

    # Analyze
    console.print("[yellow]Running analysis...[/yellow]")
    result = analyze_network(client, site=creds.get("site", "default"), lookback_days=3)
    console.print("[green]✓[/green] Analysis complete\n")

    # Generate reports (both versions)
    console.print("[yellow]Generating HTML report...[/yellow]")
    analysis = result.get("full_analysis")
    recommendations = result.get("recommendations", [])

    report_path = generate_html_report(
        analysis_data=analysis,
        recommendations=recommendations,
        site_name=creds.get("site", "default"),
        output_dir="reports",
    )

    console.print(f"[green]✓[/green] Report generated: {report_path}")

    # Generate sharing-friendly version
    share_report_path = generate_share_html_report(
        analysis_data=analysis,
        recommendations=recommendations,
        site_name=creds.get("site", "default"),
        output_dir="reports",
    )

    console.print(f"[green]✓[/green] Sharing version: {share_report_path}")
    console.print(f"[dim]   (Works in email/iMessage - uses static images)[/dim]\n")

    # Open main report in browser
    import subprocess

    subprocess.run(["open", report_path])
    console.print("[green]✓[/green] Opened in browser\n")

    return True


if __name__ == "__main__":
    try:
        success = regenerate_report()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled by user[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        import traceback

        traceback.print_exc()
        sys.exit(1)
