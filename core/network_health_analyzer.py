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

from utils.network_helpers import fix_rssi


class NetworkHealthAnalyzer:
    def __init__(self, client, site="default"):
        self.client = client
        self.site = site

    def analyze_network_health(self, devices, clients):
        """
        Comprehensive health analysis using category-weighted scoring.

        Categories and weights:
          - RF Health (35%): Channel plan, power balance, interference
          - Client Health (30%): Aggregate wireless client signal quality
          - Infrastructure (20%): Device stability, firmware, uplinks
          - Security (15%): VLAN segmentation, network hygiene
        """
        categories = {
            "device_stability": self._analyze_device_stability(devices),
            "broadcast_traffic": self._analyze_broadcast_traffic(devices),
            "channel_health": self._analyze_channel_health(devices),
            "radio_health": self._analyze_radio_health(devices),
            "vlan_segmentation": self._analyze_vlan_segmentation(clients),
            "firmware_status": self._analyze_firmware_status(devices),
        }

        results = {
            "severity": "low",
            "overall_score": 0,
            "categories": categories,
            "issues": [],
            "recommendations": [],
        }

        # Collect all issues and recommendations
        for category, analysis in categories.items():
            if analysis.get("issues"):
                results["issues"].extend(analysis["issues"])
            if analysis.get("recommendations"):
                results["recommendations"].extend(analysis["recommendations"])

        # Category-weighted scoring (each sub-score is 0-100)
        rf_score = self._compute_category_score(
            categories["channel_health"], categories["radio_health"]
        )
        infra_score = self._compute_category_score(
            categories["device_stability"],
            categories["broadcast_traffic"],
            categories["firmware_status"],
        )
        security_score = self._compute_category_score(categories["vlan_segmentation"])
        client_score = self._compute_client_aggregate(clients)

        results["overall_score"] = int(
            rf_score * 0.35 + client_score * 0.30 + infra_score * 0.20 + security_score * 0.15
        )
        results["overall_score"] = max(0, min(100, results["overall_score"]))

        # Store component scores for reporting
        results["component_scores"] = {
            "rf_health": int(rf_score),
            "client_health": int(client_score),
            "infrastructure": int(infra_score),
            "security": int(security_score),
        }

        # Determine overall severity
        if results["overall_score"] < 50:
            results["severity"] = "high"
        elif results["overall_score"] < 75:
            results["severity"] = "medium"
        else:
            results["severity"] = "low"

        return results

    @staticmethod
    def _compute_category_score(*analyses):
        """Compute 0-100 score from penalty-based category analyses."""
        total_penalty = sum(a.get("score_penalty", 0) for a in analyses)
        return max(0, 100 - total_penalty)

    @staticmethod
    def _compute_client_aggregate(clients):
        """Compute aggregate wireless client health 0-100 from RSSI distribution."""
        if not clients:
            return 100
        rssi_values = []
        for c in clients:
            if c.get("is_wired", False):
                continue
            rssi = fix_rssi(c.get("rssi", -100))
            rssi_values.append(rssi)
        if not rssi_values:
            return 100
        # Continuous RSSI-to-quality mapping, averaged across all wireless clients
        qualities = [max(0, min(100, (r + 95) * (100 / 45))) for r in rssi_values]
        return int(sum(qualities) / len(qualities))

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
            "event_span_days": lookback_days,
        }

        try:
            # Fetch all events, filter by lookback client-side
            import time as _time

            events_response = self.client.get(f"s/{self.site}/stat/event")

            if not events_response or "data" not in events_response:
                return result

            all_events = events_response.get("data", [])
            cutoff_ms = (_time.time() - lookback_days * 86400) * 1000
            events = [e for e in all_events if e.get("time", 0) >= cutoff_ms]
            # If no recent events, use all available but track actual span
            if not events and all_events:
                events = all_events
                times = [e.get("time", 0) for e in all_events if e.get("time", 0)]
                if times:
                    span_ms = max(times) - min(times)
                    result["event_span_days"] = max(1, span_ms / (86400 * 1000))
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
            if not uptime_seconds:
                continue  # Skip devices with no uptime data
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
                event_span = restart_info.get("event_span_days", 7)

                # Estimate restart rate from uptime when event log is stale
                # If uptime < 24h and event log is old, use uptime to estimate daily rate
                estimated_daily = None
                if event_span > 14 and uptime_hours > 0 and uptime_hours < 48:
                    # Event log is stale. Use historical event rate as best estimate
                    historical_rate = restart_count / max(event_span, 1)
                    estimated_daily = round(max(historical_rate, 1), 1)

                device_info["restart_count"] = restart_count
                device_info["manual_restart"] = is_manual
                device_info["event_span_days"] = round(event_span, 1)
                device_info["estimated_daily_restarts"] = estimated_daily

                # CYCLIC RESTARTS: Multiple restarts = stability issue (HIGH PRIORITY)
                if restart_count >= 3 or (estimated_daily and estimated_daily >= 2):
                    analysis["devices"]["cyclic_restart"].append(device_info)
                    analysis["status"] = "critical"
                    analysis["score_penalty"] += 15

                    # Build accurate message
                    if estimated_daily and event_span > 14:
                        msg = (
                            f"{device_name} restarts ~{estimated_daily}x/day "
                            f"(uptime {uptime_hours:.1f}h, {restart_count} events over {event_span:.0f} days)"
                        )
                    else:
                        span_str = f"{event_span:.0f} days" if event_span != 7 else "7 days"
                        msg = f"{device_name} has restarted {restart_count} times in {span_str} - CYCLIC BEHAVIOR"

                    analysis["issues"].append(
                        {
                            "severity": "high",
                            "type": "cyclic_restart",
                            "device": device_name,
                            "device_type": device_type,
                            "restart_count": restart_count,
                            "uptime_hours": uptime_hours,
                            "estimated_daily_restarts": estimated_daily,
                            "message": msg,
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
            # Normalize by switch uptime — cumulative counters are meaningless without rate
            uptime_seconds = switch.get("uptime", 86400) or 86400  # default 1 day
            uptime_hours = max(1, uptime_seconds / 3600)

            # Collect per-port broadcast RATES (packets/hour)
            port_rates = []
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
                bcast_per_hour = total_broadcast / uptime_hours

                # Identify connected device
                port_name = port.get("name", "")
                connected_device = port_name or f"Port {port_idx}"
                # Check if AP is connected to this port
                if port.get("port_poe"):
                    connected_device += " (PoE)"

                port_rates.append(bcast_per_hour)
                port_data[port_idx] = {
                    "broadcast": total_broadcast,
                    "bcast_per_hour": bcast_per_hour,
                    "multicast": rx_multicast + tx_multicast,
                    "rx_broadcast": rx_broadcast,
                    "tx_broadcast": tx_broadcast,
                    "connected_device": connected_device,
                }

            if not port_rates:
                continue

            avg_rate = sum(port_rates) / len(port_rates)
            max_rate = max(port_rates)

            # Only flag if a port's rate is 3x+ the average AND exceeds 10K/hour
            # Normal broadcast: ~500-5000/hour per port (mDNS, ARP, DHCP)
            # Abnormal: >10,000/hour AND significantly above peers
            for port_idx, data in port_data.items():
                rate = data["bcast_per_hour"]
                connected = data["connected_device"]
                ratio = rate / avg_rate if avg_rate > 0 else 0

                if rate > 10000 and ratio > 3.0:
                    analysis["status"] = "critical"
                    analysis["score_penalty"] += 5

                    analysis["issues"].append(
                        {
                            "severity": "high",
                            "type": "broadcast_storm",
                            "switch": switch_name,
                            "port": port_idx,
                            "broadcast_count": data["broadcast"],
                            "bcast_per_hour": int(rate),
                            "connected_device": connected,
                            "message": f"{switch_name} Port {port_idx} ({connected}): {int(rate):,}/hr broadcast rate ({ratio:.1f}x average)",
                            "impact": "Excessive broadcast traffic from this device consuming shared airtime",
                            "recommendation": f"Check device on port {port_idx} for network loops, chatty protocols, or misconfiguration",
                        }
                    )

                    analysis["ports_with_issues"].append(
                        {
                            "switch": switch_name,
                            "port": port_idx,
                            "broadcast": data["broadcast"],
                            "bcast_per_hour": int(rate),
                            "connected_device": connected,
                            "severity": "high",
                        }
                    )

                elif rate > 5000 and ratio > 2.0:
                    # Elevated but not critical
                    tx_rate = data["tx_broadcast"] / uptime_hours
                    if tx_rate > rate * 0.6:  # Mostly transmitting = source
                        if analysis["status"] == "healthy":
                            analysis["status"] = "warning"
                        analysis["score_penalty"] += 2

                        analysis["issues"].append(
                            {
                                "severity": "medium",
                                "type": "broadcast_source",
                                "switch": switch_name,
                                "port": port_idx,
                                "broadcast_count": data["tx_broadcast"],
                                "bcast_per_hour": int(tx_rate),
                                "connected_device": connected,
                                "message": f"{switch_name} Port {port_idx} ({connected}): {int(tx_rate):,}/hr TX broadcast rate — chatty device",
                                "impact": "Device generating elevated broadcast traffic",
                                "recommendation": f"Identify device on port {port_idx}; consider VLAN isolation if IoT device",
                            }
                        )

                        analysis["ports_with_issues"].append(
                            {
                                "switch": switch_name,
                                "port": port_idx,
                                "broadcast": data["broadcast"],
                                "bcast_per_hour": int(rate),
                                "connected_device": connected,
                                "severity": "medium",
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

        # Analyze 2.4GHz channel overlap
        # With N APs and 3 non-overlapping channels, floor(N/3) per channel is expected
        ng_channels = analysis["channel_assignments"].get("ng", {})
        total_24_aps = sum(len(aps) for aps in ng_channels.values())
        expected_per_channel = max(1, total_24_aps / 3) if total_24_aps > 0 else 1

        for channel, ap_list in ng_channels.items():
            # Only flag if channel has significantly MORE than expected share
            # (2+ above expected is genuine imbalance)
            if len(ap_list) > expected_per_channel + 1.5:
                analysis["status"] = "warning"
                analysis["score_penalty"] += 3

                ap_names = [ap["ap"] for ap in ap_list]

                analysis["issues"].append(
                    {
                        "severity": "low",
                        "type": "channel_imbalance",
                        "band": "2.4GHz",
                        "channel": channel,
                        "ap_count": len(ap_list),
                        "expected": round(expected_per_channel, 1),
                        "aps": ap_names,
                        "message": f"2.4GHz Channel {channel}: {len(ap_list)} APs (expected ~{expected_per_channel:.0f}) — imbalanced",
                        "impact": f"Uneven AP distribution increases contention on channel {channel}",
                        "recommendation": f"Redistribute APs more evenly across channels 1, 6, 11",
                    }
                )

            # Flag non-standard channels (not 1, 6, 11) — always a real problem
            if channel not in [1, 6, 11]:
                analysis["status"] = "warning"
                analysis["score_penalty"] += 5

                analysis["issues"].append(
                    {
                        "severity": "medium",
                        "type": "non_standard_channel",
                        "band": "2.4GHz",
                        "channel": channel,
                        "ap_count": len(ap_list),
                        "message": f"2.4GHz Channel {channel}: overlapping channel in use ({len(ap_list)} APs)",
                        "impact": "Overlapping channels cause adjacent-channel interference (worse than co-channel)",
                        "recommendation": "Move to non-overlapping channels: 1, 6, or 11",
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
                elif radio_name in ["6e", "6g"]:  # 6GHz radio names
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

                # TX power = 0 with radio enabled is unusual — may indicate config issue
                # (disabled radios already filtered out above)
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
            vlan_total = sum(v["count"] for v in analysis["vlans"].values())
            if vlan_total > 0:
                default_vlan_pct = (analysis["vlans"].get(1, {}).get("count", 0) / vlan_total) * 100
            else:
                default_vlan_pct = 0

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
