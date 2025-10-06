#!/usr/bin/env python3
"""
UniFi Network Optimizer - Safe Configuration Management
Expert-level analysis with RSSI monitoring, historical lookback, mesh AP optimization
"""

import argparse
import os
import sys
from collections import defaultdict
from getpass import getpass

from rich.console import Console
from rich.panel import Panel

# Add parent directory to path so we can import from api/ and core/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.cloudkey_gen2_client import CloudKeyGen2Client, get_devices
from core.advanced_analyzer import AdvancedNetworkAnalyzer, run_advanced_analysis
from core.change_applier import ChangeApplier
from core.html_report_generator import generate_html_report
from core.html_report_generator_share import generate_html_report as generate_share_html_report
from core.network_analyzer import run_expert_analysis
from core.network_health_analyzer import NetworkHealthAnalyzer
from utils.keychain import (
    delete_profile,
    get_credentials,
    is_keychain_available,
    list_profiles,
    save_credentials,
)

console = Console()


def analyze_network(client, site="default", lookback_days=3):
    """
    Expert-level network analysis with RSSI monitoring, historical data, and mesh optimization

    Args:
        client: CloudKey API client
        site: Site name (default: 'default')
        lookback_days: Days of historical data to analyze (default: 3)

    Returns:
        list: List of recommended changes prioritized by severity and client impact
    """
    console.print(
        Panel(
            "[bold cyan]Expert Network Analysis[/bold cyan]\n"
            f"• Historical lookback: {lookback_days} days\n"
            "• RSSI and signal quality analysis\n"
            "• Mesh AP reliability checks\n"
            "• Channel and power optimization\n"
            "• Client health diagnostics",
            style="cyan",
        )
    )
    console.print()

    # Run expert analysis
    try:
        analysis = run_expert_analysis(client, site, lookback_days)

        # Display AP summary
        ap_analysis = analysis["ap_analysis"]
        console.print(f"[green]✓[/green] Found {ap_analysis['total_aps']} access points")

        if ap_analysis["mesh_aps"]:
            console.print(
                f"[cyan]  • {len(ap_analysis['mesh_aps'])} wireless mesh APs "
                "(reliability-focused)[/cyan]"
            )
        if ap_analysis["wired_aps"]:
            console.print(f"[blue]  • {len(ap_analysis['wired_aps'])} wired uplink APs[/blue]")

        # Display mesh AP details
        if ap_analysis["mesh_aps"]:
            console.print("\n[bold]Mesh AP Uplink Status:[/bold]")
            for mesh_ap in ap_analysis["mesh_aps"]:
                rssi = mesh_ap.get("uplink_rssi")
                if rssi:
                    if rssi > -70:
                        status_color = "green"
                        status = "Excellent"
                    elif rssi > -75:
                        status_color = "blue"
                        status = "Good"
                    elif rssi > -80:
                        status_color = "yellow"
                        status = "Fair"
                    else:
                        status_color = "red"
                        status = "Poor"

                    console.print(
                        f"  [{status_color}]{mesh_ap['name']}: {rssi} dBm ({status})[/{status_color}]"
                    )

        # Display client summary
        client_analysis = analysis["client_analysis"]
        console.print(f"\n[green]✓[/green] Analyzed {client_analysis['total_clients']} clients")

        if client_analysis["weak_signal"]:
            console.print(
                f"[yellow]  ⚠ {len(client_analysis['weak_signal'])} clients with weak signal[/yellow]"
            )
        if client_analysis["frequent_disconnects"]:
            console.print(
                f"[red]  ⚠ {len(client_analysis['frequent_disconnects'])} clients with frequent disconnects[/red]"
            )
        if client_analysis["poor_health"]:
            console.print(
                f"[magenta]  ⚠ {len(client_analysis['poor_health'])} clients with poor health (D/F grade)[/magenta]"
            )

        console.print()

        # Run advanced analysis
        console.print("[bold cyan]Running advanced analysis...[/bold cyan]")
        devices = analysis.get("devices", [])
        clients = analysis.get("clients", [])
        advanced_analysis = run_advanced_analysis(client, site, devices, clients, lookback_days)

        # Run manufacturer analysis
        from core.manufacturer_analyzer import analyze_manufacturers

        manufacturer_analysis = analyze_manufacturers(clients)
        analysis["manufacturer_analysis"] = manufacturer_analysis

        # Add advanced analysis to full analysis
        analysis["dfs_analysis"] = advanced_analysis["dfs_analysis"]
        analysis["band_steering_analysis"] = advanced_analysis["band_steering_analysis"]
        analysis["min_rssi_analysis"] = advanced_analysis["min_rssi_analysis"]
        analysis["fast_roaming_analysis"] = advanced_analysis["fast_roaming_analysis"]
        analysis["airtime_analysis"] = advanced_analysis["airtime_analysis"]
        analysis["client_capabilities"] = advanced_analysis["client_capabilities"]
        analysis["switch_analysis"] = advanced_analysis["switch_analysis"]

        # Merge band steering recommendations into main recommendations list
        band_steering_recs = advanced_analysis["band_steering_analysis"].get("recommendations", [])
        if band_steering_recs:
            # Ensure recommendations list exists
            if "recommendations" not in analysis:
                analysis["recommendations"] = []
            # Add band steering recommendations with proper device info
            for rec in band_steering_recs:
                # Band steering recs have 'device' as name string, need to find full device object
                device_name = rec.get("device", "")
                device_obj = next((d for d in devices if d.get("name") == device_name), None)
                if device_obj:
                    # Add device object to recommendation
                    rec_with_device = rec.copy()
                    rec_with_device["ap"] = {
                        "name": device_obj.get("name", "Unknown"),
                        "mac": device_obj.get("mac", ""),
                        "is_mesh": device_obj.get("adopted", False)
                        and device_obj.get("uplink", {}).get("type") == "wireless",
                    }
                    analysis["recommendations"].append(rec_with_device)

        # Merge min RSSI recommendations into main recommendations list
        min_rssi_recs = advanced_analysis["min_rssi_analysis"].get("recommendations", [])
        if min_rssi_recs:
            # Ensure recommendations list exists
            if "recommendations" not in analysis:
                analysis["recommendations"] = []
            # Add min RSSI recommendations with proper device info
            for rec in min_rssi_recs:
                # Min RSSI recs have 'device' as name string, need to find full device object
                device_name = rec.get("device", "")
                device_obj = next((d for d in devices if d.get("name") == device_name), None)
                if device_obj:
                    # Add device object to recommendation
                    rec_with_device = rec.copy()
                    rec_with_device["ap"] = {
                        "name": device_obj.get("name", "Unknown"),
                        "mac": device_obj.get("mac", ""),
                        "is_mesh": device_obj.get("adopted", False)
                        and device_obj.get("uplink", {}).get("type") == "wireless",
                    }
                    analysis["recommendations"].append(rec_with_device)

        # Run comprehensive network health analysis
        console.print("[bold cyan]Analyzing network health...[/bold cyan]")
        health_analyzer = NetworkHealthAnalyzer(client, site)
        health_analysis = health_analyzer.analyze_network_health(devices, clients)
        analysis["health_analysis"] = health_analysis

        # Calculate network health score
        adv_health_analyzer = AdvancedNetworkAnalyzer(client, site)
        health_score = adv_health_analyzer.calculate_network_health_score(analysis)
        analysis["health_score"] = health_score

        # Check for API errors and add to analysis
        error_summary = client.get_error_summary()
        analysis["api_errors"] = error_summary
        analysis["has_incomplete_data"] = error_summary is not None

        # Display warning if there were API errors
        if error_summary:
            console.print(
                f"\n[yellow]⚠ Warning: {error_summary['total_errors']} API call(s) failed[/yellow]"
            )
            console.print(f"[yellow]  Some analysis features may be incomplete[/yellow]")

            if client.has_critical_errors():
                console.print(
                    f"[red]  ✗ Critical errors detected (authentication/permissions)[/red]"
                )
                console.print(f"[red]  Network grade will not be assigned[/red]")

            if client.verbose:
                console.print(f"\n[dim]Failed endpoints:[/dim]")
                for endpoint in error_summary["failed_endpoints"]:
                    console.print(f"[dim]  • {endpoint}[/dim]")

        # Display detailed summary of advanced findings
        console.print(f"\n[green]✓[/green] Advanced analysis complete\n")

        # Display Client Capability Matrix
        client_caps = advanced_analysis.get("client_capabilities", {})
        if client_caps:
            cap_dist = client_caps.get("capability_distribution", {})
            if sum(cap_dist.values()) > 0:
                console.print("[bold cyan]📊 Client Capability Distribution:[/bold cyan]")
                total = sum(cap_dist.values())
                for standard, count in cap_dist.items():
                    if count > 0:
                        pct = (count / total * 100) if total > 0 else 0
                        if standard == "802.11ax":
                            color = "green"
                            emoji = "🚀"
                        elif standard == "802.11ac":
                            color = "blue"
                            emoji = "⚡"
                        elif standard == "802.11n":
                            color = "yellow"
                            emoji = "📶"
                        else:
                            color = "red"
                            emoji = "⚠️"
                        console.print(
                            f"  [{color}]{emoji} {standard}: {count} clients ({pct:.1f}%)[/{color}]"
                        )

                # Show channel width distribution
                channel_width = client_caps.get("channel_width", {})
                if sum(channel_width.values()) > 0:
                    console.print("\n[bold cyan]📡 Channel Width Usage:[/bold cyan]")
                    for width, count in sorted(channel_width.items(), reverse=True):
                        if count > 0:
                            console.print(f"  • {width}: {count} clients")

                # Show problem devices
                problem_devices = client_caps.get("problem_devices", [])
                if problem_devices:
                    console.print(
                        f"\n[bold red]⚠️  {len(problem_devices)} Legacy Device(s) Detected:[/bold red]"
                    )
                    from rich.table import Table

                    problem_table = Table(show_header=True, header_style="bold red")
                    problem_table.add_column("Client", style="cyan")
                    problem_table.add_column("Connected AP", style="yellow")
                    problem_table.add_column("Signal", style="white")
                    problem_table.add_column("Capability", style="red")

                    for device in problem_devices[:10]:  # Show first 10
                        hostname = device.get("hostname", "Unknown")
                        ap_name = device.get("ap_name", "Unknown")
                        rssi = device.get("rssi", 0)

                        # Format RSSI with color
                        if rssi > 0:
                            rssi = -rssi  # Fix positive RSSI

                        if rssi > -60:
                            rssi_str = f"[green]{rssi} dBm[/green]"
                        elif rssi > -70:
                            rssi_str = f"[yellow]{rssi} dBm[/yellow]"
                        else:
                            rssi_str = f"[red]{rssi} dBm[/red]"

                        capability = device.get("radio_proto", "unknown")

                        problem_table.add_row(hostname, ap_name, rssi_str, capability)

                    console.print(problem_table)
                console.print()

        # Display DFS Analysis
        dfs_analysis = advanced_analysis.get("dfs_analysis", {})
        if dfs_analysis:
            dfs_count = dfs_analysis.get("event_count", 0)
            if dfs_count > 0:
                console.print(
                    f"[yellow]⚠️  DFS Events: {dfs_count} radar interference events detected[/yellow]"
                )
            else:
                console.print(f"[green]✓ DFS Status: No radar interference (excellent!)[/green]")

        # Display Band Steering
        band_steering = advanced_analysis.get("band_steering_analysis", {})
        if band_steering and band_steering.get("capable_aps"):
            capable_count = len(band_steering.get("capable_aps", []))
            enabled_count = len(
                [
                    ap
                    for ap in band_steering.get("capable_aps", [])
                    if ap.get("band_steering_enabled")
                ]
            )
            console.print(
                f"[cyan]📡 Band Steering: {enabled_count}/{capable_count} APs enabled[/cyan]"
            )

        # Display Switch Analysis
        switch_analysis = advanced_analysis.get("switch_analysis", {})
        if switch_analysis and switch_analysis.get("switches"):
            switches = switch_analysis["switches"]
            console.print(f"\n[bold cyan]🔌 Switch Analysis:[/bold cyan]")
            for switch in switches:
                console.print(f"  • {switch['name']} ({switch['model']})")
                console.print(f"    Ports: {switch['active_ports']}/{switch['total_ports']} active")
                if switch["poe_capable"]:
                    poe_util = (
                        (switch["poe_usage"] / switch["poe_max"] * 100)
                        if switch["poe_max"] > 0
                        else 0
                    )
                    poe_color = "red" if poe_util > 90 else "yellow" if poe_util > 75 else "green"
                    console.print(
                        f"    PoE: [{poe_color}]{switch['poe_usage']:.1f}W / {switch['poe_max']}W ({poe_util:.0f}%)[/{poe_color}]"
                    )

                # Show port speed distribution
                speed_dist = {}
                for port in switch["ports"]:
                    if port["up"] and port["enabled"]:
                        speed = f"{port['speed']}M"
                        speed_dist[speed] = speed_dist.get(speed, 0) + 1
                if speed_dist:
                    speeds = ", ".join(
                        [
                            f"{count}x{speed}"
                            for speed, count in sorted(speed_dist.items(), reverse=True)
                        ]
                    )
                    console.print(f"    Speeds: {speeds}")

                # Show ports with issues in detail
                if switch.get("issues"):
                    issue_count = len(switch["issues"])
                    high_issues = len([i for i in switch["issues"] if i.get("severity") == "high"])
                    if high_issues > 0:
                        console.print(
                            f"    [red]⚠️  {issue_count} issues ({high_issues} high priority)[/red]"
                        )
                    else:
                        console.print(f"    [yellow]⚠️  {issue_count} issues[/yellow]")

                    # Show detailed port issues
                    for port in switch["ports"]:
                        if port.get("issues") and port["up"]:
                            port_display = f"Port {port['port_idx']}"
                            if port.get("connected_client"):
                                port_display += f" - {port['connected_client']}"

                            # Mark AP uplink ports with icon (only highlight in red if there are issues)
                            if port.get("is_ap"):
                                # Determine severity of issues
                                has_high_severity = any(
                                    issue.get("severity") == "high"
                                    for issue in port.get("issues", [])
                                )
                                if has_high_severity:
                                    console.print(
                                        f"      [bold red]� {port_display} [AP UPLINK - CRITICAL]:[/bold red]"
                                    )
                                else:
                                    console.print(f"      [cyan]📡 {port_display} [AP]:[/cyan]")
                            else:
                                console.print(f"      [cyan]{port_display}:[/cyan]")

                            for issue in port["issues"]:
                                severity = issue.get("severity", "low")
                                color = {"high": "red", "medium": "yellow", "low": "blue"}.get(
                                    severity, "white"
                                )
                                message = issue.get("message", "Unknown issue")
                                console.print(f"        [{color}]• {message}[/{color}]")

                                if issue.get("impact"):
                                    console.print(f"          Impact: [dim]{issue['impact']}[/dim]")
                                if issue.get("recommendation"):
                                    console.print(
                                        f"          Action: [dim]{issue['recommendation']}[/dim]"
                                    )

        console.print()

        # Display Network Health Analysis
        health_analysis = analysis.get("health_analysis", {})
        if health_analysis:
            overall_score = health_analysis.get("overall_score", 100)
            severity = health_analysis.get("severity", "low")

            # Color code the score
            if overall_score >= 90:
                score_color = "green"
                score_emoji = "✅"
            elif overall_score >= 75:
                score_color = "yellow"
                score_emoji = "⚠️"
            else:
                score_color = "red"
                score_emoji = "🔴"

            console.print(f"[bold cyan]🏥 Network Health Analysis[/bold cyan]")
            console.print(
                f"  Overall Health Score: [{score_color}]{score_emoji} {overall_score}/100[/{score_color}]\n"
            )

            categories = health_analysis.get("categories", {})

            # Display critical issues first
            critical_issues = [
                i for i in health_analysis.get("issues", []) if i.get("severity") == "high"
            ]
            if critical_issues:
                console.print(
                    f"  [bold red]🔴 Critical Issues ({len(critical_issues)}):[/bold red]"
                )
                for issue in critical_issues[:5]:  # Show top 5
                    msg = issue.get("message", "Unknown issue")
                    console.print(f"    • {msg}")
                    if issue.get("recommendation"):
                        console.print(f"      [dim]→ {issue['recommendation']}[/dim]")
                console.print()

            # Display category summaries
            for category_name, category_data in categories.items():
                if not category_data.get("issues"):
                    continue

                category_status = category_data.get("status", "healthy")
                status_colors = {"critical": "red", "warning": "yellow", "healthy": "green"}
                status_emoji = {"critical": "🔴", "warning": "⚠️", "healthy": "✅"}

                color = status_colors.get(category_status, "white")
                emoji = status_emoji.get(category_status, "•")

                display_name = category_data.get(
                    "category", category_name.replace("_", " ").title()
                )
                issue_count = len(category_data.get("issues", []))

                if issue_count > 0:
                    console.print(
                        f"  {emoji} [bold]{display_name}:[/bold] [{color}]{issue_count} issue(s)[/{color}]"
                    )

            console.print()

        # Convert expert recommendations to our format (pass devices for full info)
        recommendations = _convert_expert_recommendations(analysis["recommendations"], devices)

        return {"recommendations": recommendations, "full_analysis": analysis}

    except Exception as e:
        console.print(f"[red]Error during expert analysis: {e}[/red]")
        import traceback

        traceback.print_exc()
        console.print("\n[yellow]Falling back to basic analysis...[/yellow]\n")

        # Fallback to basic analysis
        devices = get_devices(client)
        aps = [d for d in devices if d.get("type") == "uap"]
        return {"recommendations": _generate_basic_recommendations(aps, devices), "full_analysis": None}


