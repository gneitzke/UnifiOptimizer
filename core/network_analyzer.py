#!/usr/bin/env python3
"""
Expert Network Analyzer - Comprehensive UniFi Network Analysis
Includes RSSI analysis, historical lookback, mesh AP optimization, and best practices
"""

from collections import defaultdict
from datetime import datetime, timezone

from rich.console import Console

console = Console()


class ExpertNetworkAnalyzer:
    """Expert-level network analysis with historical data and best practices"""

    def __init__(self, client, site="default"):
        self.client = client
        self.site = site
        self.devices = []
        self.clients = []
        self.events = []
        self.historical_events = []

        # Load thresholds from config (user-customizable via data/config.yaml)
        from utils.config import get_threshold

        self.RSSI_EXCELLENT = get_threshold("rssi.excellent", -50)
        self.RSSI_GOOD = get_threshold("rssi.good", -60)
        self.RSSI_FAIR = get_threshold("rssi.fair", -70)
        self.RSSI_POOR = get_threshold("rssi.poor", -80)

        self.MESH_RSSI_ACCEPTABLE = get_threshold("mesh.uplink_acceptable_dbm", -75)
        self.MESH_DISCONNECT_THRESHOLD = get_threshold("mesh.disconnect_threshold", 5)

    # Channel recommendations
    CHANNEL_24_PREFERRED = [1, 6, 11]  # Non-overlapping channels
    CHANNEL_5_DFS_START = 52  # DFS channels start here

    def collect_data(self, lookback_days=3):
        """
        Collect network data including historical lookback

        Args:
            lookback_days: Number of days to look back for events
        """
        self.lookback_days = lookback_days
        console.print(f"[cyan]Collecting network data (last {lookback_days} days)...[/cyan]")

        # Get devices
        devices_response = self.client.get(f"s/{self.site}/stat/device")
        self.devices = devices_response.get("data", []) if devices_response else []

        # Get current clients
        clients_response = self.client.get(f"s/{self.site}/stat/sta")
        self.clients = clients_response.get("data", []) if clients_response else []

        # Get recent events (default is usually last 1 hour)
        events_response = self.client.post(f"s/{self.site}/stat/event", {})
        self.events = events_response.get("data", []) if events_response else []

        # Get historical events (last N days)
        # UniFi stat/event requires POST with {"within": hours} in body
        try:
            within_hours = lookback_days * 24
            historical_response = self.client.post(
                f"s/{self.site}/stat/event",
                {"within": within_hours, "_limit": 5000},
            )
            self.historical_events = (
                historical_response.get("data", []) if historical_response else []
            )
        except Exception:
            # Fallback to regular events if historical query fails
            self.historical_events = self.events

        console.print(
            f"[green]✓ {len(self.devices)} devices, {len(self.clients)} clients, "
            f"{len(self.historical_events)} events collected[/green]\n"
        )

    def analyze_aps(self):
        """
        Analyze access points with expert-level recommendations

        Returns:
            dict: AP analysis with recommendations
        """
        aps = [d for d in self.devices if d.get("type") == "uap"]

        analysis = {
            "total_aps": len(aps),
            "wired_aps": [],
            "mesh_aps": [],
            "ap_details": [],
            "channel_usage": defaultdict(list),
            "power_settings": defaultdict(list),
            "issues": [],
        }

        for ap in aps:
            ap_mac = ap.get("mac")
            ap_name = ap.get("name", "Unnamed AP")
            uplink = ap.get("uplink", {})
            uplink_type = uplink.get("type", "wire")
            is_mesh = uplink_type == "wireless"

            # Get uplink details for mesh APs
            uplink_remote_mac = uplink.get("uplink_remote_mac") if is_mesh else None
            uplink_rssi = uplink.get("rssi") if is_mesh else None

            # Analyze radios
            radio_table = ap.get("radio_table", [])
            radios = {}

            for radio in radio_table:
                radio_name = radio.get("radio")
                if not radio_name:
                    continue

                band = "2.4GHz" if radio_name == "ng" else "5GHz" if radio_name == "na" else "6GHz"
                # Ensure channel is an integer for comparisons
                channel = radio.get("channel", 0)
                try:
                    channel = int(channel)
                except (ValueError, TypeError):
                    channel = 0

                ht = radio.get("ht", 20)
                tx_power = radio.get("tx_power")
                tx_power_mode = radio.get("tx_power_mode", "auto")

                radios[band] = {
                    "radio": radio_name,
                    "channel": channel,
                    "width": ht,
                    "tx_power": tx_power,
                    "tx_power_mode": tx_power_mode,
                }

                # Track channel usage
                analysis["channel_usage"][f"{band}_ch{channel}"].append(ap_name)

                # Track power settings
                analysis["power_settings"][f"{band}_{tx_power_mode}"].append(ap_name)

            # Get clients on this AP
            ap_clients = [c for c in self.clients if c.get("ap_mac") == ap_mac]

            ap_info = {
                "name": ap_name,
                "mac": ap_mac,
                "model": ap.get("model", "Unknown"),
                "is_mesh": is_mesh,
                "uplink_type": uplink_type,
                "uplink_rssi": uplink_rssi,
                "uplink_remote_mac": uplink_remote_mac,
                "radios": radios,
                "client_count": len(ap_clients),
                "clients": ap_clients,
                "state": ap.get("state", 0),
                "uptime": ap.get("uptime", 0),
            }

            analysis["ap_details"].append(ap_info)

            if is_mesh:
                analysis["mesh_aps"].append(ap_info)
            else:
                analysis["wired_aps"].append(ap_info)

        return analysis

    def analyze_mesh_aps(self, ap_analysis):
        """
        Special analysis for mesh APs (reliability focused)

        Args:
            ap_analysis: Results from analyze_aps()

        Returns:
            list: Mesh-specific recommendations
        """
        recommendations = []

        console.print(f"[bold cyan]Analyzing {len(ap_analysis['mesh_aps'])} Mesh APs[/bold cyan]")
        console.print("[dim]Mesh APs prioritize reliability over speed[/dim]\n")

        for mesh_ap in ap_analysis["mesh_aps"]:
            ap_name = mesh_ap["name"]
            uplink_rssi = mesh_ap["uplink_rssi"]

            # Critical: Check mesh uplink signal strength
            if uplink_rssi and uplink_rssi < -80:
                recommendations.append(
                    {
                        "ap": mesh_ap,
                        "priority": "critical",
                        "issue": "weak_mesh_uplink",
                        "message": f"Mesh uplink RSSI {uplink_rssi} dBm is too weak (< -80 dBm)",
                        "recommendation": "Reposition AP or add intermediate mesh hop for reliability",
                        "type": "mesh_reliability",
                    }
                )
            elif uplink_rssi and uplink_rssi < self.MESH_RSSI_ACCEPTABLE:
                recommendations.append(
                    {
                        "ap": mesh_ap,
                        "priority": "high",
                        "issue": "marginal_mesh_uplink",
                        "message": f"Mesh uplink RSSI {uplink_rssi} dBm is marginal",
                        "recommendation": "Monitor for disconnections; consider repositioning",
                        "type": "mesh_reliability",
                    }
                )

            # Check for high power on mesh APs (can cause issues)
            for band, radio in mesh_ap["radios"].items():
                if radio["tx_power_mode"] == "high":
                    recommendations.append(
                        {
                            "ap": mesh_ap,
                            "radio": radio,
                            "band": band,
                            "priority": "medium",
                            "issue": "mesh_high_power",
                            "message": f"{band} power set to HIGH on mesh AP",
                            "recommendation": "Use MEDIUM or AUTO for mesh APs to improve stability",
                            "type": "mesh_power",
                        }
                    )

            # Check for DFS channels on mesh APs (can cause disconnects)
            for band, radio in mesh_ap["radios"].items():
                channel = radio["channel"]
                if band == "5GHz" and self.CHANNEL_5_DFS_START <= channel <= 144:
                    recommendations.append(
                        {
                            "ap": mesh_ap,
                            "radio": radio,
                            "band": band,
                            "priority": "medium",
                            "issue": "mesh_dfs_channel",
                            "message": f"{band} using DFS channel {channel}",
                            "recommendation": "DFS channels can cause brief disconnects on radar detection. "
                            "Consider non-DFS channels (36-48, 149-165) for mesh reliability",
                            "type": "mesh_channel",
                        }
                    )

            # Check mesh AP disconnect history
            mesh_disconnects = [
                e
                for e in self.historical_events
                if e.get("ap") == mesh_ap["mac"]
                and (
                    "disconnect" in e.get("key", "").lower()
                    or "offline" in e.get("key", "").lower()
                )
            ]

            if len(mesh_disconnects) > self.MESH_DISCONNECT_THRESHOLD:
                recommendations.append(
                    {
                        "ap": mesh_ap,
                        "priority": "high",
                        "issue": "mesh_unstable",
                        "message": f"{len(mesh_disconnects)} disconnect events in lookback period",
                        "recommendation": "Investigate mesh uplink signal, interference, or power issues",
                        "type": "mesh_reliability",
                    }
                )

        return recommendations

    def analyze_channels(self, ap_analysis):
        """
        Analyze channel usage and recommend optimization (smart tracking to avoid repeated recommendations)

        Args:
            ap_analysis: Results from analyze_aps()

        Returns:
            list: Channel-related recommendations (filtered to avoid repeats)
        """
        # Use smart channel analyzer that tracks recommendations
        from core.channel_optimizer import ChannelRecommendationTracker, analyze_channels_smart

        tracker = ChannelRecommendationTracker()
        recommendations = analyze_channels_smart(ap_analysis, tracker)

        # Check 5GHz channel width (40MHz is often better than 80MHz in dense environments)
        # NOTE: Skip 6GHz — wide channels (80/160/320MHz) are standard and desirable on 6GHz
        from utils.config import get_threshold

        cw_ap_threshold = get_threshold("channel_width.prefer_40mhz_when_aps_gt", 4)
        cw_client_threshold = get_threshold("channel_width.narrow_when_clients_lt", 5)

        for ap_info in ap_analysis["ap_details"]:
            if "5GHz" in ap_info["radios"]:
                radio = ap_info["radios"]["5GHz"]
                width = radio["width"]
                client_count = ap_info["client_count"]

                if width == 80 and len(ap_analysis["ap_details"]) > cw_ap_threshold and client_count < cw_client_threshold:
                    recommendations.append(
                        {
                            "ap": ap_info,
                            "radio": radio,
                            "band": "5GHz",
                            "priority": "low",
                            "issue": "wide_channel",
                            "message": f"Using 80MHz width with only {client_count} clients",
                            "recommendation": "Consider 40MHz for less interference in multi-AP environments",
                            "type": "channel_width",
                        }
                    )

        return recommendations

    def analyze_power_settings(self, ap_analysis):
        """
        Analyze transmit power settings

        Args:
            ap_analysis: Results from analyze_aps()

        Returns:
            list: Power-related recommendations
        """
        recommendations = []

        # Count high power APs
        high_power_aps = []
        for ap_info in ap_analysis["ap_details"]:
            for band, radio in ap_info["radios"].items():
                if radio["tx_power_mode"] == "high":
                    high_power_aps.append((ap_info, band))

        # If multiple APs on high power, recommend reduction
        if len(high_power_aps) > 1:
            for ap_info, band in high_power_aps:
                recommendations.append(
                    {
                        "ap": ap_info,
                        "radio": ap_info["radios"][band],
                        "band": band,
                        "priority": "medium",
                        "issue": "high_power",
                        "message": f"{band} power set to HIGH",
                        "recommendation": "HIGH power can cause roaming issues and co-channel interference. "
                        "Use MEDIUM or AUTO for better coverage overlap and roaming.",
                        "type": "power_optimization",
                    }
                )

        return recommendations

    def analyze_client_health_historical(self):
        """
        Analyze client health with historical data

        Returns:
            dict: Client health analysis with historical trends
        """
        # Track disconnections per client over lookback period
        client_disconnects = defaultdict(list)
        client_roams = defaultdict(list)

        for event in self.historical_events:
            event_key = event.get("key", "")
            client_mac = event.get("user") or event.get("client")
            timestamp = event.get("time", 0)

            if "disconnect" in event_key.lower():
                client_disconnects[client_mac].append(timestamp)
            elif "roam" in event_key.lower():
                client_roams[client_mac].append(timestamp)

        # Analyze current clients with historical context
        client_analysis = []

        for client in self.clients:
            mac = client.get("mac")
            hostname = client.get("hostname", "Unknown")
            rssi = client.get("rssi", -100)

            # FIX: Some UniFi controllers return positive RSSI values
            # RSSI should always be negative in dBm for WiFi
            if rssi > 0:
                rssi = -rssi

            ap_mac = client.get("ap_mac")
            channel = client.get("channel", 0)

            # Find AP name
            ap_name = "Unknown"
            for ap in self.devices:
                if ap.get("mac") == ap_mac:
                    ap_name = ap.get("name", "Unknown")
                    break

            # Get historical stats
            disconnect_count = len(client_disconnects.get(mac, []))
            roam_count = len(client_roams.get(mac, []))

            # Calculate health score using continuous weighted composite
            # (matches the algorithm in client_health.py)
            import math

            worst_rssi = self.RSSI_POOR - 15  # e.g., -95 for default -80
            best_rssi = self.RSSI_EXCELLENT    # e.g., -50
            rssi_span = best_rssi - worst_rssi
            signal_quality = max(0, min(100, int((rssi - worst_rssi) / rssi_span * 100))) if rssi_span else 50

            if disconnect_count == 0:
                stability = 100
            else:
                rate = disconnect_count / max(self.lookback_days, 1)
                stability = max(0, min(100, 100 * math.exp(-0.35 * rate)))

            daily_roams = roam_count / max(self.lookback_days, 1)
            if daily_roams == 0:
                roaming = 95 if rssi > -65 else (70 if rssi > -75 else 30)
            elif daily_roams <= 10:
                roaming = 90 if rssi > -75 else 60
            else:
                roaming = max(10, int(90 * math.exp(-0.05 * daily_roams)))

            tx_rate = client.get("tx_rate", 0) or 0
            proto_max = {"ax": 1200, "ac": 867, "n": 300, "a": 54, "b": 11, "g": 54}
            wifi_proto = client.get("radio_proto", "n")
            max_rate = proto_max.get(wifi_proto, 300)
            throughput_eff = min(100, (tx_rate / max_rate) * 100) if max_rate > 0 else 50

            health_score = int(
                signal_quality * 0.40
                + stability * 0.25
                + roaming * 0.20
                + throughput_eff * 0.15
            )

            client_analysis.append(
                {
                    "mac": mac,
                    "hostname": hostname,
                    "ip": client.get("ip", "Unknown"),
                    "rssi": rssi,
                    "ap_name": ap_name,
                    "ap_mac": ap_mac,
                    "channel": channel,
                    "signal_quality": int(signal_quality),
                    "disconnect_count": disconnect_count,
                    "roam_count": roam_count,
                    "health_score": health_score,
                    "grade": self._get_grade(health_score),
                }
            )

        # Sort by health score (worst first)
        client_analysis.sort(key=lambda x: x["health_score"])

        # Calculate signal distribution for histogram
        signal_distribution = {
            "excellent": 0,
            "good": 0,
            "fair": 0,
            "poor": 0,
            "critical": 0,
            "wired": 0,
        }

        for client in self.clients:
            if client.get("is_wired", False):
                signal_distribution["wired"] += 1
            else:
                rssi = client.get("rssi", -100)

                # FIX: Some UniFi controllers return positive RSSI values
                # RSSI should always be negative in dBm for WiFi
                if rssi > 0:
                    rssi = -rssi

                if rssi > self.RSSI_EXCELLENT:
                    signal_distribution["excellent"] += 1
                elif rssi > self.RSSI_GOOD:
                    signal_distribution["good"] += 1
                elif rssi > self.RSSI_FAIR:
                    signal_distribution["fair"] += 1
                elif rssi > self.RSSI_POOR:
                    signal_distribution["poor"] += 1
                else:
                    signal_distribution["critical"] += 1

        return {
            "clients": client_analysis,
            "total_clients": len(client_analysis),
            "weak_signal": [c for c in client_analysis if c["rssi"] < self.RSSI_FAIR],
            "frequent_disconnects": [c for c in client_analysis if c["disconnect_count"] >= 3],
            "poor_health": [c for c in client_analysis if c["health_score"] < 60],
            "signal_distribution": signal_distribution,
        }

    def generate_expert_recommendations(self, ap_analysis, client_analysis):
        """
        Generate comprehensive expert recommendations

        Args:
            ap_analysis: AP analysis results
            client_analysis: Client health analysis results

        Returns:
            list: Prioritized recommendations
        """
        recommendations = []

        # Analyze mesh APs first (reliability critical)
        if ap_analysis["mesh_aps"]:
            mesh_recs = self.analyze_mesh_aps(ap_analysis)
            recommendations.extend(mesh_recs)

        # Analyze channels
        channel_recs = self.analyze_channels(ap_analysis)
        recommendations.extend(channel_recs)

        # Analyze power settings
        power_recs = self.analyze_power_settings(ap_analysis)
        recommendations.extend(power_recs)

        # Client-based recommendations
        weak_clients_by_ap = defaultdict(list)
        for client in client_analysis["weak_signal"]:
            weak_clients_by_ap[client["ap_mac"]].append(client)

        for ap_mac, clients in weak_clients_by_ap.items():
            if len(clients) >= 2:  # Multiple weak clients on same AP
                # Find AP info
                ap_info = next(
                    (ap for ap in ap_analysis["ap_details"] if ap["mac"] == ap_mac), None
                )
                if ap_info:
                    avg_rssi = sum(c["rssi"] for c in clients) / len(clients)
                    recommendations.append(
                        {
                            "ap": ap_info,
                            "priority": "high",
                            "issue": "weak_coverage",
                            "message": f"{len(clients)} clients with weak signal (avg {avg_rssi:.1f} dBm)",
                            "recommendation": "Check AP placement, power settings, or add additional AP for coverage",
                            "type": "coverage",
                            "affected_clients": len(clients),
                        }
                    )

        # Disconnection-based recommendations
        disconnect_clients_by_ap = defaultdict(list)
        for client in client_analysis["frequent_disconnects"]:
            disconnect_clients_by_ap[client["ap_mac"]].append(client)

        for ap_mac, clients in disconnect_clients_by_ap.items():
            if len(clients) >= 2:  # Multiple problematic clients on same AP
                ap_info = next(
                    (ap for ap in ap_analysis["ap_details"] if ap["mac"] == ap_mac), None
                )
                if ap_info:
                    total_disconnects = sum(c["disconnect_count"] for c in clients)
                    recommendations.append(
                        {
                            "ap": ap_info,
                            "priority": "high",
                            "issue": "frequent_disconnects",
                            "message": f"{len(clients)} clients with {total_disconnects} total disconnects",
                            "recommendation": "Investigate interference, try different channel, check for DFS radar events",
                            "type": "stability",
                            "affected_clients": len(clients),
                        }
                    )

        # Sort by priority
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        recommendations.sort(
            key=lambda x: (priority_order.get(x["priority"], 3), -x.get("affected_clients", 0))
        )

        return recommendations

    def _get_grade(self, score):
        """Convert score to letter grade"""
        if score >= 90:
            return "A"
        elif score >= 80:
            return "B"
        elif score >= 70:
            return "C"
        elif score >= 60:
            return "D"
        else:
            return "F"


