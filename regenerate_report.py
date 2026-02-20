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
    import json
    import sys as _sys

    from core.report_v2 import generate_v2_report

    console.print("\n[bold cyan]HTML Report Regenerator[/bold cyan]\n")

    # Accept --cache <file> argument
    cache_file = None
    args = _sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--cache" and i + 1 < len(args):
            cache_file = Path(args[i + 1])
            break

    if not cache_file:
        cache_file = find_latest_analysis_cache()

    if cache_file:
        console.print(f"[green]Found cached analysis:[/green] {cache_file}")
        try:
            with open(cache_file, "r") as f:
                cached = json.load(f)
            analysis = cached.get("full_analysis")
            recommendations = cached.get("recommendations", [])
            site_name = cached.get("site_name", "default")
            console.print("[green]✓[/green] Loaded from cache\n")
        except Exception as e:
            console.print(f"[red]Failed to load cache: {e}[/red]")
            console.print(
                "[yellow]No valid cache available. Run 'optimizer.py analyze' first.[/yellow]"
            )
            return False
    else:
        console.print("[yellow]No cached analysis found.[/yellow]")
        console.print(
            "[yellow]Run 'python3 optimizer.py analyze' first to generate analysis data.[/yellow]"
        )
        return False

    if not analysis:
        console.print("[red]Cached analysis data is empty.[/red]")
        return False

    # Generate report
    console.print("[yellow]Generating report...[/yellow]")

    report_path, _ = generate_v2_report(
        analysis_data=analysis,
        recommendations=recommendations,
        site_name=site_name,
    )

    console.print(f"[green]✓[/green] Report generated: {report_path}")

    # Open report in browser
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