def _convert_expert_recommendations(expert_recs, all_devices=None):
    """
    Convert expert analyzer recommendations to change applier format

    Args:
        expert_recs: Recommendations from ExpertNetworkAnalyzer
        all_devices: Full device list from API (needed for _id field)

    Returns:
        list: Recommendations in standard format
    """
    converted = []

    # Create device lookup by MAC
    device_by_mac = {}
    if all_devices:
        device_by_mac = {d.get("mac"): d for d in all_devices}

    for rec in expert_recs:
        ap_info = rec.get("ap")
        if not ap_info:
            continue

        # Get full device object from API data (includes _id)
        ap_mac = ap_info["mac"]
        device = device_by_mac.get(ap_mac)

        if not device:
            # Fallback: create minimal device dict
            console.print(
                f"[yellow]Warning: Could not find full device info for {ap_info['name']}[/yellow]"
            )
            device = {"mac": ap_info["mac"], "name": ap_info["name"], "type": "uap"}

        rec_type = rec.get("type", "")
        issue = rec.get("issue", "")
        priority = rec.get("priority", "low")

        # Convert based on issue type
        if "power" in issue or "power" in rec_type:
            radio = rec.get("radio", {})
            band = rec.get("band", "")
            current_power = radio.get("tx_power_mode", "high")

            # Determine new power based on recommendation
            if "mesh" in issue or ap_info.get("is_mesh"):
                new_power = "medium" if current_power == "high" else "low"
            else:
                new_power = "medium" if current_power == "high" else "low"

            converted.append(
                {
                    "device": device,
                    "action": "power_change",
                    "radio": radio.get("radio", "ng"),
                    "current_power": current_power,
                    "new_power": new_power,
                    "reason": rec.get("message", "") + ". " + rec.get("recommendation", ""),
                    "priority": priority,
                    "affected_clients": rec.get("affected_clients", 0),
                }
            )

        elif "channel" in issue or "channel" in rec_type:
            radio = rec.get("radio", {})
            band = rec.get("band", "")
            current_channel = radio.get("channel", 1)
            # Ensure channel is integer
            try:
                current_channel = int(current_channel)
            except (ValueError, TypeError):
                current_channel = 1

            # Suggest appropriate channel based on band
            if band == "2.4GHz":
                # Suggest different non-overlapping channel
                if current_channel == 1:
                    new_channel = 6
                elif current_channel == 6:
                    new_channel = 11
                else:
                    new_channel = 1
            elif band == "6GHz":
                # 6GHz channels (UNII-5 through UNII-8)
                # Preferred channels: 37, 53, 69, 85, 101, 117, 133, 149, 165, 181, 197, 213
                if current_channel < 37 or current_channel > 213:
                    new_channel = 37  # Start of UNII-5
                elif current_channel == 37:
                    new_channel = 69  # UNII-5 alternative
                elif current_channel in [53, 69]:
                    new_channel = 101  # Move to UNII-6
                elif current_channel in [85, 101]:
                    new_channel = 133  # Move to UNII-7
                else:
                    new_channel = 37  # Return to start
            else:  # 5GHz
                # Suggest non-DFS channel if currently on DFS
                if 52 <= current_channel <= 144:
                    new_channel = 36  # Safe non-DFS channel
                else:
                    new_channel = 149  # Alternative non-DFS

            converted.append(
                {
                    "device": device,
                    "action": "channel_change",
                    "radio": radio.get("radio", "ng"),
                    "current_channel": current_channel,
                    "new_channel": new_channel,
                    "reason": rec.get("message", "") + ". " + rec.get("recommendation", ""),
                    "priority": priority,
                    "affected_clients": rec.get("affected_clients", 0),
                }
            )

        elif "band_steering" in issue or "band_steering" in rec_type or rec_type == "band_steering":
            # Band steering configuration change
            # Check both possible field names for compatibility
            current_mode = device.get("bandsteering_mode") or device.get(
                "band_steering_mode", "off"
            )

            converted.append(
                {
                    "device": device,
                    "action": "band_steering",
                    "current_mode": current_mode,
                    "new_mode": "prefer_5g",  # Enable band steering with 5GHz preference
                    "reason": rec.get("message", "") + ". " + rec.get("recommendation", ""),
                    "priority": priority,
                    "affected_clients": rec.get("affected_clients", 0),
                }
            )

        elif "min_rssi" in issue or "min_rssi" in rec_type or rec_type == "min_rssi_disabled":
            # **DO NOT convert mesh AP min RSSI warnings to actions!**
            # These are warnings to DISABLE min RSSI, not enable it
            if rec.get("is_mesh") or rec_type == "mesh_min_rssi_danger":
                # This is a warning about mesh AP, not an actionable recommendation
                # User needs to manually disable min RSSI on mesh APs
                continue

            # Min RSSI configuration change (wired APs only)
            radio_name = rec.get("radio", "ng")
            band = rec.get("band", "2.4GHz")
            current_enabled = False
            current_value = None

            # Find current radio settings
            radio_table = device.get("radio_table", [])
            for radio in radio_table:
                if radio.get("radio") == radio_name:
                    current_enabled = radio.get("min_rssi_enabled", False)
                    current_value = radio.get("min_rssi", None)
                    break

            recommended_value = rec.get("recommended_value", -75 if radio_name == "ng" else -72)

            converted.append(
                {
                    "device": device,
                    "action": "min_rssi",
                    "radio": radio_name,
                    "band": band,
                    "current_enabled": current_enabled,
                    "current_value": current_value,
                    "new_enabled": True,
                    "new_value": recommended_value,
                    "reason": rec.get("message", "") + ". " + rec.get("recommendation", ""),
                    "priority": priority,
                }
            )

        else:
            # For other issues, create informational recommendation
            # These won't be auto-applied but will be shown to user
            pass

    return converted