def _build_event_timeline(events, lookback_days):
    """Time-bucket historical events by hour for trend visualization.

    Categorizes events into disconnect, roaming, DFS radar, and device restart
    buckets to enable sparkline/bar charts in the report.
    """
    if not events:
        return {"hours": [], "categories": {}, "summary": {}}

    # Classify events
    category_map = {
        "EVT_WU_Disconnected": "disconnect",
        "EVT_WU_Roam": "roaming",
        "EVT_WU_RoamRadio": "roaming",
        "EVT_AP_RadarDetected": "dfs_radar",
        "EVT_AP_ChannelChanged": "dfs_radar",
        "EVT_SW_RestartedUnknown": "device_restart",
        "EVT_AP_RestartedUnknown": "device_restart",
        "EVT_GW_RestartedUnknown": "device_restart",
        "EVT_SW_Connected": "device_restart",
        "EVT_AP_Connected": "device_restart",
    }

    # Build hourly buckets
    hourly = defaultdict(lambda: defaultdict(int))
    category_totals = defaultdict(int)
    ap_event_counts = defaultdict(lambda: defaultdict(int))

    for event in events:
        ts = event.get("time", 0)
        if not ts:
            continue
        # UniFi timestamps are Unix epoch in milliseconds
        ts_sec = ts / 1000 if ts > 1e12 else ts
        try:
            dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
        except (ValueError, OSError):
            continue

        key = event.get("key", "")
        category = category_map.get(key, "other")
        hour_key = dt.strftime("%Y-%m-%d %H:00")
        hourly[hour_key][category] += 1
        category_totals[category] += 1

        # Track per-AP events for detective insights
        ap_name = event.get("ap_name", event.get("ap", ""))
        if ap_name and category in ("disconnect", "roaming", "dfs_radar"):
            ap_event_counts[ap_name][category] += 1

    # Sort hours chronologically
    sorted_hours = sorted(hourly.keys())

    # Build per-category arrays aligned to sorted hours
    categories = ["disconnect", "roaming", "dfs_radar", "device_restart"]
    series = {}
    for cat in categories:
        series[cat] = [hourly[h].get(cat, 0) for h in sorted_hours]

    # Detective insights: find peak hours and problem APs
    insights = []
    if sorted_hours:
        # Find peak disconnect hour
        disconnect_by_hour = {h: hourly[h].get("disconnect", 0) for h in sorted_hours}
        peak_hour = max(disconnect_by_hour, key=disconnect_by_hour.get, default=None)
        if peak_hour and disconnect_by_hour[peak_hour] > 3:
            insights.append(
                f"Peak disconnects: {disconnect_by_hour[peak_hour]} events at {peak_hour}"
            )

        # Find AP with most disconnects
        if ap_event_counts:
            worst_ap = max(
                ap_event_counts.keys(),
                key=lambda a: ap_event_counts[a].get("disconnect", 0),
                default=None,
            )
            if worst_ap and ap_event_counts[worst_ap]["disconnect"] > 5:
                insights.append(
                    f"Most disconnects: {worst_ap} ({ap_event_counts[worst_ap]['disconnect']} events)"
                )

        # DFS correlation
        dfs_hours = [h for h in sorted_hours if hourly[h].get("dfs_radar", 0) > 0]
        if dfs_hours:
            for dfs_h in dfs_hours:
                disc_count = hourly[dfs_h].get("disconnect", 0)
                if disc_count > 2:
                    insights.append(
                        f"DFS radar at {dfs_h} coincided with {disc_count} disconnects"
                    )

    return {
        "hours": sorted_hours,
        "categories": series,
        "totals": dict(category_totals),
        "insights": insights,
        "ap_events": {k: dict(v) for k, v in ap_event_counts.items()},
        "lookback_days": lookback_days,
    }


