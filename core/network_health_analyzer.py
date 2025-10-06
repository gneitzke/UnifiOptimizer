"""
Comprehensive Network Health Analyzer
Provides holistic analysis of wired and wireless infrastructure including:
- Device stability (uptime, restarts)
- Broadcast storm detection
- Channel overlap and interference
- TX power validation
- VLAN segmentation
- Firmware status
"""


class NetworkHealthAnalyzer:
    def __init__(self, client, site="default"):
        self.client = client
        self.site = site

    def analyze_network_health(self, devices, clients):
        """
        Comprehensive health analysis covering all network aspects
        """
        results = {
            "severity": "low",
            "overall_score": 100,  # Start at 100, deduct for issues
            "categories": {
                "device_stability": self._analyze_device_stability(devices),
                "broadcast_traffic": self._analyze_broadcast_traffic(devices),
                "channel_health": self._analyze_channel_health(devices),
                "radio_health": self._analyze_radio_health(devices),
                "vlan_segmentation": self._analyze_vlan_segmentation(clients),
                "firmware_status": self._analyze_firmware_status(devices),
            },
            "issues": [],
            "recommendations": [],
        }

        # Aggregate issues and calculate overall score
        for category, analysis in results["categories"].items():
            if analysis.get("issues"):
                results["issues"].extend(analysis["issues"])
            if analysis.get("recommendations"):
                results["recommendations"].extend(analysis["recommendations"])

            # Deduct from overall score based on severity
            score_penalty = analysis.get("score_penalty", 0)
            results["overall_score"] -= score_penalty

        # Determine overall severity
        if results["overall_score"] < 50:
            results["severity"] = "high"
        elif results["overall_score"] < 75:
            results["severity"] = "medium"
        else:
            results["severity"] = "low"

        # Ensure score doesn't go below 0
        results["overall_score"] = max(0, results["overall_score"])

        return results

    def _get_device_restart_events(self, device_mac, lookback_days=7):
        """
        Query event log to find restart events for a specific device

        Returns:
            dict with:
            - restart_count: number of restarts found
            - manual_restart: bool indicating if restart was user-initiated
            - events: list of restart event details
        """
        result = {
            "restart_count": 0,
            "manual_restart": False,
            "events": [],
        }

        try:
            within_hours = lookback_days * 24
            events_response = self.client.get(
                f"s/{self.site}/stat/event?within={within_hours}&_limit=1000"
            )

            if not events_response or "data" not in events_response:
                return result

            events = events_response.get("data", [])

            # Look for restart-related events for this device
            # NOTE: "disconnected" is NOT included - that's for client disconnections, not device restarts
            restart_keywords = [
                "restarted",
                "rebooted",
                "restart",
                "reboot",
            ]

            # Event keys that specifically indicate device restart (not client activity)
            # NOTE: EVT_AP_Disconnected = AP device lost connection to controller (restart-related)
            #       EVT_WU_Disconnected = Wireless User disconnected (client activity - NOT restart)
            device_restart_keys = [
                "EVT_AP_RestartedUnknown",
                "EVT_AP_Restarted",
                "EVT_AP_RestartedUser",
                "EVT_AP_Rebooted",
                "EVT_AP_Disconnected",  # Device (AP) disconnected from controller
                "EVT_AP_PoweredOff",
                "EVT_SW_RestartedUnknown",
                "EVT_SW_Restarted",
                "EVT_SW_Rebooted",
                "EVT_SW_Disconnected",  # Device (Switch) disconnected from controller
                "EVT_SW_PoweredOff",
                "EVT_GW_RestartedUnknown",
                "EVT_GW_Restarted",
                "EVT_GW_Rebooted",
                "EVT_GW_PoweredOff",
                "EVT_AP_Upgraded",
                "EVT_SW_Upgraded",
                "EVT_GW_Upgraded",
            ]

            manual_keywords = [
                "user",
                "manually",
                "admin",
                "upgrade",
                "requested",
                "firmware update",
                "configuration change",
            ]

            for event in events:
                # Match by device MAC or name
                event_device = event.get("device", "")
                event_ap = event.get("ap", "")
                event_msg = event.get("msg", "").lower()
                event_key = event.get("key", "")

                # Check if this event is for our device
                is_device_event = (
                    event_device == device_mac
                    or event_ap == device_mac
                    or device_mac.lower() in event_msg
                )

                if is_device_event:
                    # Check if it's a restart event
                    # Must match restart keywords OR be a known device restart event key
                    is_restart = (
                        any(keyword in event_msg for keyword in restart_keywords)
                        or event_key in device_restart_keys
                    )

                    if is_restart:
                        result["restart_count"] += 1

                        # Check if it was a manual/expected restart
                        # Look for upgrade events or manual restart keywords
                        is_manual = (
                            any(keyword in event_msg for keyword in manual_keywords)
                            or any(keyword in event_key.lower() for keyword in manual_keywords)
                            or "Upgraded" in event_key
                        )

                        if is_manual:
                            result["manual_restart"] = True

                        result["events"].append(
                            {
                                "timestamp": event.get("time"),
                                "message": event.get("msg"),
                                "key": event.get("key"),
                                "is_manual": is_manual,
                            }
                        )

        except Exception:
            # If event query fails, don't crash the analysis
            pass

        return result

    def _analyze_device_stability(self, devices):
        """
        Analyze device uptime and restart patterns
        Intelligently detects:
        - Manual/expected restarts (OK)
        - Single unexpected restarts (low priority)
        - Cyclic/repeated restarts (high priority - stability issue)
        """
        analysis = {
            "category": "Device Stability",
            "status": "healthy",
            "issues": [],
            "recommendations": [],
            "score_penalty": 0,
            "devices": {
                "stable": [],
                "recent_restart": [],
                "critical_restart": [],
                "cyclic_restart": [],
            },
        }

        for device in devices:
            device_name = device.get("name", "Unknown")
            device_type = device.get("type", "unknown")
            device_mac = device.get("mac", "")
            uptime_seconds = device.get("uptime", 0)
            uptime_days = uptime_seconds / 86400
            uptime_hours = uptime_seconds / 3600

            device_info = {
                "name": device_name,
                "type": device_type,
                "uptime_seconds": uptime_seconds,
                "uptime_days": uptime_days,
                "uptime_hours": uptime_hours,
            }

            # Check if device restarted recently
            if uptime_days < 7:
                # Query event log to determine restart cause and frequency
                restart_info = self._get_device_restart_events(device_mac, lookback_days=7)
                restart_count = restart_info["restart_count"]
                is_manual = restart_info["manual_restart"]

                device_info["restart_count"] = restart_count
                device_info["manual_restart"] = is_manual

                # CYCLIC RESTARTS: Multiple restarts = stability issue (HIGH PRIORITY)
                if restart_count >= 3:
                    analysis["devices"]["cyclic_restart"].append(device_info)
                    analysis["status"] = "critical"
                    analysis["score_penalty"] += 15

                    analysis["issues"].append(
                        {
                            "severity": "high",
                            "type": "cyclic_restart",
                            "device": device_name,
                            "device_type": device_type,
                            "restart_count": restart_count,
                            "uptime_hours": uptime_hours,
                            "message": f"{device_name} has restarted {restart_count} times in 7 days - CYCLIC BEHAVIOR",
                            "impact": "Repeated restarts indicate serious stability problem (power, hardware, firmware)",
                            "recommendation": "URGENT: Check power supply, thermal issues, firmware bugs, or hardware failure",
                        }
                    )

                # MANUAL RESTART: User-initiated or upgrade (OK - no issue)
                elif is_manual and restart_count == 1:
                    analysis["devices"]["stable"].append(device_info)
                    # No issue - manual restarts are expected and OK

                # SINGLE RESTART (last 24h): Could be crash, but not cyclic (LOW-MEDIUM PRIORITY)
                elif uptime_days < 1:
                    analysis["devices"]["critical_restart"].append(device_info)

                    # Only flag if not manual - single unplanned restart warrants monitoring
                    if not is_manual:
                        if analysis["status"] == "healthy":
                            analysis["status"] = "warning"
                        analysis["score_penalty"] += 3

                        analysis["issues"].append(
                            {
                                "severity": "medium",
                                "type": "recent_restart",
                                "device": device_name,
                                "device_type": device_type,
                                "uptime_hours": uptime_hours,
                                "message": f"{device_name} restarted {uptime_hours:.1f} hours ago (single occurrence)",
                                "impact": "Single restart may be temporary issue - not cyclic",
                                "recommendation": "Monitor device - if restarts repeat, investigate power/hardware",
                            }
                        )

                # SINGLE RESTART (1-7 days): Recent but not critical (LOW PRIORITY)
                elif uptime_days < 7:
                    analysis["devices"]["recent_restart"].append(device_info)

                    # Only flag if not manual and appears to be isolated incident
                    if not is_manual:
                        analysis["score_penalty"] += 1

                        analysis["issues"].append(
                            {
                                "severity": "low",
                                "type": "recent_restart",
                                "device": device_name,
                                "device_type": device_type,
                                "uptime_days": uptime_days,
                                "message": f"{device_name} restarted {uptime_days:.1f} days ago (isolated incident)",
                                "impact": "Single restart over week ago - likely not a concern",
                                "recommendation": "No action needed unless restarts become frequent",
                            }
                        )

            # Stable device
            else:
                analysis["devices"]["stable"].append(device_info)

        # Summary recommendations
        if analysis["devices"]["cyclic_restart"]:
            analysis["recommendations"].append(
                {
                    "priority": "high",
                    "category": "stability",
                    "message": f"{len(analysis['devices']['cyclic_restart'])} device(s) with CYCLIC restart pattern - hardware/stability issue",
                    "action": "URGENT: Check power supplies, temperature, firmware bugs, or failing hardware",
                }
            )

        if analysis["devices"]["critical_restart"] and not analysis["devices"]["cyclic_restart"]:
            # Only show this if no cyclic restarts (otherwise it's redundant)
            analysis["recommendations"].append(
                {
                    "priority": "medium",
                    "category": "stability",
                    "message": f"{len(analysis['devices']['critical_restart'])} device(s) restarted recently - monitor for patterns",
                    "action": "Monitor devices - single restarts are OK, repeated restarts indicate problems",
                }
            )

        return analysis

    def _analyze_broadcast_traffic(self, devices):
        """
        Detect broadcast storms and excessive multicast traffic

        Smart detection: If all ports have similar broadcast counts, it's normal switch behavior
        (broadcasts are forwarded to all ports). Only flag outliers or anomalies.
        """
        analysis = {
            "category": "Broadcast Traffic",
            "status": "healthy",
            "issues": [],
            "recommendations": [],
            "score_penalty": 0,
            "ports_with_issues": [],
        }

        switches = [d for d in devices if d.get("type") == "usw"]

        for switch in switches:
            switch_name = switch.get("name", "Unknown Switch")
            port_table = switch.get("port_table", [])

            # Collect broadcast counts from all active ports
            port_broadcasts = []
            port_data = {}

            for port in port_table:
                if not port.get("up"):
                    continue

                port_idx = port.get("port_idx")
                rx_broadcast = port.get("rx_broadcast", 0)
                tx_broadcast = port.get("tx_broadcast", 0)
                rx_multicast = port.get("rx_multicast", 0)
                tx_multicast = port.get("tx_multicast", 0)

                total_broadcast = rx_broadcast + tx_broadcast
                total_multicast = rx_multicast + tx_multicast

                port_broadcasts.append(total_broadcast)
                port_data[port_idx] = {
                    "broadcast": total_broadcast,
                    "multicast": total_multicast,
                    "rx_broadcast": rx_broadcast,
                    "tx_broadcast": tx_broadcast,
                }

            if not port_broadcasts:
                continue

            # Calculate statistics
            avg_broadcast = sum(port_broadcasts) / len(port_broadcasts)
            max_broadcast = max(port_broadcasts)
            min_broadcast = min(port_broadcasts)

            # Calculate standard deviation
            variance = sum((x - avg_broadcast) ** 2 for x in port_broadcasts) / len(port_broadcasts)
            std_dev = variance**0.5

            # If all ports have similar counts (low variance), it's normal switch behavior
            # Only flag if there are significant outliers
            threshold_multiplier = 2.0  # Flag if >2x standard deviation from mean

            for port_idx, data in port_data.items():
                total_broadcast = data["broadcast"]
                total_multicast = data["multicast"]
                rx_broadcast = data["rx_broadcast"]
                tx_broadcast = data["tx_broadcast"]

                # Calculate deviation from average
                deviation = abs(total_broadcast - avg_broadcast)
                is_outlier = deviation > (std_dev * threshold_multiplier) if std_dev > 0 else False

                # Only flag as storm if it's an outlier AND extremely high
                if is_outlier and total_broadcast > 100000000:  # >100M broadcasts AND outlier
                    analysis["status"] = "critical"
                    analysis["score_penalty"] += 5

                    analysis["issues"].append(
                        {
                            "severity": "high",
                            "type": "broadcast_storm",
                            "switch": switch_name,
                            "port": port_idx,
                            "broadcast_count": total_broadcast,
                            "message": f"Port {port_idx}: Broadcast storm detected ({total_broadcast:,} packets, {(total_broadcast/avg_broadcast):.1f}x average)",
                            "impact": "Excessive broadcast traffic consuming bandwidth and CPU on all devices",
                            "recommendation": "Identify device on this port; check for network loops, chatty IoT devices, or misconfigurations",
                        }
                    )

                    analysis["ports_with_issues"].append(
                        {
                            "switch": switch_name,
                            "port": port_idx,
                            "broadcast": total_broadcast,
                            "multicast": total_multicast,
                            "severity": "high",
                        }
                    )

                # Flag significantly higher TX broadcasts (device generating broadcasts)
                elif is_outlier and tx_broadcast > (rx_broadcast * 2) and tx_broadcast > 50000000:
                    if analysis["status"] == "healthy":
                        analysis["status"] = "warning"
                    analysis["score_penalty"] += 3

                    analysis["issues"].append(
                        {
                            "severity": "medium",
                            "type": "broadcast_source",
                            "switch": switch_name,
                            "port": port_idx,
                            "broadcast_count": tx_broadcast,
                            "message": f"Port {port_idx}: Device generating excessive broadcasts ({tx_broadcast:,} TX packets)",
                            "impact": "Device is source of broadcast traffic affecting network performance",
                            "recommendation": "Identify device and check for misconfiguration or chatty protocol (mDNS, NetBIOS, etc.)",
                        }
                    )

                    analysis["ports_with_issues"].append(
                        {
                            "switch": switch_name,
                            "port": port_idx,
                            "broadcast": total_broadcast,
                            "multicast": total_multicast,
                            "severity": "medium",
                        }
                    )

                # Info: High multicast (common for media devices)
                if total_multicast > 100000000 and is_outlier:
                    analysis["issues"].append(
                        {
                            "severity": "low",
                            "type": "high_multicast",
                            "switch": switch_name,
                            "port": port_idx,
                            "multicast_count": total_multicast,
                            "message": f"Port {port_idx}: High multicast traffic ({total_multicast:,} packets)",
                            "impact": "Normal for media devices (Sonos, Chromecast, Apple TV) but unusually high",
                            "recommendation": "Monitor if causing performance issues; consider IGMP snooping optimization",
                        }
                    )

            # Provide informational context if broadcast levels are uniformly high
            if avg_broadcast > 10000000 and std_dev / avg_broadcast < 0.1:  # High but uniform
                analysis["recommendations"].append(
                    {
                        "priority": "low",
                        "category": "broadcast",
                        "message": f"Network has elevated broadcast traffic (~{avg_broadcast/1000000:.1f}M per port) but uniformly distributed",
                        "action": "This is normal for networks with IoT devices. Consider VLAN segmentation to reduce broadcast domain size if performance issues occur.",
                    }
                )

        return analysis

    def _analyze_channel_health(self, devices):
        """
        Analyze WiFi channel assignments, overlap, and optimization
        Focuses on 2.4GHz co-channel interference
        """
        analysis = {
            "category": "Channel Health",
            "status": "healthy",
            "issues": [],
            "recommendations": [],
            "score_penalty": 0,
            "channel_assignments": {"ng": {}, "na": {}},  # 2.4GHz  # 5GHz
        }

        aps = [d for d in devices if d.get("type") == "uap"]

        # Collect channel assignments
        for ap in aps:
            ap_name = ap.get("name", "Unknown")
            radio_table = ap.get("radio_table", [])

            for radio in radio_table:
                radio_name = radio.get("radio", "unknown")
                channel = radio.get("channel")

                if not channel:
                    continue

                if radio_name not in analysis["channel_assignments"]:
                    analysis["channel_assignments"][radio_name] = {}

                if channel not in analysis["channel_assignments"][radio_name]:
                    analysis["channel_assignments"][radio_name][channel] = []

                analysis["channel_assignments"][radio_name][channel].append(
                    {
                        "ap": ap_name,
                        "channel": channel,
                        "tx_power": radio.get("tx_power", 0),
                        "utilization": radio.get("channel_utilization", 0),
                    }
                )

        # Analyze 2.4GHz channel overlap (only channels 1, 6, 11 are non-overlapping)
        ng_channels = analysis["channel_assignments"].get("ng", {})

        for channel, ap_list in ng_channels.items():
            if len(ap_list) > 1:
                analysis["status"] = "warning"
                analysis["score_penalty"] += 5

                ap_names = [ap["ap"] for ap in ap_list]

                analysis["issues"].append(
                    {
                        "severity": "medium",
                        "type": "channel_overlap",
                        "band": "2.4GHz",
                        "channel": channel,
                        "ap_count": len(ap_list),
                        "aps": ap_names,
                        "message": f"2.4GHz Channel {channel}: {len(ap_list)} APs causing co-channel interference",
                        "impact": f"Reduced WiFi performance for {len(ap_list)} APs and their clients due to airtime contention",
                        "recommendation": f"Redistribute APs across channels 1, 6, 11 to minimize interference",
                    }
                )

        # Check for unused 2.4GHz channels
        used_24ghz = set(ng_channels.keys())
        optimal_24ghz = {1, 6, 11}
        available_24ghz = optimal_24ghz - used_24ghz

        if available_24ghz and len(ng_channels) > 0:
            analysis["recommendations"].append(
                {
                    "priority": "medium",
                    "category": "channel_optimization",
                    "message": f"2.4GHz channels {available_24ghz} are unused",
                    "action": f"Redistribute APs to utilize unused channels and reduce co-channel interference. Optimal distribution: use only channels 1, 6, 11.",
                }
            )

        # Analyze 5GHz channel utilization
        na_channels = analysis["channel_assignments"].get("na", {})

        for channel, ap_list in na_channels.items():
            for ap_info in ap_list:
                utilization = ap_info["utilization"]
                if utilization > 70:
                    analysis["status"] = "warning"
                    analysis["score_penalty"] += 3

                    analysis["issues"].append(
                        {
                            "severity": "medium",
                            "type": "channel_congestion",
                            "band": "5GHz",
                            "channel": channel,
                            "ap": ap_info["ap"],
                            "utilization": utilization,
                            "message": f"{ap_info['ap']} (5GHz Ch {channel}): High channel utilization ({utilization}%)",
                            "impact": "Congested channel reduces WiFi performance for all clients on this AP",
                            "recommendation": "Consider changing channel, adjusting TX power, or adding additional AP coverage",
                        }
                    )

        return analysis

    def _analyze_radio_health(self, devices):
        """
        Analyze AP radio health including TX power, retries, and errors
        """
        analysis = {
            "category": "Radio Health",
            "status": "healthy",
            "issues": [],
            "recommendations": [],
            "score_penalty": 0,
            "radios": [],
        }

        aps = [d for d in devices if d.get("type") == "uap"]

        for ap in aps:
            ap_name = ap.get("name", "Unknown")
            uplink_type = ap.get("uplink", {}).get("type", "unknown")
            is_mesh = uplink_type == "wireless"
            radio_table = ap.get("radio_table", [])

            for radio in radio_table:
                radio_name = radio.get("radio", "unknown")

                # Determine band - handle 6GHz radios
                if radio_name == "ng":
                    band = "2.4GHz"
                elif radio_name == "na":
                    band = "5GHz"
                elif radio_name in ["6e", "ax", "6g"]:  # 6GHz radio names
                    band = "6GHz"
                else:
                    band = "Unknown"

                tx_power = radio.get("tx_power")
                tx_power_mode = radio.get("tx_power_mode", "unknown")
                channel = radio.get("channel")

                # Check if radio is actually enabled/in-use
                # A radio is considered active if it has a valid channel assigned
                # Convert channel to int safely (may be string or None)
                try:
                    channel_num = int(channel) if channel is not None else 0
                except (ValueError, TypeError):
                    channel_num = 0

                is_radio_enabled = channel_num > 0

                # Handle None/null TX power from API (common on mesh APs or disabled radios)
                if tx_power is None:
                    tx_power = 0

                radio_info = {
                    "ap": ap_name,
                    "band": band,
                    "radio": radio_name,
                    "tx_power": tx_power,
                    "tx_power_mode": tx_power_mode,
                    "channel": channel,
                    "is_mesh": is_mesh,
                    "is_enabled": is_radio_enabled,
                }

                analysis["radios"].append(radio_info)

                # Skip TX power checks for mesh APs (they report None/0 even when working)
                if is_mesh:
                    continue

                # Skip TX power checks if radio is not enabled (no channel assigned)
                # This is normal for APs with disabled radios or 6GHz radios not in use
                if not is_radio_enabled:
                    continue

                # Skip TX power checks if in AUTO mode (controller is managing power intelligently)
                if tx_power_mode == "auto":
                    continue

                # Check for very low TX power (< 10dBm) - only for wired APs with manual power settings
                if tx_power < 10 and tx_power > 0:
                    if analysis["status"] == "healthy":
                        analysis["status"] = "warning"
                    analysis["score_penalty"] += 2

                    analysis["issues"].append(
                        {
                            "severity": "medium",
                            "type": "low_tx_power",
                            "ap": ap_name,
                            "band": band,
                            "tx_power": tx_power,
                            "tx_power_mode": tx_power_mode,
                            "message": f"{ap_name} ({band}): Low TX power ({tx_power}dBm) in {tx_power_mode} mode",
                            "impact": "Reduced coverage area and signal strength; clients at edge may have poor performance",
                            "recommendation": "Review TX power settings; increase to medium or high, or enable auto mode for dynamic optimization",
                        }
                    )

                # Check for TX power = 0 (error state) - only for wired APs with enabled radios
                # If we got here, the radio is enabled (has channel) but reports 0 power - that's unusual
                elif tx_power == 0:
                    # Only flag as critical if the radio is actually in use (has channel and clients)
                    # Otherwise it might just be an AP with disabled radio (like U7 Pro with 6GHz off)
                    analysis["status"] = "warning"
                    analysis["score_penalty"] += 5

                    analysis["issues"].append(
                        {
                            "severity": "medium",
                            "type": "radio_low_power",
                            "ap": ap_name,
                            "band": band,
                            "tx_power": tx_power,
                            "channel": channel,
                            "message": f"{ap_name} ({band} Ch {channel}): Very low TX power (0dBm) - check configuration",
                            "impact": "Minimal WiFi coverage; clients may have difficulty connecting",
                            "recommendation": "Review radio TX power settings; increase power level or enable auto mode",
                        }
                    )

        # Check for TX power inconsistencies (only for wired APs)
        ng_powers = [
            r["tx_power"]
            for r in analysis["radios"]
            if r["radio"] == "ng" and r["tx_power"] > 0 and not r.get("is_mesh")
        ]
        na_powers = [
            r["tx_power"]
            for r in analysis["radios"]
            if r["radio"] == "na" and r["tx_power"] > 0 and not r.get("is_mesh")
        ]

        if ng_powers and max(ng_powers) - min(ng_powers) > 10:
            analysis["recommendations"].append(
                {
                    "priority": "low",
                    "category": "power_consistency",
                    "message": f"2.4GHz TX power varies significantly across APs ({min(ng_powers)}-{max(ng_powers)}dBm)",
                    "action": "Consider normalizing TX power levels for consistent coverage, unless intentionally designed for specific coverage patterns",
                }
            )

        if na_powers and max(na_powers) - min(na_powers) > 10:
            analysis["recommendations"].append(
                {
                    "priority": "low",
                    "category": "power_consistency",
                    "message": f"5GHz TX power varies significantly across APs ({min(na_powers)}-{max(na_powers)}dBm)",
                    "action": "Consider normalizing TX power levels for consistent coverage",
                }
            )

        return analysis

    def _analyze_vlan_segmentation(self, clients):
        """
        Analyze VLAN usage and network segmentation
        """
        analysis = {
            "category": "VLAN Segmentation",
            "status": "healthy",
            "issues": [],
            "recommendations": [],
            "score_penalty": 0,
            "vlans": {},
        }

        # Collect VLAN usage
        for client in clients:
            vlan = client.get("vlan", 1)

            if vlan not in analysis["vlans"]:
                analysis["vlans"][vlan] = {"count": 0, "clients": []}

            analysis["vlans"][vlan]["count"] += 1
            analysis["vlans"][vlan]["clients"].append(
                {
                    "name": client.get("hostname", client.get("name", "Unknown")),
                    "mac": client.get("mac"),
                }
            )

        # Check for single VLAN usage
        if len(analysis["vlans"]) == 1 and 1 in analysis["vlans"]:
            total_clients = analysis["vlans"][1]["count"]

            if total_clients > 50:
                analysis["status"] = "warning"
                analysis["score_penalty"] += 5

                analysis["issues"].append(
                    {
                        "severity": "medium",
                        "type": "no_segmentation",
                        "vlan_count": 1,
                        "client_count": total_clients,
                        "message": f"All {total_clients} clients on default VLAN (no network segmentation)",
                        "impact": "Flat network increases security risk and broadcast domain size",
                        "recommendation": "Implement VLANs to segment: IoT devices, guest devices, security cameras, and critical infrastructure",
                    }
                )

            analysis["recommendations"].append(
                {
                    "priority": "medium",
                    "category": "segmentation",
                    "message": "Network segmentation recommended for security and performance",
                    "action": "Create separate VLANs for: IoT/Smart Home (Sonos, smart plugs), Guest WiFi, Security cameras, Management interfaces",
                }
            )

        # Check for proper segmentation
        elif len(analysis["vlans"]) > 1:
            default_vlan_pct = (
                analysis["vlans"].get(1, {}).get("count", 0)
                / sum(v["count"] for v in analysis["vlans"].values())
            ) * 100

            if default_vlan_pct > 80:
                analysis["recommendations"].append(
                    {
                        "priority": "low",
                        "category": "segmentation",
                        "message": f"{default_vlan_pct:.0f}% of clients still on default VLAN",
                        "action": "Consider moving more devices to dedicated VLANs for better segmentation",
                    }
                )

        return analysis

    def _analyze_firmware_status(self, devices):
        """
        Check firmware update status across all devices
        """
        analysis = {
            "category": "Firmware Status",
            "status": "healthy",
            "issues": [],
            "recommendations": [],
            "score_penalty": 0,
            "devices": {"up_to_date": [], "updates_available": []},
        }

        for device in devices:
            device_name = device.get("name", "Unknown")
            device_type = device.get("type", "unknown")
            current_version = device.get("version", "Unknown")
            upgradable = device.get("upgradable", False)
            upgrade_to = device.get("upgrade_to_firmware", None)

            device_info = {
                "name": device_name,
                "type": device_type,
                "current_version": current_version,
                "upgrade_to": upgrade_to,
            }

            if upgradable:
                analysis["devices"]["updates_available"].append(device_info)
                analysis["status"] = "warning"
                analysis["score_penalty"] += 2

                analysis["issues"].append(
                    {
                        "severity": "low",
                        "type": "firmware_update",
                        "device": device_name,
                        "device_type": device_type,
                        "current_version": current_version,
                        "available_version": upgrade_to,
                        "message": f"{device_name}: Firmware update available",
                        "impact": "Missing latest features, bug fixes, and security patches",
                        "recommendation": f"Update from {current_version} to {upgrade_to or 'latest version'}",
                    }
                )
            else:
                analysis["devices"]["up_to_date"].append(device_info)

        # Summary recommendation
        if len(analysis["devices"]["updates_available"]) > 0:
            analysis["recommendations"].append(
                {
                    "priority": "low",
                    "category": "firmware",
                    "message": f"{len(analysis['devices']['updates_available'])} device(s) have firmware updates available",
                    "action": "Schedule maintenance window to update devices; always backup configuration first",
                }
            )

        return analysis