def _generate_health_based_recommendations(aps, health_analysis):
    """
    Generate recommendations based on client health diagnostics

    Args:
        aps: List of access point devices
        health_analysis: Client health analysis results

    Returns:
        list: Prioritized recommendations
    """
    recommendations = []

    # Create AP lookup by MAC
    ap_by_mac = {ap.get("mac"): ap for ap in aps}

    # Analyze weak signal clients
    weak_clients = health_analysis.get("weak_clients", [])
    if weak_clients:
        console.print(f"[yellow]Found {len(weak_clients)} clients with weak signal[/yellow]")

        # Group weak clients by AP
        clients_by_ap = defaultdict(list)
        for client in weak_clients:
            ap_mac = client.get("ap_mac")
            if ap_mac:
                clients_by_ap[ap_mac].append(client)

        # Generate recommendations for APs with many weak clients
        for ap_mac, clients in clients_by_ap.items():
            if len(clients) >= 2:  # 2+ weak clients on same AP
                ap = ap_by_mac.get(ap_mac)
                if not ap:
                    continue

                avg_rssi = sum(c.get("rssi", 0) for c in clients) / len(clients)

                # Check if power reduction might help (too much power can cause issues)
                radio_table = ap.get("radio_table", [])
                for radio in radio_table:
                    power = radio.get("tx_power_mode", "auto")
                    radio_name = radio.get("radio")

                    # Check if these weak clients are on this radio's band
                    band_channels = (
                        list(range(1, 15)) if radio_name == "ng" else list(range(36, 166))
                    )
                    client_channels = [c.get("channel", 0) for c in clients]

                    if any(ch in band_channels for ch in client_channels):
                        # CRITICAL: SKIP power recommendations for mesh APs - they need max power!
                        # Also check if this AP is a parent to any mesh APs
                        uplink_type = ap.get("uplink", {}).get("type", "")
                        uplink_rssi = ap.get("uplink", {}).get("rssi")
                        is_wireless_uplink = uplink_type == "wireless"

                        is_mesh = (ap.get("adopted", False) and (
                                   is_wireless_uplink or (uplink_rssi and uplink_rssi < -70)))

                        # Additional safety: Never reduce power if uplink RSSI is weak
                        has_weak_uplink = uplink_rssi and uplink_rssi < -65

                        # Check if this AP is a parent to mesh nodes
                        is_mesh_parent = _is_mesh_parent(ap, aps)

                        if power == "high" and not is_mesh and not has_weak_uplink and not is_mesh_parent:
                            recommendations.append(
                                {
                                    "device": ap,
                                    "action": "power_change",
                                    "radio": radio_name,
                                    "current_power": power,
                                    "new_power": "medium",
                                    "reason": f"{len(clients)} clients with weak signal (avg {avg_rssi:.1f} dBm). High power may cause coverage gaps.",
                                    "priority": "high",
                                    "affected_clients": len(clients),
                                }
                            )

    # Analyze disconnection-prone clients
    disconnect_prone = health_analysis.get("disconnection_prone", [])
    if disconnect_prone:
        console.print(
            f"[red]Found {len(disconnect_prone)} clients with frequent disconnections[/red]"
        )

        # Group by AP
        disconnects_by_ap = defaultdict(list)
        for client in disconnect_prone:
            ap_mac = client.get("ap_mac")
            if ap_mac:
                disconnects_by_ap[ap_mac].append(client)

        # Generate recommendations for problematic APs
        for ap_mac, clients in disconnects_by_ap.items():
            if len(clients) >= 2:  # 2+ problematic clients
                ap = ap_by_mac.get(ap_mac)
                if not ap:
                    continue

                total_disconnects = sum(c.get("disconnect_count", 0) for c in clients)

                radio_table = ap.get("radio_table", [])
                for radio in radio_table:
                    channel = radio.get("channel")
                    radio_name = radio.get("radio")
                    band = "2.4GHz" if radio_name == "ng" else "5GHz"

                    # Recommend channel change to reduce interference
                    if radio_name == "ng" and channel in [1, 6, 11]:
                        # Suggest different non-overlapping channel
                        new_channel = 6 if channel != 6 else 1
                        recommendations.append(
                            {
                                "device": ap,
                                "action": "channel_change",
                                "radio": radio_name,
                                "current_channel": channel,
                                "new_channel": new_channel,
                                "reason": f"{len(clients)} clients experiencing {total_disconnects} disconnections. Channel change may reduce interference.",
                                "priority": "high",
                                "affected_clients": len(clients),
                            }
                        )

    # Analyze roaming issues
    roaming_issues = health_analysis.get("roaming_issues", [])
    if roaming_issues:
        console.print(f"[magenta]Found {len(roaming_issues)} clients with roaming issues[/magenta]")

        # Group by AP
        roaming_by_ap = defaultdict(list)
        for client in roaming_issues:
            # Try to find which AP this client is on
            mac = client.get("mac")
            # Look in health_analysis for current AP
            for ap_mac, ap in ap_by_mac.items():
                roaming_by_ap[ap_mac].append(client)
                break

        # Recommend power reduction to improve roaming
        for ap_mac, clients in roaming_by_ap.items():
            ap = ap_by_mac.get(ap_mac)
            if not ap:
                continue

            radio_table = ap.get("radio_table", [])
            for radio in radio_table:
                power = radio.get("tx_power_mode", "auto")
                radio_name = radio.get("radio")

                # CRITICAL: SKIP power recommendations for mesh APs - they need max power for uplink!
                # Also check if this AP is a parent to any mesh APs
                uplink_type = ap.get("uplink", {}).get("type", "")
                uplink_rssi = ap.get("uplink", {}).get("rssi")
                is_wireless_uplink = uplink_type == "wireless"

                is_mesh = (ap.get("adopted", False) and (
                    is_wireless_uplink or (uplink_rssi and uplink_rssi < -70)))

                # Additional safety: Never reduce power if uplink RSSI is weak
                has_weak_uplink = uplink_rssi and uplink_rssi < -65

                # Check if this AP is a parent to mesh nodes (needs high power for TX to children)
                is_mesh_parent = _is_mesh_parent(ap, aps)

                if power in ["high", "medium"] and not is_mesh and not has_weak_uplink and not is_mesh_parent:
                    new_power = "medium" if power == "high" else "low"
                    recommendations.append(
                        {
                            "device": ap,
                            "action": "power_change",
                            "radio": radio_name,
                            "current_power": power,
                            "new_power": new_power,
                            "reason": f"Roaming issues detected. Reducing power helps clients roam between APs more smoothly.",
                            "priority": "medium",
                            "affected_clients": len(clients),
                        }
                    )

    # Show health score summary
    health_scores = health_analysis.get("health_scores", [])
    if health_scores:
        failing_clients = [c for c in health_scores if c["score"] < 60]
        if failing_clients:
            console.print(f"[red]⚠ {len(failing_clients)} clients with health grade D or F[/red]")

    console.print()

    # Sort by priority and affected clients
    priority_order = {"high": 0, "medium": 1, "low": 2}
    recommendations.sort(
        key=lambda x: (
            priority_order.get(x.get("priority", "low"), 2),
            -x.get("affected_clients", 0),
        )
    )

    return recommendations