def _build_client_journeys(events, clients, devices, lookback_days):
    """Build per-client journey profiles from historical events and current state.

    For each active client, reconstruct:
    - AP transition path (roaming history)
    - Disconnect pattern (time-of-day clustering)
    - Session quality indicators
    - Connection RSSI at roam events
    """
    if not events and not clients:
        return {"clients": {}, "top_issues": []}

    # Build device name lookup
    ap_names = {}
    for d in devices:
        if d.get("type") == "uap":
            ap_names[d.get("mac", "")] = d.get("name", "Unknown AP")

    # Parse events into per-client timelines
    client_events = defaultdict(list)
    for event in events:
        mac = event.get("user") or event.get("client")
        if not mac:
            continue
        ts = event.get("time", 0)
        ts_sec = ts / 1000 if ts > 1e12 else ts
        key = event.get("key", "")

        entry = {"ts": ts_sec, "key": key}

        # Extract roaming details
        if "roam" in key.lower():
            entry["type"] = "roam"
            entry["ap_from"] = event.get("ap_from", event.get("ap", ""))
            entry["ap_to"] = event.get("ap_to", event.get("ap", ""))
            entry["ap_name"] = event.get("ap_name", "")
            entry["ssid"] = event.get("ssid", "")
            entry["channel_from"] = event.get("channel_from")
            entry["channel_to"] = event.get("channel_to")
        elif "disconnect" in key.lower():
            entry["type"] = "disconnect"
            entry["ap"] = event.get("ap", "")
            entry["ap_name"] = event.get("ap_name", "")
            entry["reason"] = event.get("reason", "")
        elif "connected" in key.lower():
            entry["type"] = "connect"
            entry["ap"] = event.get("ap", "")
            entry["ap_name"] = event.get("ap_name", "")
        else:
            entry["type"] = "other"

        client_events[mac].append(entry)

    # Sort each client's events chronologically
    for mac in client_events:
        client_events[mac].sort(key=lambda e: e["ts"])

    # Build journey profiles for current clients
    journeys = {}
    for client in clients:
        mac = client.get("mac", "")
        hostname = client.get("hostname") or client.get("name", "Unknown")
        is_wired = client.get("is_wired", False)
        if is_wired:
            continue

        events_list = client_events.get(mac, [])
        rssi = client.get("rssi", -100)
        if rssi > 0:
            rssi = -rssi
        current_ap = ap_names.get(client.get("ap_mac", ""), "Unknown")

        # Reconstruct AP transitions
        ap_path = []
        disconnects = []
        connects = []
        for evt in events_list:
            if evt["type"] == "roam":
                from_name = evt.get("ap_name") or ap_names.get(evt.get("ap_from", ""), "?")
                to_name = ap_names.get(evt.get("ap_to", ""), evt.get("ap_name", "?"))
                ap_path.append({
                    "ts": evt["ts"],
                    "from_ap": from_name,
                    "to_ap": to_name,
                    "channel_from": evt.get("channel_from"),
                    "channel_to": evt.get("channel_to"),
                })
            elif evt["type"] == "disconnect":
                disconnects.append({
                    "ts": evt["ts"],
                    "ap": evt.get("ap_name") or ap_names.get(evt.get("ap", ""), "?"),
                    "reason": evt.get("reason", ""),
                })
            elif evt["type"] == "connect":
                connects.append({
                    "ts": evt["ts"],
                    "ap": evt.get("ap_name") or ap_names.get(evt.get("ap", ""), "?"),
                })

        # Detect patterns
        disconnect_count = len(disconnects)
        roam_count = len(ap_path)

        # Time-of-day clustering for disconnects
        hour_buckets = defaultdict(int)
        for d in disconnects:
            try:
                dt = datetime.fromtimestamp(d["ts"], tz=timezone.utc)
                hour_buckets[dt.hour] += 1
            except (ValueError, OSError):
                pass

        # Find peak disconnect hour
        peak_hour = max(hour_buckets, key=hour_buckets.get) if hour_buckets else None
        peak_count = hour_buckets[peak_hour] if peak_hour is not None else 0

        # Unique APs visited
        visited_aps = set()
        for r in ap_path:
            visited_aps.add(r["from_ap"])
            visited_aps.add(r["to_ap"])
        visited_aps.discard("?")
        visited_aps.discard("")

        # Build session estimate (connect → disconnect pairs)
        sessions = []
        conn_stack = list(connects)
        for disc in disconnects:
            # Find closest connect before this disconnect
            best_conn = None
            for c in conn_stack:
                if c["ts"] < disc["ts"]:
                    best_conn = c
            if best_conn:
                duration_min = (disc["ts"] - best_conn["ts"]) / 60
                sessions.append({"duration_min": duration_min, "ap": best_conn["ap"]})
                conn_stack.remove(best_conn)

        avg_session_min = (
            sum(s["duration_min"] for s in sessions) / len(sessions) if sessions else None
        )

        # Classify client behavior
        behavior = "stable"
        behavior_detail = ""
        daily_roams = roam_count / max(lookback_days, 1)
        daily_disc = disconnect_count / max(lookback_days, 1)

        if daily_disc > 5:
            behavior = "unstable"
            behavior_detail = f"{daily_disc:.1f} disconnects/day"
        elif daily_roams > 20:
            behavior = "flapping"
            behavior_detail = f"{daily_roams:.1f} roams/day between APs"
        elif daily_roams > 5 and rssi > -70:
            behavior = "healthy_roamer"
            behavior_detail = f"Active roamer with good signal ({rssi} dBm)"
        elif daily_roams == 0 and rssi < -75:
            behavior = "sticky"
            behavior_detail = f"Poor signal ({rssi} dBm) but not roaming — may be stuck"
        elif disconnect_count > 0 and peak_count >= disconnect_count * 0.5 and peak_hour is not None:
            behavior = "pattern"
            behavior_detail = f"Disconnects cluster at {peak_hour}:00 UTC"

        journeys[mac] = {
            "hostname": hostname,
            "mac": mac,
            "current_rssi": rssi,
            "current_ap": current_ap,
            "behavior": behavior,
            "behavior_detail": behavior_detail,
            "disconnect_count": disconnect_count,
            "roam_count": roam_count,
            "visited_aps": sorted(visited_aps),
            "ap_path": ap_path[-20:],  # Last 20 transitions
            "disconnects": disconnects[-20:],  # Last 20 disconnects
            "peak_disconnect_hour": peak_hour,
            "avg_session_min": round(avg_session_min, 1) if avg_session_min else None,
            "daily_disconnect_rate": round(daily_disc, 2),
            "daily_roam_rate": round(daily_roams, 2),
        }

    # Find top issues across all clients
    top_issues = []
    for mac, j in journeys.items():
        if j["behavior"] == "unstable":
            top_issues.append({
                "client": j["hostname"],
                "mac": mac,
                "issue": f"Unstable: {j['behavior_detail']}",
                "severity": "high",
            })
        elif j["behavior"] == "flapping":
            top_issues.append({
                "client": j["hostname"],
                "mac": mac,
                "issue": f"Flapping: {j['behavior_detail']}",
                "severity": "high",
            })
        elif j["behavior"] == "sticky":
            top_issues.append({
                "client": j["hostname"],
                "mac": mac,
                "issue": f"Sticky: {j['behavior_detail']}",
                "severity": "medium",
            })
    top_issues.sort(key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x["severity"], 3))

    return {
        "clients": journeys,
        "top_issues": top_issues[:20],
        "total_tracked": len(journeys),
    }


