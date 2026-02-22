"""Analysis service — headless network analysis pipeline.

Runs the full expert analysis pipeline without any console output.
Returns structured data that can be consumed by the CLI display layer
or the web API.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.advanced_analyzer import AdvancedNetworkAnalyzer, run_advanced_analysis
from core.network_analyzer import run_expert_analysis
from core.network_health_analyzer import NetworkHealthAnalyzer


def run_analysis(client, site="default", lookback_days=3, min_rssi_strategy="optimal"):
    """Run the full expert network analysis pipeline.

    This is the headless equivalent of optimize_network.analyze_network().
    It performs all data collection and analysis without any console output.

    Args:
        client: Authenticated CloudKeyGen2Client instance.
        site: UniFi site name.
        lookback_days: Days of historical data to analyze.
        min_rssi_strategy: 'optimal' or 'max_connectivity'.

    Returns:
        dict with keys:
            - full_analysis: Complete analysis data dict (or None on fallback).
            - devices: Raw device list from the controller.
            - clients: Raw client list from the controller.
            - error_summary: API error summary dict (or None).

    Raises:
        Exception: On fatal analysis errors (caller should handle).
    """
    # Step 1: Expert base analysis (devices, clients, events, stats)
    analysis = run_expert_analysis(client, site, lookback_days)

    devices = analysis.get("devices", [])
    clients = analysis.get("clients", [])

    # Step 2: Advanced analysis (DFS, band steering, min RSSI, airtime, capabilities)
    advanced = run_advanced_analysis(client, site, devices, clients, lookback_days, min_rssi_strategy)

    # Merge advanced results into analysis dict
    for key in (
        "dfs_analysis",
        "band_steering_analysis",
        "mesh_necessity_analysis",
        "min_rssi_analysis",
        "fast_roaming_analysis",
        "airtime_analysis",
        "client_capabilities",
        "switch_analysis",
        "switch_port_history",
    ):
        analysis[key] = advanced.get(key, {})

    # Step 3: Manufacturer analysis
    from core.manufacturer_analyzer import analyze_manufacturers

    analysis["manufacturer_analysis"] = analyze_manufacturers(clients)

    # Step 4: Merge recommendations from advanced analysis into main list
    if "recommendations" not in analysis:
        analysis["recommendations"] = []

    _merge_recommendations(analysis, advanced, devices)

    # Step 5: Health analysis
    health_analyzer = NetworkHealthAnalyzer(client, site)
    analysis["health_analysis"] = health_analyzer.analyze_network_health(devices, clients)

    adv_health = AdvancedNetworkAnalyzer(client, site)
    analysis["health_score"] = adv_health.calculate_network_health_score(analysis)

    # Step 6: Client findings
    analysis["client_findings"] = generate_client_findings(clients, devices)

    # Step 7: API error summary
    error_summary = client.get_error_summary()
    analysis["api_errors"] = error_summary
    analysis["has_incomplete_data"] = error_summary is not None

    return {
        "full_analysis": analysis,
        "devices": devices,
        "clients": clients,
        "error_summary": error_summary,
    }


def _merge_recommendations(analysis, advanced, devices):
    """Merge band-steering and min-RSSI recs into the main recommendations list."""
    for source_key in ("band_steering_analysis", "min_rssi_analysis"):
        recs = advanced.get(source_key, {}).get("recommendations", [])
        for rec in recs:
            device_name = rec.get("device", "")
            device_obj = next((d for d in devices if d.get("name") == device_name), None)
            if device_obj:
                rec_copy = rec.copy()
                rec_copy["ap"] = {
                    "name": device_obj.get("name", "Unknown"),
                    "mac": device_obj.get("mac", ""),
                    "is_mesh": (
                        device_obj.get("adopted", False)
                        and device_obj.get("uplink", {}).get("type") == "wireless"
                    ),
                }
                analysis["recommendations"].append(rec_copy)


def generate_client_findings(clients, devices):
    """Generate per-client actionable findings.

    Identifies:
    - Clients on wrong band (2.4GHz but supports 5GHz+)
    - Dead-zone clients (persistently poor signal)

    Returns:
        list[dict]: Findings with type, severity, message, etc.
    """
    findings = []
    if not clients:
        return findings

    ap_names = {}
    for d in devices or []:
        if d.get("type") == "uap":
            ap_names[d.get("mac", "")] = d.get("name", "Unknown AP")

    for client_data in clients:
        if client_data.get("is_wired", False):
            continue

        mac = client_data.get("mac", "")
        hostname = client_data.get("hostname", client_data.get("name", mac))
        rssi = client_data.get("rssi", -100)
        if rssi > 0:
            rssi = -rssi
        radio = client_data.get("radio", "")
        radio_proto = client_data.get("radio_proto", "")
        ap_mac = client_data.get("ap_mac", "")
        ap_name = ap_names.get(ap_mac, "Unknown AP")

        # Wrong band: 2.4GHz but supports 5GHz
        if radio == "ng" and ("ac" in radio_proto.lower() or "ax" in radio_proto.lower()):
            findings.append(
                {
                    "client": hostname,
                    "mac": mac,
                    "ap": ap_name,
                    "type": "wrong_band",
                    "severity": "medium",
                    "message": (
                        f"{hostname} on 2.4GHz but supports {radio_proto}"
                        " — band steering could move to 5GHz"
                    ),
                    "rssi": rssi,
                }
            )

        # Dead-zone client
        if rssi < -80:
            findings.append(
                {
                    "client": hostname,
                    "mac": mac,
                    "ap": ap_name,
                    "type": "dead_zone",
                    "severity": "high" if rssi < -85 else "medium",
                    "message": (
                        f"{hostname} at {rssi} dBm on {ap_name}"
                        " — possible dead zone or needs AP placement change"
                    ),
                    "rssi": rssi,
                }
            )

    return findings