def _is_mesh_parent(ap, all_aps):
    """
    Check if this AP is a parent to any mesh nodes

    Args:
        ap: The AP to check
        all_aps: List of all AP devices

    Returns:
        bool: True if this AP is a parent to mesh nodes
    """
    ap_mac = ap.get("mac")
    if not ap_mac:
        return False

    # Check if any other AP has this AP as its parent
    for other_ap in all_aps:
        uplink = other_ap.get("uplink", {})
        uplink_type = uplink.get("type", "")
        parent_mac = uplink.get("uplink_remote_mac")

        # If another AP has wireless uplink pointing to this AP
        if uplink_type == "wireless" and parent_mac == ap_mac:
            return True

    return False


def _get_parent_ap_info(mesh_ap, all_aps):
    """
    Get parent AP information for a mesh AP

    Args:
        mesh_ap: The mesh AP device
        all_aps: List of all AP devices

    Returns:
        tuple: (parent_ap, parent_name, parent_power_status)
    """
    uplink = mesh_ap.get("uplink", {})
    parent_mac = uplink.get("uplink_remote_mac")

    if not parent_mac:
        return None, None, None

    # Find parent AP by MAC
    parent_ap = None
    for ap in all_aps:
        if ap.get("mac") == parent_mac:
            parent_ap = ap
            break

    if not parent_ap:
        return None, None, None

    parent_name = parent_ap.get("name", "Unknown Parent")

    # Check parent's power settings on relevant radios
    # The mesh uplink is typically on 5GHz or 6GHz
    radio_table = parent_ap.get("radio_table", [])
    parent_power_status = {
        "has_low_power": False,
        "low_power_bands": [],
        "power_modes": {}
    }

    for radio in radio_table:
        radio_name = radio.get("radio")
        power_mode = radio.get("tx_power_mode", "auto")

        # Map radio names to bands
        if radio_name == "ng":
            band = "2.4GHz"
        elif radio_name == "na":
            band = "5GHz"
        elif radio_name == "6e":
            band = "6GHz"
        else:
            band = radio_name

        parent_power_status["power_modes"][band] = power_mode

        # Low or medium power on 5/6GHz can hurt mesh uplinks
        if band in ["5GHz", "6GHz"] and power_mode in ["low", "medium"]:
            parent_power_status["has_low_power"] = True
            parent_power_status["low_power_bands"].append(band)

    return parent_ap, parent_name, parent_power_status


