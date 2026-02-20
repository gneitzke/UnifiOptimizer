#!/usr/bin/env python3
"""
Test script for end-to-end validation of advanced features
"""

import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console

from api.cloudkey_gen2_client import CloudKeyGen2Client
from core.optimize_network import analyze_network
from utils.keychain import get_credentials

console = Console()


def test_advanced_features():
    """Test all advanced features with real controller"""

    console.print(
        "\n[bold cyan]═══════════════════════════════════════════════════════════════[/bold cyan]"
    )
    console.print("[bold cyan]  UniFi Network Optimizer - Advanced Features Test[/bold cyan]")
    console.print(
        "[bold cyan]═══════════════════════════════════════════════════════════════[/bold cyan]\n"
    )

    # Load saved credentials
    console.print("[yellow]Loading saved credentials...[/yellow]")
    creds = get_credentials("default")

    if not creds:
        console.print(
            "[red]Error: No saved credentials found. Run optimizer.py first to save credentials.[/red]"
        )
        return False

    host = creds["host"]
    username = creds["username"]
    password = creds["password"]
    site = creds.get("site", "default")

    console.print(f"[green]✓[/green] Host: {host}")
    console.print(f"[green]✓[/green] Username: {username}")
    console.print(f"[green]✓[/green] Site: {site}\n")

    # Connect to controller
    console.print("[yellow]Connecting to UniFi Controller...[/yellow]")
    try:
        client = CloudKeyGen2Client(host, username, password, site=site, verify_ssl=False)
        console.print("[green]✓[/green] Successfully connected!\n")
    except Exception as e:
        console.print(f"[red]✗[/red] Connection failed: {e}\n")
        return False

    # Run analysis with advanced features
    console.print("[bold cyan]Running Expert Network Analysis...[/bold cyan]\n")

    try:
        result = analyze_network(client, site=site, lookback_days=3)

        console.print(
            "\n[bold green]═══════════════════════════════════════════════════════════════[/bold green]"
        )
        console.print("[bold green]  Analysis Complete! Testing Advanced Features...[/bold green]")
        console.print(
            "[bold green]═══════════════════════════════════════════════════════════════[/bold green]\n"
        )

        # Get analysis data
        analysis = result.get("full_analysis")

        if not analysis:
            console.print("[red]✗[/red] Analysis data not available")
            return False

        # Test 1: Network Health Score
        console.print("[bold]Test 1: Network Health Score[/bold]")
        health_score = analysis.get("health_score")
        if health_score:
            score = health_score.get("score", "N/A")
            grade = health_score.get("grade", "N/A")
            status = health_score.get("status", "N/A")
            console.print(f"  [green]✓[/green] Score: {score}/100 (Grade {grade}) - {status}")
            console.print(f"  [blue]Details:[/blue]")
            details = health_score.get("details", {})
            for key, value in details.items():
                if key != "error":
                    console.print(f"    • {key}: {value}")
        else:
            console.print(f"  [red]✗[/red] Health score not calculated")
        console.print()

        # Test 2: DFS Analysis
        console.print("[bold]Test 2: DFS Event Tracking[/bold]")
        dfs_analysis = analysis.get("dfs_analysis")
        if dfs_analysis:
            total_events = dfs_analysis.get("total_events", 0)
            severity = dfs_analysis.get("severity", "ok")
            events_by_ap = dfs_analysis.get("events_by_ap", {})
            console.print(f"  [green]✓[/green] DFS events detected: {total_events}")
            console.print(f"  [green]✓[/green] Severity: {severity}")
            if events_by_ap:
                console.print(f"  [blue]Events by AP:[/blue]")
                for ap_name, events in events_by_ap.items():
                    console.print(f"    • {ap_name}: {len(events)} events")
            else:
                console.print(f"  [green]✓[/green] No DFS events (excellent!)")
        else:
            console.print(f"  [red]✗[/red] DFS analysis not performed")
        console.print()

        # Test 3: Band Steering Analysis
        console.print("[bold]Test 3: Band Steering Analysis[/bold]")
        band_steering = analysis.get("band_steering_analysis")
        if band_steering:
            misplaced = band_steering.get("dual_band_clients_on_2ghz", 0)
            severity = band_steering.get("severity", "ok")
            enabled_count = sum(
                1 for v in band_steering.get("band_steering_enabled", {}).values() if v
            )
            total_aps = len(band_steering.get("band_steering_enabled", {}))
            console.print(f"  [green]✓[/green] Dual-band clients on 2.4GHz: {misplaced}")
            console.print(
                f"  [green]✓[/green] Band steering enabled on {enabled_count}/{total_aps} APs"
            )
            console.print(f"  [green]✓[/green] Severity: {severity}")
        else:
            console.print(f"  [red]✗[/red] Band steering analysis not performed")
        console.print()

        # Test 4: Fast Roaming Validation
        console.print("[bold]Test 4: Fast Roaming Validation[/bold]")
        fast_roaming = analysis.get("fast_roaming_analysis")
        if fast_roaming:
            consistent = fast_roaming.get("consistent_config", True)
            console.print(f"  [green]✓[/green] Configuration consistent: {consistent}")
            roaming_features = fast_roaming.get("roaming_features", {})
            for feature, data in roaming_features.items():
                enabled = data.get("enabled_count", 0)
                disabled = data.get("disabled_count", 0)
                console.print(
                    f"  [green]✓[/green] {feature}: {enabled} enabled, {disabled} disabled"
                )
        else:
            console.print(f"  [red]✗[/red] Fast roaming analysis not performed")
        console.print()

        # Test 5: Airtime Utilization
        console.print("[bold]Test 5: Airtime Utilization Monitoring[/bold]")
        airtime = analysis.get("airtime_analysis")
        if airtime:
            saturated = len(airtime.get("saturated_aps", []))
            total_radios = len(airtime.get("ap_utilization", {}))
            severity = airtime.get("severity", "ok")
            console.print(f"  [green]✓[/green] Radios monitored: {total_radios}")
            console.print(f"  [green]✓[/green] Saturated APs (>70%): {saturated}")
            console.print(f"  [green]✓[/green] Severity: {severity}")
            if total_radios > 0:
                console.print(f"  [blue]Sample utilization:[/blue]")
                for i, (ap_key, data) in enumerate(
                    list(airtime.get("ap_utilization", {}).items())[:3]
                ):
                    airtime_pct = data.get("airtime_pct", 0)
                    clients = data.get("clients", 0)
                    console.print(f"    • {ap_key}: {airtime_pct:.1f}% ({clients} clients)")
        else:
            console.print(f"  [red]✗[/red] Airtime analysis not performed")
        console.print()

        # Test 6: Client Capabilities
        console.print("[bold]Test 6: Client Capability Matrix[/bold]")
        capabilities = analysis.get("client_capabilities")
        if capabilities:
            cap_dist = capabilities.get("capability_distribution", {})
            problem_devices = len(capabilities.get("problem_devices", []))
            console.print(f"  [green]✓[/green] Client standards tracked:")
            for standard, count in cap_dist.items():
                if count > 0:
                    console.print(f"    • {standard}: {count} clients")
            console.print(f"  [green]✓[/green] Problem devices detected: {problem_devices}")
        else:
            console.print(f"  [red]✗[/red] Client capability analysis not performed")
        console.print()

        # Test 7: HTML Report Generation
        console.print("[bold]Test 7: HTML Report Generation[/bold]")
        console.print("  [yellow]Attempting to generate HTML report...[/yellow]")

        try:
            from core.report_v2 import generate_v2_report

            recommendations = result.get("recommendations", [])

            report_path, _ = generate_v2_report(
                analysis_data=analysis,
                recommendations=recommendations,
                site_name=site,
                output_dir="reports",
            )

            console.print(f"  [green]✓[/green] HTML report generated successfully!")
            console.print(f"  [blue]Report saved to:[/blue] {report_path}")

            # Check if file exists
            if os.path.exists(report_path):
                file_size = os.path.getsize(report_path)
                console.print(f"  [green]✓[/green] File size: {file_size:,} bytes")
            else:
                console.print(f"  [red]✗[/red] File not found at expected path")

        except Exception as e:
            console.print(f"  [red]✗[/red] HTML report generation failed: {e}")

        console.print()

        # Summary
        console.print(
            "[bold green]═══════════════════════════════════════════════════════════════[/bold green]"
        )
        console.print("[bold green]  Test Summary[/bold green]")
        console.print(
            "[bold green]═══════════════════════════════════════════════════════════════[/bold green]\n"
        )

        tests_passed = []
        if health_score:
            tests_passed.append("Network Health Score")
        if dfs_analysis:
            tests_passed.append("DFS Event Tracking")
        if band_steering:
            tests_passed.append("Band Steering Analysis")
        if fast_roaming:
            tests_passed.append("Fast Roaming Validation")
        if airtime:
            tests_passed.append("Airtime Utilization")
        if capabilities:
            tests_passed.append("Client Capability Matrix")

        console.print(f"[bold cyan]Tests Passed:[/bold cyan] {len(tests_passed)}/7")
        for test in tests_passed:
            console.print(f"  [green]✓[/green] {test}")

        if len(tests_passed) == 7:
            console.print(
                "\n[bold green]🎉 All advanced features working correctly! 🎉[/bold green]\n"
            )
            return True
        else:
            console.print("\n[bold yellow]⚠ Some features may need attention[/bold yellow]\n")
            return False

    except Exception as e:
        console.print(f"\n[red]✗[/red] Analysis failed with error:\n")
        console.print(f"[red]{type(e).__name__}: {e}[/red]\n")

        import traceback

        console.print("[yellow]Traceback:[/yellow]")
        console.print(traceback.format_exc())

        return False


if __name__ == "__main__":
    success = test_advanced_features()
    sys.exit(0 if success else 1)