def run_expert_analysis(client, site="default", lookback_days=3):
    """
    Run complete expert network analysis

    Args:
        client: CloudKey Gen2+ API client
        site: Site name
        lookback_days: Days of historical data to analyze

    Returns:
        dict: Complete analysis results
    """
    analyzer = ExpertNetworkAnalyzer(client, site)

    # Collect data
    analyzer.collect_data(lookback_days)

    # Analyze APs
    ap_analysis = analyzer.analyze_aps()

    # Analyze clients with historical data
    client_analysis = analyzer.analyze_client_health_historical()

    # Generate expert recommendations
    recommendations = analyzer.generate_expert_recommendations(ap_analysis, client_analysis)

    # Build event timeline for historical analysis
    event_timeline = _build_event_timeline(analyzer.historical_events, lookback_days)

    # Build per-client journey profiles
    client_journeys = _build_client_journeys(
        analyzer.historical_events, analyzer.clients, analyzer.devices, lookback_days
    )

    return {
        "ap_analysis": ap_analysis,
        "client_analysis": client_analysis,
        "recommendations": recommendations,
        "lookback_days": lookback_days,
        "devices": analyzer.devices,
        "clients": analyzer.clients,
        "event_timeline": event_timeline,
        "client_journeys": client_journeys,
    }