def _generate_basic_recommendations(aps, all_devices=None):
    """
    Generate basic recommendations without client health data

    Args:
        aps: List of access point devices
        all_devices: List of all devices (APs, switches, gateways, etc.) for mesh parent detection

    Returns:
        list: Basic recommendations
    """
    recommendations = []

    if all_devices is None:
        all_devices = aps

    console.print("[dim]Using basic analysis mode (client health data not available)[/dim]\n")

    # Build AP and device lookups for parent checking
    ap_by_mac = {ap.get("mac"): ap for ap in aps if ap.get("mac")}
    device_by_mac = {d.get("mac"): d for d in all_devices if d.get("mac")}
    ap_macs = set(ap_by_mac.keys())

    # Build mesh topology summary
    mesh_aps = []
    mesh_parent_macs = set()
    for ap in aps:
        uplink_type = ap.get("uplink", {}).get("type", "")
        uplink_rssi = ap.get("uplink", {}).get("rssi")
        is_mesh = (ap.get("adopted", False) and (
                   uplink_type == "wireless" or (uplink_rssi and uplink_rssi < -70)))
        if is_mesh:
            mesh_aps.append(ap)
            parent_mac = ap.get("uplink", {}).get("uplink_remote_mac")
            if parent_mac:
                mesh_parent_macs.add(parent_mac)

    # Count parent APs vs parent gateways
    mesh_parent_aps = len([mac for mac in mesh_parent_macs if mac in ap_macs])
    mesh_parent_gateways = len([mac for mac in mesh_parent_macs if mac not in ap_macs])

    # Display mesh topology summary if any mesh APs exist
    if mesh_aps:
        from rich.panel import Panel

        # Build parent description
        if mesh_parent_aps > 0 and mesh_parent_gateways > 0:
            parent_line = f"[blue]📡 Parent APs: {mesh_parent_aps} | Parent Gateways: {mesh_parent_gateways}[/blue]"
        elif mesh_parent_aps > 0:
            parent_line = f"[blue]📡 Parent APs: {mesh_parent_aps}[/blue]"
        elif mesh_parent_gateways > 0:
            parent_line = f"[blue]📡 Parent Gateways: {mesh_parent_gateways}[/blue]"
        else:
            parent_line = f"[blue]📡 Parent Devices: {len(mesh_parent_macs)}[/blue]"

        mesh_summary_lines = [
            f"[bold]Mesh Network Detected[/bold]",
            f"",
            f"[cyan]🔗 Mesh Child APs: {len(mesh_aps)}[/cyan]",
            parent_line,
            f"[green]🛡️  Protected APs: {len(mesh_aps) + mesh_parent_aps}[/green]",
            f"",
            f"[dim]All mesh nodes maintain HIGH power for reliable wireless backhaul[/dim]"
        ]
        console.print(Panel(
            "\n".join(mesh_summary_lines),
            title="🔗 Mesh Topology",
            border_style="cyan",
            padding=(1, 2)
        ))
        console.print()

    # Analyze each AP
    for ap in aps:
        ap_name = ap.get("name", "Unnamed AP")
        radio_table = ap.get("radio_table", [])

        # Check if this is a mesh AP - DO NOT recommend power changes for mesh!
        # Check multiple indicators for mesh AP detection
        uplink_type = ap.get("uplink", {}).get("type", "")
        is_wireless_uplink = uplink_type == "wireless"
        uplink_rssi = ap.get("uplink", {}).get("rssi")
        uplink_remote_mac = ap.get("uplink", {}).get("uplink_remote_mac")

        # Enhanced mesh detection: wireless uplink is PRIMARY indicator
        # Only use RSSI as secondary check for very weak uplinks (< -70 dBm suggests wireless)
        is_mesh = (ap.get("adopted", False) and (
                   is_wireless_uplink or (uplink_rssi and uplink_rssi < -70)))

        mesh_label = " [MESH]" if is_mesh else ""

        # Enhanced mesh info with parent AP checking
        parent_info = ""
        parent_warning = None
        if is_mesh and uplink_remote_mac:
            parent_ap, parent_name, parent_power = _get_parent_ap_info(ap, aps)
            if parent_name:
                parent_info = f" → Parent: {parent_name}"
                mesh_label = f" [MESH - Uplink: {uplink_rssi} dBm{parent_info}]"

                # Check if parent has low power that could hurt mesh uplink
                if parent_power and parent_power.get("has_low_power"):
                    low_bands = ", ".join(parent_power.get("low_power_bands", []))
                    parent_warning = (
                        f"  [yellow]⚠️  Parent AP '{parent_name}' has reduced power on {low_bands}[/yellow]\n"
                        f"  [yellow]   This may weaken the mesh uplink (TX from parent → this AP)[/yellow]"
                    )
        elif is_mesh and uplink_rssi:
            mesh_label = f" [MESH - Uplink: {uplink_rssi} dBm]"

        # Enhanced header with role indicators
        is_mesh_parent = _is_mesh_parent(ap, aps)
        role_badges = ""
        if is_mesh:
            role_badges += " [yellow]🔗 MESH-CHILD[/yellow]"
        if is_mesh_parent:
            role_badges += " [blue]📡 MESH-PARENT[/blue]"

        console.print(f"\n[bold cyan]━━━ {ap_name} {role_badges} ━━━[/bold cyan]")
        if mesh_label and "[MESH" in mesh_label:
            console.print(f"[dim]{mesh_label}[/dim]")

        # If mesh AP, show enhanced warning about power requirements (both sides of connection)
        if is_mesh:
            # Determine signal quality for visual feedback
            if uplink_rssi:
                if uplink_rssi > -65:
                    signal_emoji = "🟢"
                    signal_text = "Excellent"
                    signal_color = "green"
                elif uplink_rssi > -70:
                    signal_emoji = "🔵"
                    signal_text = "Good"
                    signal_color = "cyan"
                elif uplink_rssi > -75:
                    signal_emoji = "🟡"
                    signal_text = "Fair"
                    signal_color = "yellow"
                else:
                    signal_emoji = "🔴"
                    signal_text = "Weak"
                    signal_color = "red"

                console.print(f"  [{signal_color}]{signal_emoji} Uplink: {uplink_rssi} dBm ({signal_text})[/{signal_color}]")

                # Enhanced bidirectional power requirements
                if uplink_rssi < -70:
                    console.print(f"  [yellow]⚠️  Bidirectional Power Requirements:[/yellow]")
                    console.print(f"  [yellow]   ← RX: Child receiving weak signal from parent[/yellow]")
                    console.print(f"  [yellow]   → TX: Child needs HIGH power to reach parent[/yellow]")
                    console.print(f"  [yellow]   → TX: Parent needs HIGH power to reach child[/yellow]")
                    console.print(f"  [green]   🛡️  Both APs protected from power reduction[/green]")
                else:
                    console.print(f"  [green]🛡️  Power Protected - Maintaining HIGH power for reliable mesh[/green]")

            # Parent side (TX): Show if parent has low power
            if parent_warning:
                console.print(parent_warning)

        for radio in radio_table:
            radio_name = radio.get("radio")
            channel = radio.get("channel")
            power = radio.get("tx_power_mode", "auto")
            ht = radio.get("ht", 20)

            band = "2.4GHz" if radio_name == "ng" else "5GHz"
            console.print(f"  {band}: Channel {channel}, Power {power}, Width {ht}MHz")

            # Basic recommendations
            # CRITICAL: NEVER reduce power on mesh APs - they need max power for uplink!
            # Also check if this AP is a parent to mesh nodes - parents need high power too!
            is_mesh_parent = _is_mesh_parent(ap, aps)

            if power == "high" and not is_mesh and not is_mesh_parent:
                recommendations.append(
                    {
                        "device": ap,
                        "action": "power_change",
                        "radio": radio_name,
                        "current_power": power,
                        "new_power": "medium",
                        "reason": "Reduce co-channel interference and improve roaming",
                        "priority": "low",
                    }
                )
            elif is_mesh_parent:
                console.print(f"  [green]🛡️  Mesh Parent - Maintaining HIGH power (TX → children)[/green]")

        console.print()

    return recommendations


def display_recommendations(recommendations):
    """Display optimization recommendations with priority and impact"""
    if not recommendations:
        console.print("[green]✓ No optimization recommendations at this time.[/green]")
        console.print("[green]✓ Your network appears to be well-configured![/green]")
        return

    # Count by priority
    high_priority = len([r for r in recommendations if r.get("priority") == "high"])
    medium_priority = len([r for r in recommendations if r.get("priority") == "medium"])
    low_priority = len([r for r in recommendations if r.get("priority") == "low"])

    summary = f"[bold]Found {len(recommendations)} Optimization Opportunities[/bold]\n"
    if high_priority:
        summary += f"[red]● {high_priority} High Priority[/red] "
    if medium_priority:
        summary += f"[yellow]● {medium_priority} Medium Priority[/yellow] "
    if low_priority:
        summary += f"[blue]● {low_priority} Low Priority[/blue]"

    console.print(Panel(summary, style="yellow"))
    console.print()

    for i, rec in enumerate(recommendations, 1):
        device_name = rec["device"].get("name", "Unnamed AP")
        action = rec["action"]
        priority = rec.get("priority", "low")
        affected_clients = rec.get("affected_clients", 0)

        # Priority indicator
        priority_colors = {"high": "red", "medium": "yellow", "low": "blue"}
        priority_color = priority_colors.get(priority, "white")
        priority_label = f"[{priority_color}]{priority.upper()}[/{priority_color}]"

        console.print(f"[bold cyan]{i}. {device_name}[/bold cyan] {priority_label}")

        if action == "channel_change":
            band = "2.4GHz" if rec["radio"] == "ng" else "5GHz"
            console.print(
                f"   Change {band} channel: {rec['current_channel']} → {rec['new_channel']}"
            )
            console.print(f"   Reason: {rec['reason']}")
            if affected_clients > 0:
                console.print(f"   [dim]Affects {affected_clients} client(s)[/dim]")

        elif action == "power_change":
            band = "2.4GHz" if rec["radio"] == "ng" else "5GHz"
            console.print(f"   Change {band} power: {rec['current_power']} → {rec['new_power']}")
            console.print(f"   Reason: {rec['reason']}")
            if affected_clients > 0:
                console.print(f"   [dim]Affects {affected_clients} client(s)[/dim]")

        elif action == "band_steering":
            console.print(f"   Enable band steering: {rec['current_mode']} → {rec['new_mode']}")
            console.print(f"   Reason: {rec['reason']}")
            if affected_clients > 0:
                console.print(f"   [dim]Will help {affected_clients} dual-band client(s)[/dim]")

        elif action == "min_rssi":
            band = rec.get("band", "2.4GHz")
            current_display = f"{'enabled' if rec['current_enabled'] else 'disabled'}"
            if rec["current_value"]:
                current_display += f" ({rec['current_value']} dBm)"
            new_display = f"{'enabled' if rec['new_enabled'] else 'disabled'}"
            if rec["new_enabled"]:
                new_display += f" ({rec['new_value']} dBm)"
            console.print(f"   Configure Min RSSI ({band}): {current_display} → {new_display}")
            console.print(f"   Reason: {rec['reason']}")

        console.print()


def display_rssi_histogram(analysis):
    """Display colorful RSSI histogram showing client signal distribution"""
    client_analysis = analysis.get("client_analysis", {})

    # Get signal distribution from client analysis
    signal_dist = client_analysis.get("signal_distribution", {})

    if not signal_dist:
        return

    console.print("\n")
    console.print(
        Panel(
            "[bold cyan]Client RSSI Distribution[/bold cyan]\n"
            f"Historical data over {analysis.get('lookback_days', 3)} days",
            style="cyan",
        )
    )
    console.print()

    # Calculate percentages and create horizontal bars
    total = sum(signal_dist.values())

    if total == 0:
        console.print("[yellow]No wireless clients found[/yellow]\n")
        return

    # Define categories with colors and thresholds
    categories = [
        ("Excellent (> -50 dBm)", signal_dist.get("excellent", 0), "bright_green"),
        ("Good (-50 to -60 dBm)", signal_dist.get("good", 0), "green"),
        ("Fair (-60 to -70 dBm)", signal_dist.get("fair", 0), "yellow"),
        ("Poor (-70 to -80 dBm)", signal_dist.get("poor", 0), "red"),
        ("Critical (< -80 dBm)", signal_dist.get("critical", 0), "bright_red"),
    ]

    # Show wired clients separately if present
    wired_count = signal_dist.get("wired", 0)

    max_bar_width = 50

    for label, count, color in categories:
        if count == 0:
            continue

        percentage = (count / total) * 100
        bar_length = int((count / total) * max_bar_width)
        bar = "█" * bar_length

        console.print(
            f"[{color}]{label:25}[/{color}] [{color}]{bar}[/{color}] {count:3d} ({percentage:5.1f}%)"
        )

    if wired_count > 0:
        console.print(
            f"[bright_blue]{'Wired Clients':25}[/bright_blue] [bright_blue]{'━' * min(wired_count, max_bar_width)}[/bright_blue] {wired_count:3d}"
        )

    console.print()


def display_quick_health_dashboard(analysis, recommendations):
    """Display a quick, scannable health dashboard at the top"""
    from rich.table import Table

    # Get health score
    health_score = analysis.get("health_score", {})
    score = health_score.get("score", 0)
    grade = health_score.get("grade", "N/A")

    # Grade-based color
    if grade == "A":
        grade_color = "bold green"
        grade_emoji = "🟢"
    elif grade == "B":
        grade_color = "bold blue"
        grade_emoji = "🔵"
    elif grade == "C":
        grade_color = "bold yellow"
        grade_emoji = "🟡"
    elif grade == "D":
        grade_color = "bold orange3"
        grade_emoji = "🟠"
    else:
        grade_color = "bold red"
        grade_emoji = "🔴"

    # Count issues by severity
    critical_count = len(
        [r for r in recommendations if r.get("priority") == "high" or r.get("severity") == "high"]
    )
    warning_count = len(
        [
            r
            for r in recommendations
            if r.get("priority") == "medium" or r.get("severity") == "medium"
        ]
    )
    info_count = len(
        [r for r in recommendations if r.get("priority") == "low" or r.get("severity") == "low"]
    )

    # Get advanced analysis metrics
    band_steering = analysis.get("band_steering_analysis", {})
    wifi7_clients = band_steering.get("wifi7_clients_suboptimal", 0)
    tri_band_clients = band_steering.get("tri_band_clients_suboptimal", 0)
    misplaced_clients = band_steering.get("dual_band_clients_on_2ghz", 0)

    airtime_analysis = analysis.get("airtime_analysis", {})
    saturated_aps = len(airtime_analysis.get("saturated_aps", []))

    dfs_analysis = analysis.get("dfs_analysis", {})
    dfs_events = dfs_analysis.get("total_events", 0)

    # Build dashboard
    console.print("\n" + "=" * 80)
    console.print(
        Panel.fit(
            f"[{grade_color}]NETWORK HEALTH: {grade_emoji} Grade {grade} ({score}/100)[/{grade_color}]",
            style=grade_color,
            border_style=grade_color,
        )
    )

    # Create issues table
    if critical_count > 0 or warning_count > 0 or info_count > 0:
        issues_table = Table(show_header=False, box=None, padding=(0, 2))
        issues_table.add_column("Icon", style="bold", width=4)
        issues_table.add_column("Severity", width=15)
        issues_table.add_column("Count", justify="right", width=8)

        if critical_count > 0:
            issues_table.add_row(
                "🔴", "[bold red]Critical", f"[bold red]{critical_count}[/bold red]"
            )
        if warning_count > 0:
            issues_table.add_row(
                "🟡", "[bold yellow]Warning", f"[bold yellow]{warning_count}[/bold yellow]"
            )
        if info_count > 0:
            issues_table.add_row("🔵", "[bold cyan]Info", f"[bold cyan]{info_count}[/bold cyan]")

        console.print(issues_table)
    else:
        console.print("[bold green]✨ No issues found - network is optimized![/bold green]")

    # Display key metrics if any issues exist
    if wifi7_clients > 0 or tri_band_clients > 0 or saturated_aps > 0 or dfs_events > 0:
        console.print("\n[bold]Key Findings:[/bold]")
        metrics_table = Table(show_header=False, box=None, padding=(0, 2))
        metrics_table.add_column("Icon", width=4)
        metrics_table.add_column("Metric", width=45)
        metrics_table.add_column("Value", justify="right")

        if wifi7_clients > 0:
            metrics_table.add_row(
                "📡",
                "[magenta]WiFi 7 clients on suboptimal bands",
                f"[bold magenta]{wifi7_clients}[/bold magenta]",
            )
        elif tri_band_clients > 0:
            metrics_table.add_row(
                "📡",
                "[blue]WiFi 6E clients on suboptimal bands",
                f"[bold blue]{tri_band_clients}[/bold blue]",
            )

        if misplaced_clients > 0 and wifi7_clients == 0 and tri_band_clients == 0:
            metrics_table.add_row(
                "📡",
                "[yellow]Clients on suboptimal bands",
                f"[bold yellow]{misplaced_clients}[/bold yellow]",
            )

        if saturated_aps > 0:
            metrics_table.add_row(
                "📶",
                "[red]APs with high airtime utilization",
                f"[bold red]{saturated_aps}[/bold red]",
            )

        if dfs_events > 0:
            metrics_table.add_row(
                "⚠️ ",
                "[orange3]DFS radar interference events",
                f"[bold orange3]{dfs_events}[/bold orange3]",
            )

        console.print(metrics_table)

    console.print("=" * 80 + "\n")


def display_executive_summary(analysis, recommendations, lookback_days):
    """Display executive summary of findings and recommended actions"""
    console.print(Panel("[bold magenta]Executive Summary[/bold magenta]", style="magenta"))
    console.print()

    # Display Network Health Score prominently
    health_score = analysis.get("health_score", {})
    if health_score:
        score = health_score.get("score", 0)
        grade = health_score.get("grade", "N/A")
        status = health_score.get("status", "Unknown")

        # Color code by grade
        if grade == "A":
            grade_color = "bold green"
        elif grade == "B":
            grade_color = "bold blue"
        elif grade == "C":
            grade_color = "bold yellow"
        elif grade == "D":
            grade_color = "bold orange3"
        else:
            grade_color = "bold red"

        console.print(
            f"[{grade_color}]Network Health Score: {score}/100 (Grade {grade}) - {status}[/{grade_color}]"
        )
        console.print()

    # Analyze what was found
    ap_analysis = analysis.get("ap_analysis", {})
    client_analysis = analysis.get("client_analysis", {})
    expert_recs = analysis.get("recommendations", [])

    total_aps = ap_analysis.get("total_aps", 0)
    total_clients = client_analysis.get("total_clients", 0)
    mesh_aps = len(ap_analysis.get("mesh_aps", []))

    weak_signal = len(client_analysis.get("weak_signal", []))
    frequent_disconnects = len(client_analysis.get("frequent_disconnects", []))
    poor_health = len(client_analysis.get("poor_health", []))

    # Count issues by type
    channel_issues = len([r for r in expert_recs if r.get("type") == "channel_optimization"])
    power_issues = len([r for r in expert_recs if "power" in r.get("type", "")])
    mesh_issues = len([r for r in expert_recs if "mesh" in r.get("type", "")])

    # Advanced analysis findings
    dfs_analysis = analysis.get("dfs_analysis", {})
    dfs_events = dfs_analysis.get("total_events", 0)

    band_steering = analysis.get("band_steering_analysis", {})
    misplaced_clients = band_steering.get("dual_band_clients_on_2ghz", 0)
    wifi7_clients = band_steering.get("wifi7_clients_suboptimal", 0)
    tri_band_clients = band_steering.get("tri_band_clients_suboptimal", 0)

    airtime_analysis = analysis.get("airtime_analysis", {})
    saturated_aps = len(airtime_analysis.get("saturated_aps", []))

    # Build summary paragraphs
    summary_parts = []

    # Network overview
    overview = f"Your network consists of [bold cyan]{total_aps} access points[/bold cyan] serving [bold cyan]{total_clients} clients[/bold cyan] over the past {lookback_days} days. "

    if mesh_aps > 0:
        overview += f"This includes [bold]{mesh_aps} wireless mesh AP(s)[/bold], which require special attention for reliability. "

    # Advanced findings
    if dfs_events > 0:
        overview += f"[yellow]Detected {dfs_events} DFS radar event(s)[/yellow] which may cause intermittent disconnects. "

    if misplaced_clients > 0:
        if wifi7_clients > 0:
            overview += f"[yellow]{misplaced_clients} client(s) on suboptimal bands (including {wifi7_clients} WiFi 7 capable)[/yellow]. "
        elif tri_band_clients > 0:
            overview += f"[yellow]{misplaced_clients} client(s) on suboptimal bands (including {tri_band_clients} WiFi 6E capable)[/yellow]. "
        else:
            overview += (
                f"[yellow]{misplaced_clients} dual-band client(s) stuck on 2.4GHz[/yellow]. "
            )

    if saturated_aps > 0:
        overview += f"[red]{saturated_aps} AP(s) experiencing high airtime utilization[/red]. "

    # Issues found
    issues_found = []
    if weak_signal > 0:
        issues_found.append(f"[yellow]{weak_signal} client(s) with weak signal strength[/yellow]")
    if frequent_disconnects > 0:
        issues_found.append(
            f"[red]{frequent_disconnects} client(s) with frequent disconnections[/red]"
        )
    if poor_health > 0:
        issues_found.append(
            f"[magenta]{poor_health} client(s) with poor health scores (D/F)[/magenta]"
        )

    if issues_found:
        overview += "Analysis identified " + ", ".join(issues_found) + ". "
    else:
        overview += "[green]All clients show good signal strength and stable connections.[/green] "

    summary_parts.append(overview)

    # Recommendations impact
    if len(recommendations) == 0:
        impact = "[bold green]Your network is well-optimized and no changes are recommended at this time.[/bold green] Continue monitoring for any degradation in client experience."
    else:
        impact = f"[bold yellow]{len(recommendations)} optimization opportunities[/bold yellow] were identified. "

        impact_details = []
        if channel_issues > 0:
            impact_details.append(f"{channel_issues} channel optimization(s)")
        if power_issues > 0:
            impact_details.append(f"{power_issues} power adjustment(s)")
        if mesh_issues > 0:
            impact_details.append(f"{mesh_issues} mesh AP reliability improvement(s)")

        if impact_details:
            impact += "These include " + ", ".join(impact_details) + ". "

        # Expected improvements
        improvements = []
        if weak_signal > 0:
            improvements.append("improved signal strength for weak clients")
        if frequent_disconnects > 0:
            improvements.append("reduced disconnections")
        if mesh_issues > 0:
            improvements.append("enhanced mesh AP reliability")
        if channel_issues > 0:
            improvements.append("reduced co-channel interference")

        if improvements:
            impact += f"Implementing these changes should result in {', '.join(improvements)}. "

        impact += "[bold cyan]Each change will cause brief client disconnections (5-10 seconds)[/bold cyan] as APs restart their radios. "
        impact += "Use [bold]dry-run mode[/bold] to preview impact, or [bold]interactive mode[/bold] to approve changes individually."

    summary_parts.append(impact)

    # Print summary
    for i, part in enumerate(summary_parts):
        console.print(part)
        if i < len(summary_parts) - 1:
            console.print()

    console.print()


def apply_recommendations(client, recommendations, dry_run=False, interactive=True):
    """Apply recommendations with approval"""

    if dry_run:
        console.print(
            Panel(
                "[bold cyan]DRY RUN MODE[/bold cyan]\n"
                "Changes will be simulated but not actually applied",
                style="cyan",
            )
        )
    elif interactive:
        console.print(
            Panel(
                "[bold yellow]INTERACTIVE MODE[/bold yellow]\n"
                "You will be prompted to approve each change",
                style="yellow",
            )
        )
    else:
        console.print(
            Panel(
                "[bold red]AUTOMATIC MODE[/bold red]\n" "All changes will be applied automatically",
                style="red",
            )
        )

    console.print()

    # Create applier
    applier = ChangeApplier(client, dry_run=dry_run, interactive=interactive)

    # Apply each recommendation
    for rec in recommendations:
        action = rec["action"]

        if action == "channel_change":
            applier.apply_channel_change(rec["device"], rec["radio"], rec["new_channel"])

        elif action == "power_change":
            applier.apply_power_change(rec["device"], rec["radio"], rec["new_power"])

        elif action == "band_steering":
            applier.apply_band_steering(rec["device"], rec["new_mode"])

        elif action == "min_rssi":
            applier.apply_min_rssi(
                rec["device"], rec["radio"], rec["new_enabled"], rec["new_value"]
            )

    # Generate report
    applier.generate_report()


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="UniFi Network Optimizer - Safe Configuration Management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze and show recommendations (safe, read-only)
  python3 optimize_network.py analyze --host https://YOUR_CONTROLLER_IP --username admin

  # Dry-run: See what would change without making actual changes
  python3 optimize_network.py apply --host https://YOUR_CONTROLLER_IP --username admin --dry-run

  # Interactive: Approve each change individually
  python3 optimize_network.py apply --host https://YOUR_CONTROLLER_IP --username admin --interactive

  # Automatic: Apply all changes without prompts (use with caution!)
  python3 optimize_network.py apply --host https://YOUR_CONTROLLER_IP --username admin --yes
        """,
    )

    parser.add_argument(
        "command",
        choices=["analyze", "apply"],
        help="analyze: Show recommendations only | apply: Apply changes",
    )
    parser.add_argument("--host", help="Controller URL")
    parser.add_argument("--username", help="Username")
    parser.add_argument("--password", help="Password (will prompt if not provided)")
    parser.add_argument("--site", default="default", help="Site name (default: default)")
    parser.add_argument("--profile", help="Use saved profile (ignores other connection args)")
    parser.add_argument("--save-profile", help="Save connection as profile with this name")
    parser.add_argument("--list-profiles", action="store_true", help="List saved profiles and exit")
    parser.add_argument("--delete-profile", help="Delete saved profile and exit")
    parser.add_argument(
        "--lookback", type=int, default=3, help="Days of historical data to analyze (default: 3)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Simulate changes without applying (safe)"
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        default=True,
        help="Prompt for approval before each change (default)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Apply all changes automatically without prompts (dangerous!)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed error messages and debugging information",
    )

    args = parser.parse_args()

    # Handle profile management commands
    if args.list_profiles:
        profiles = list_profiles()
        if not profiles:
            console.print("[yellow]No saved profiles found[/yellow]")
        else:
            console.print("\n[bold cyan]Saved Profiles:[/bold cyan]\n")
            for name, info in profiles.items():
                console.print(f"[green]●[/green] [bold]{name}[/bold]")
                console.print(f"  Host: {info['host']}")
                console.print(f"  User: {info['username']}")
                console.print(f"  Site: {info.get('site', 'default')}\n")
        return 0

    if args.delete_profile:
        if delete_profile(args.delete_profile):
            return 0
        else:
            return 1

    # Get connection details from profile or arguments
    used_profile = False  # Track if credentials came from a profile

    if args.profile:
        console.print(f"[cyan]Loading profile '{args.profile}'...[/cyan]")
        creds = get_credentials(args.profile)

        if not creds:
            console.print(f"[red]Profile '{args.profile}' not found[/red]")
            console.print("[dim]Use --list-profiles to see available profiles[/dim]")
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
            console.print("[dim]Tip: Save a default profile with --save-profile default[/dim]")
            return 1
    else:
        # Use command-line arguments
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
            "[bold]UniFi Network Optimizer[/bold]\n"
            f"Controller: {host}\n"
            f"Site: {site}\n"
            f"Mode: {args.command.upper()}",
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

            client = CloudKeyGen2Client(
                args.host, args.username, password, args.site, verbose=args.verbose
            )

            if client.login():
                # Login successful - save/update credentials appropriately
                if is_keychain_available():
                    if used_profile and attempt > 1:
                        # Password was corrected after initial failure - update the profile
                        profile_name = args.profile if args.profile else "default"
                        save_credentials(profile_name, host, username, password, site)
                        console.print(f"[dim]✓ Updated password for profile '{profile_name}'[/dim]")
                    elif not used_profile and attempt == 1:
                        # Manually entered credentials on first attempt - auto-save as profile
                        if args.save_profile:
                            # User specified profile name via --save-profile
                            profile_name = args.save_profile
                        else:
                            # Auto-save as 'default' profile
                            profile_name = "default"

                        save_credentials(profile_name, host, username, password, site)
                        console.print(f"[dim]✓ Credentials saved as profile '{profile_name}'[/dim]")

                break  # Continue with analysis
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

    # Analyze network with expert analysis
    result = analyze_network(client, site, args.lookback)
    recommendations = result["recommendations"]
    full_analysis = result.get("full_analysis")

    if args.command == "analyze":
        # Show quick health dashboard FIRST for immediate visibility
        if full_analysis:
            display_quick_health_dashboard(full_analysis, recommendations)

        # Show RSSI histogram
        if full_analysis:
            display_rssi_histogram(full_analysis)

        # Show recommendations
        display_recommendations(recommendations)

        # Show executive summary LAST so it stays on screen
        if full_analysis:
            display_executive_summary(full_analysis, recommendations, args.lookback)

            # Generate HTML reports (both versions)
            try:
                report_path = generate_html_report(full_analysis, recommendations, site)
                console.print(
                    f"\n[green]📄 HTML Report generated:[/green] [cyan]{report_path}[/cyan]"
                )

                # Also generate sharing-friendly version with static images
                share_report_path = generate_share_html_report(full_analysis, recommendations, site)
                console.print(
                    f"[green]📱 Sharing version:[/green] [cyan]{share_report_path}[/cyan]"
                )
                console.print(f"[dim]   (Works in email/iMessage - uses static images)[/dim]")
            except Exception as e:
                console.print(f"\n[yellow]⚠️  Could not generate HTML report: {e}[/yellow]")

        console.print("\n[dim]Tip: Use 'apply --dry-run' to see detailed impact of changes[/dim]")

    elif args.command == "apply":
        if not recommendations:
            console.print("[green]No changes needed - network is optimized![/green]")
            return 0

        # Show quick health dashboard FIRST for immediate visibility
        if full_analysis:
            display_quick_health_dashboard(full_analysis, recommendations)

        # Show RSSI histogram
        if full_analysis:
            display_rssi_histogram(full_analysis)

        # Show recommendations
        display_recommendations(recommendations)

        # Determine mode
        interactive = not args.yes if not args.dry_run else False

        # Apply recommendations (dry-run or real)
        apply_recommendations(
            client, recommendations, dry_run=args.dry_run, interactive=interactive
        )

        # Show executive summary LAST so it stays on screen with apply results
        if full_analysis:
            console.print()  # Extra space before summary
            display_executive_summary(full_analysis, recommendations, args.lookback)

            # Generate HTML reports (both versions)
            try:
                report_path = generate_html_report(full_analysis, recommendations, site)
                console.print(
                    f"\n[green]📄 HTML Report generated:[/green] [cyan]{report_path}[/cyan]"
                )

                # Also generate sharing-friendly version with static images
                share_report_path = generate_share_html_report(full_analysis, recommendations, site)
                console.print(
                    f"[green]📱 Sharing version:[/green] [cyan]{share_report_path}[/cyan]"
                )
                console.print(f"[dim]   (Works in email/iMessage - uses static images)[/dim]")
            except Exception as e:
                console.print(f"\n[yellow]⚠️  Could not generate HTML report: {e}[/yellow]")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Operation cancelled by user[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        import traceback

        traceback.print_exc()
        sys.exit(1)
