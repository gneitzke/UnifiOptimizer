#!/usr/bin/env python3
"""
Advanced Network Analysis Features

Implements enterprise-grade analysis:
- DFS event tracking
- Band steering analysis
- Fast roaming validation (802.11r/k/v)
- Airtime utilization monitoring
- Client capability matrix
- Network health scoring
"""

import json
from datetime import datetime, timedelta
from pathlib import Path


class AdvancedNetworkAnalyzer:
    """Advanced network analysis features for enterprise deployments"""

    def __init__(self, client, site="default"):
        self.client = client
        self.site = site
        self.device_capabilities = self._load_device_capabilities()

    def _load_device_capabilities(self):
        """Load WiFi device capability database from JSON file"""
        try:
            # Try loading from data/ directory relative to this file
            config_path = Path(__file__).parent.parent / "data" / "wifi_device_capabilities.json"

            if not config_path.exists():
                # Fallback to looking in current directory
                config_path = Path("data/wifi_device_capabilities.json")

            if config_path.exists():
                with open(config_path, "r") as f:
                    return json.load(f)
            else:
                # Return empty config if file not found (fail gracefully)
                return {
                    "wifi7_devices": {"patterns": []},
                    "wifi6e_devices": {"patterns": []},
                    "dual_band_devices": {"patterns": []},
                    "known_2.4ghz_only": {"patterns": []},
                }
        except Exception as e:
            # Log error but don't crash - return empty patterns
            print(f"Warning: Could not load device capabilities: {e}")
            return {
                "wifi7_devices": {"patterns": []},
                "wifi6e_devices": {"patterns": []},
                "dual_band_devices": {"patterns": []},
                "known_2.4ghz_only": {"patterns": []},
            }

    def _normalize_hostname(self, hostname):
        """Normalize hostname for flexible pattern matching"""
        return hostname.lower().replace("-", " ").replace("_", " ").replace("  ", " ").strip()

    def _check_device_pattern(self, hostname, pattern_list):
        """Check if normalized hostname matches any pattern in the list"""
        normalized_hostname = self._normalize_hostname(hostname)
        return any(pattern.lower() in normalized_hostname for pattern in pattern_list)

    def analyze_dfs_events(self, lookback_days=3):
        """
        Track DFS (Dynamic Frequency Selection) radar events

        DFS events cause sudden disconnects on channels 52-144 (5GHz)
        Returns events, affected APs, and recommendations
        """
        results = {
            "total_events": 0,
            "events_by_ap": {},
            "affected_channels": set(),
            "recommendations": [],
            "severity": "ok",
        }

        try:
            # Get events from controller
            within_hours = lookback_days * 24
            events_response = self.client.get(
                f"s/{self.site}/stat/event",
                params={"within": within_hours, "_limit": 1000},
            )

            if not events_response:
                return results

            events = events_response.get("data", [])
            if not events:
                return results

            # Filter DFS-related events
            dfs_keywords = ["dfs", "radar", "channel change", "cac"]

            for event in events:
                event_msg = event.get("msg", "").lower()
                event_key = event.get("key", "").lower()

                if any(keyword in event_msg or keyword in event_key for keyword in dfs_keywords):
                    results["total_events"] += 1

                    ap_name = event.get("ap_name", event.get("ap", "Unknown"))
                    channel = event.get("channel")

                    if ap_name not in results["events_by_ap"]:
                        results["events_by_ap"][ap_name] = []

                    results["events_by_ap"][ap_name].append(
                        {
                            "timestamp": event.get("time"),
                            "message": event.get("msg"),
                            "channel": channel,
                        }
                    )

                    if channel:
                        results["affected_channels"].add(channel)

            # Generate recommendations
            if results["total_events"] > 5:
                results["severity"] = "high"
                results["recommendations"].append(
                    {
                        "type": "dfs_avoidance",
                        "message": f'Detected {results["total_events"]} DFS events in {lookback_days} days',
                        "recommendation": "Switch to non-DFS channels (36, 40, 44, 48, 149-165)",
                        "priority": "high",
                    }
                )
            elif results["total_events"] > 0:
                results["severity"] = "medium"
                results["recommendations"].append(
                    {
                        "type": "dfs_monitoring",
                        "message": f'Detected {results["total_events"]} DFS events',
                        "recommendation": "Monitor DFS channel stability, consider non-DFS alternatives",
                        "priority": "medium",
                    }
                )

            # Per-AP recommendations
            for ap_name, events in results["events_by_ap"].items():
                if len(events) > 2:
                    results["recommendations"].append(
                        {
                            "type": "ap_dfs",
                            "device": ap_name,
                            "message": f"{len(events)} DFS events on {ap_name}",
                            "recommendation": f"Move {ap_name} to non-DFS channel",
                            "priority": "high",
                        }
                    )

        except Exception as e:
            results["error"] = str(e)

        results["affected_channels"] = list(results["affected_channels"])
        return results

    def analyze_client_population(self, clients):
        """
        Analyze the actual client device population on the network

        Provides data-driven insights about:
        - iOS/iPhone percentage (important for min RSSI thresholds)
        - Android percentage
        - Device capabilities (WiFi 5/6/6E/7)
        - Common device patterns

        This helps tailor recommendations to the actual network environment
        instead of using generic assumptions.

        Returns:
            dict: Client population statistics and recommendations
        """
        results = {
            "total_clients": 0,
            "ios_count": 0,
            "android_count": 0,
            "windows_count": 0,
            "mac_count": 0,
            "linux_count": 0,
            "iot_count": 0,
            "unknown_count": 0,
            "wifi_generations": {
                "wifi7": 0,
                "wifi6e": 0,
                "wifi6": 0,
                "wifi5": 0,
                "wifi4_and_older": 0,
            },
            "device_distribution": {
                "ios_percentage": 0,
                "android_percentage": 0,
                "desktop_percentage": 0,
                "iot_percentage": 0,
            },
            "recommendations": [],
        }

        if not clients:
            return results

        results["total_clients"] = len(clients)

        # Categorize each client
        for client in clients:
            hostname = client.get("hostname", "").lower()
            name = client.get("name", "").lower()
            oui = client.get("oui", "").lower()
            radio_proto = client.get("radio_proto", "").lower()

            # Detect OS/Device Type
            is_ios = (
                "iphone" in hostname or "ipad" in hostname or "iphone" in name or "ipad" in name
            )
            is_mac = "macbook" in hostname or "imac" in hostname or "mac" in hostname
            is_android = (
                "android" in hostname
                or "samsung" in hostname
                or "galaxy" in hostname
                or "pixel" in hostname
                or "oneplus" in hostname
            )
            is_windows = (
                "windows" in hostname
                or "desktop" in hostname
                or "laptop" in hostname
                or "pc-" in hostname
            )
            is_linux = "ubuntu" in hostname or "linux" in hostname
            is_iot = (
                "nest" in hostname
                or "ring" in hostname
                or "alexa" in hostname
                or "echo" in hostname
                or "chromecast" in hostname
                or "roku" in hostname
                or "firetv" in hostname
                or "apple-tv" in hostname
                or "smart" in hostname
                or "tv" in hostname
                or "printer" in hostname
                or "camera" in hostname
            )

            # Count by OS/Device Type
            if is_ios:
                results["ios_count"] += 1
            elif is_mac:
                results["mac_count"] += 1
            elif is_android:
                results["android_count"] += 1
            elif is_windows:
                results["windows_count"] += 1
            elif is_linux:
                results["linux_count"] += 1
            elif is_iot:
                results["iot_count"] += 1
            else:
                results["unknown_count"] += 1

            # Detect WiFi generation
            if "be" in radio_proto or "11be" in radio_proto:
                results["wifi_generations"]["wifi7"] += 1
            elif "ax-6e" in radio_proto or "6e" in radio_proto:
                results["wifi_generations"]["wifi6e"] += 1
            elif "ax" in radio_proto or "11ax" in radio_proto:
                results["wifi_generations"]["wifi6"] += 1
            elif "ac" in radio_proto or "11ac" in radio_proto:
                results["wifi_generations"]["wifi5"] += 1
            else:
                results["wifi_generations"]["wifi4_and_older"] += 1

        # Calculate percentages
        total = results["total_clients"]
        if total > 0:
            results["device_distribution"]["ios_percentage"] = round(
                (results["ios_count"] / total) * 100, 1
            )
            results["device_distribution"]["android_percentage"] = round(
                (results["android_count"] / total) * 100, 1
            )
            results["device_distribution"]["desktop_percentage"] = round(
                ((results["mac_count"] + results["windows_count"] + results["linux_count"]) / total)
                * 100,
                1,
            )
            results["device_distribution"]["iot_percentage"] = round(
                (results["iot_count"] / total) * 100, 1
            )

        # Generate recommendations based on population
        ios_pct = results["device_distribution"]["ios_percentage"]

        if ios_pct > 50:
            results["recommendations"].append(
                {
                    "type": "ios_dominant",
                    "message": f"Network is {ios_pct}% iOS devices",
                    "recommendation": "Use iOS-friendly min RSSI thresholds (-78/-75 dBm) to prevent frequent iPhone disconnects",
                    "priority": "high",
                }
            )
        elif ios_pct > 20:
            results["recommendations"].append(
                {
                    "type": "ios_significant",
                    "message": f"Network has {ios_pct}% iOS devices",
                    "recommendation": "Consider iOS-friendly min RSSI thresholds (-78/-75 dBm) to reduce iPhone disconnect rates",
                    "priority": "medium",
                }
            )

        # WiFi 6E/7 recommendations
        modern_wifi_count = (
            results["wifi_generations"]["wifi6e"] + results["wifi_generations"]["wifi7"]
        )
        if modern_wifi_count > 0 and total > 0:
            modern_pct = round((modern_wifi_count / total) * 100, 1)
            if modern_pct > 30:
                results["recommendations"].append(
                    {
                        "type": "modern_wifi",
                        "message": f"{modern_pct}% of clients support WiFi 6E/7",
                        "recommendation": "Ensure 6GHz radios are enabled to take advantage of modern client capabilities",
                        "priority": "medium",
                    }
                )

        return results

    def analyze_band_steering(self, devices, clients):
        """
        Analyze band steering configuration and effectiveness

        Identifies:
        - Clients on 2.4GHz that support 5GHz or 6GHz
        - Clients on 5GHz that support 6GHz
        - APs with band steering disabled
        - Sticky clients refusing to move
        """
        results = {
            "band_steering_enabled": {},
            "misplaced_clients": [],
            "mesh_aps_detected": [],
            "mesh_aps_with_band_steering": [],
            "dual_band_clients_on_2ghz": 0,
            "tri_band_clients_suboptimal": 0,  # 6GHz-capable clients not on 6GHz
            "wifi7_clients_suboptimal": 0,  # WiFi 7 capable clients not on 6GHz
            "recommendations": [],
            "severity": "ok",
        }

        try:
            # Check AP configurations
            for device in devices:
                if device.get("type") != "uap":
                    continue

                ap_name = device.get("name", "Unnamed AP")
                ap_id = device.get("_id")

                # **CRITICAL: Detect mesh APs (wireless uplink)**
                # Band steering can interfere with mesh uplink stability
                uplink_type = device.get("uplink", {}).get("type", "wire")
                is_mesh = uplink_type == "wireless"

                if is_mesh:
                    uplink_rssi = device.get("uplink", {}).get("rssi")
                    results["mesh_aps_detected"].append(
                        {
                            "name": ap_name,
                            "uplink_rssi": uplink_rssi,
                        }
                    )

                # Check band steering setting - UniFi may use either field name
                # Try both 'bandsteering_mode' (older) and 'band_steering_mode' (newer)
                band_steering = device.get("bandsteering_mode") or device.get(
                    "band_steering_mode", "off"
                )

                # Check if AP is tri-band (has 6GHz radio)
                radio_table = device.get("radio_table", [])
                has_6ghz = any(radio.get("radio") in ["6e", "ax", "6g"] for radio in radio_table)

                # Band steering is considered enabled if mode is not "off"
                # Valid modes: "prefer_5g", "equal", "prefer_2g", etc.
                is_enabled = band_steering != "off"
                results["band_steering_enabled"][ap_name] = is_enabled

                # **Warn if mesh AP has band steering enabled**
                if is_mesh and is_enabled:
                    uplink_rssi = device.get("uplink", {}).get("rssi", "Unknown")
                    results["mesh_aps_with_band_steering"].append(
                        {
                            "name": ap_name,
                            "mode": band_steering,
                            "uplink_rssi": uplink_rssi,
                        }
                    )
                    results["recommendations"].append(
                        {
                            "type": "mesh_band_steering_warning",
                            "device": ap_name,
                            "message": f"⚠️  MESH AP {ap_name} has band steering enabled ({band_steering})",
                            "recommendation": f"Consider disabling band steering on mesh APs if uplink stability issues occur (current uplink: {uplink_rssi} dBm). Band steering can interfere with mesh uplink connections.",
                            "priority": "medium",
                            "is_mesh": True,
                            "band_steering_mode": band_steering,
                            "uplink_rssi": uplink_rssi,
                        }
                    )

                if not is_enabled and not is_mesh:
                    # Only recommend enabling band steering on NON-mesh APs
                    # Determine recommendation message based on AP capabilities
                    if has_6ghz:
                        recommendation_msg = (
                            "Enable band steering to move capable clients to 5GHz/6GHz"
                        )
                        ap_type = "tri-band"
                    else:
                        recommendation_msg = "Enable band steering to move capable clients to 5GHz"
                        ap_type = "dual-band"

                    results["recommendations"].append(
                        {
                            "type": "band_steering",
                            "device": ap_name,
                            "message": f"Band steering disabled on {ap_name} ({ap_type} AP)",
                            "recommendation": recommendation_msg,
                            "priority": "medium",
                            "has_6ghz": has_6ghz,
                        }
                    )

            # Analyze client placement
            for client in clients:
                if not client.get("is_wired", False):
                    radio = client.get("radio", "")
                    radio_proto = client.get("radio_proto", "")

                    # Check if client is on 2.4GHz but supports higher bands
                    if radio == "ng":  # 2.4GHz
                        hostname = client.get("hostname", client.get("name", "")).lower()
                        tx_rate = client.get("tx_rate", 0)
                        rx_rate = client.get("rx_rate", 0)
                        nss = client.get("nss", 1)  # Number of spatial streams

                        # Multi-indicator 5GHz capability detection
                        is_dual_band = False
                        detection_reason = None

                        # Detect dual-band (5GHz) and tri-band (6GHz) capability
                        is_dual_band = False
                        is_6ghz_capable = False
                        is_wifi7_capable = False
                        detection_reason = None

                        # Method 1: WiFi 7 (802.11be) detection - Latest standard, 6GHz + MLO
                        if "be" in radio_proto or "11be" in radio_proto or client.get("is_11be"):
                            is_dual_band = True
                            is_6ghz_capable = True
                            is_wifi7_capable = True
                            detection_reason = f"WiFi 7 capable ({radio_proto})"

                        # Method 2: WiFi 6E (802.11ax-6E) detection - 6GHz capable
                        elif (
                            "ax-6e" in radio_proto
                            or "6e" in radio_proto
                            or client.get("is_11ax_6e")
                        ):
                            is_dual_band = True
                            is_6ghz_capable = True
                            detection_reason = f"WiFi 6E capable ({radio_proto})"

                        # Method 3: WiFi 6 (802.11ax) or WiFi 5 (802.11ac) - at least dual-band
                        elif (
                            "ac" in radio_proto
                            or "ax" in radio_proto
                            or client.get("is_11ac")
                            or client.get("is_11ax")
                        ):
                            is_dual_band = True
                            detection_reason = f"API indicates {radio_proto}"

                        # Method 3: High data rates (>72 Mbps suggests 802.11n+ dual-band capable)
                        elif tx_rate > 72000 or rx_rate > 72000:
                            is_dual_band = True
                            detection_reason = f"High data rate: TX={tx_rate/1000:.0f}Mbps RX={rx_rate/1000:.0f}Mbps"

                        # Method 4: Multiple spatial streams (>1 suggests modern dual-band device)
                        elif nss >= 2:
                            is_dual_band = True
                            detection_reason = f"Multiple spatial streams (nss={nss})"

                        # Method 5: Known device patterns for WiFi 7 capability
                        # Check against external device database
                        elif self._check_device_pattern(
                            hostname, self.device_capabilities["wifi7_devices"]["patterns"]
                        ):
                            is_dual_band = True
                            is_6ghz_capable = True
                            is_wifi7_capable = True
                            detection_reason = f"Known WiFi 7 device: {hostname}"

                        # Method 6: Known device patterns for 6GHz (WiFi 6E) capability
                        # Check against external device database
                        elif self._check_device_pattern(
                            hostname, self.device_capabilities["wifi6e_devices"]["patterns"]
                        ):
                            is_dual_band = True
                            is_6ghz_capable = True
                            detection_reason = f"Known WiFi 6E device: {hostname}"

                        # Method 7: Known dual-band device names (generic patterns)
                        # Check against external device database
                        elif self._check_device_pattern(
                            hostname, self.device_capabilities["dual_band_devices"]["patterns"]
                        ):
                            is_dual_band = True
                            detection_reason = f"Known dual-band device: {hostname}"

                        if is_dual_band or is_6ghz_capable:
                            results["dual_band_clients_on_2ghz"] += 1
                            if is_6ghz_capable:
                                results["tri_band_clients_suboptimal"] += 1
                            if is_wifi7_capable:
                                results["wifi7_clients_suboptimal"] += 1

                            # Get AP name - use ap_name if available, otherwise lookup by ap_mac
                            ap_display = client.get("ap_name", "")
                            if not ap_display or ap_display == "Unknown":
                                ap_mac = client.get("ap_mac", "")
                                if ap_mac and devices:
                                    for device in devices:
                                        if device.get("mac") == ap_mac:
                                            ap_display = device.get("name", ap_mac)
                                            break
                                if not ap_display:
                                    ap_display = f"Unknown (MAC: {ap_mac})" if ap_mac else "Unknown"

                            # Get last seen time
                            last_seen = client.get("last_seen", client.get("_last_seen", 0))
                            import time

                            if last_seen:
                                time_ago = int(time.time()) - last_seen
                                if time_ago < 60:
                                    last_seen_str = "Just now"
                                elif time_ago < 3600:
                                    last_seen_str = f"{time_ago // 60}m ago"
                                elif time_ago < 86400:
                                    last_seen_str = f"{time_ago // 3600}h ago"
                                else:
                                    last_seen_str = f"{time_ago // 86400}d ago"
                            else:
                                last_seen_str = "Unknown"

                            # Determine band capability label
                            if is_wifi7_capable:
                                capability = "WiFi 7 capable"
                            elif is_6ghz_capable:
                                capability = "6GHz capable"
                            else:
                                capability = "5GHz capable"

                            results["misplaced_clients"].append(
                                {
                                    "hostname": client.get(
                                        "hostname", client.get("name", "Unknown")
                                    ),
                                    "mac": client.get("mac"),
                                    "ap": ap_display,
                                    "last_seen": last_seen_str,
                                    "rssi": client.get("rssi"),
                                    "radio_proto": radio_proto,
                                    "tx_rate": tx_rate,
                                    "detection_reason": detection_reason,
                                    "current_band": "2.4GHz",
                                    "capability": capability,
                                    "is_6ghz_capable": is_6ghz_capable,
                                    "is_wifi7_capable": is_wifi7_capable,
                                }
                            )

                    # Also check if client is on 5GHz but supports 6GHz
                    elif radio in ["na", "ac", "ax"]:  # 5GHz radios
                        hostname = client.get("hostname", client.get("name", "")).lower()
                        radio_proto = client.get("radio_proto", "")
                        tx_rate = client.get("tx_rate", 0)

                        is_6ghz_capable = False
                        is_wifi7_capable = False
                        detection_reason = None

                        # WiFi 7 detection (includes 6GHz capability)
                        if "be" in radio_proto or "11be" in radio_proto or client.get("is_11be"):
                            is_wifi7_capable = True
                            is_6ghz_capable = True
                            detection_reason = f"WiFi 7 capable ({radio_proto})"

                        # WiFi 6E detection
                        elif (
                            "ax-6e" in radio_proto
                            or "6e" in radio_proto
                            or client.get("is_11ax_6e")
                        ):
                            is_6ghz_capable = True
                            detection_reason = f"WiFi 6E capable ({radio_proto})"

                        # Known WiFi 7 device patterns
                        # Check against external device database
                        elif self._check_device_pattern(
                            hostname, self.device_capabilities["wifi7_devices"]["patterns"]
                        ):
                            is_wifi7_capable = True
                            is_6ghz_capable = True
                            detection_reason = f"Known WiFi 7 device: {hostname}"

                        # Known 6GHz (WiFi 6E) device patterns
                        # Check against external device database
                        elif self._check_device_pattern(
                            hostname, self.device_capabilities["wifi6e_devices"]["patterns"]
                        ):
                            is_6ghz_capable = True
                            detection_reason = f"Known WiFi 6E device: {hostname}"

                        if is_6ghz_capable:
                            results["tri_band_clients_suboptimal"] += 1
                            if is_wifi7_capable:
                                results["wifi7_clients_suboptimal"] += 1

                            # Get AP name
                            ap_display = client.get("ap_name", "")
                            if not ap_display or ap_display == "Unknown":
                                ap_mac = client.get("ap_mac", "")
                                if ap_mac and devices:
                                    for device in devices:
                                        if device.get("mac") == ap_mac:
                                            ap_display = device.get("name", ap_mac)
                                            break
                                if not ap_display:
                                    ap_display = f"Unknown (MAC: {ap_mac})" if ap_mac else "Unknown"

                            # Get last seen time
                            last_seen = client.get("last_seen", client.get("_last_seen", 0))
                            import time

                            if last_seen:
                                time_ago = int(time.time()) - last_seen
                                if time_ago < 60:
                                    last_seen_str = "Just now"
                                elif time_ago < 3600:
                                    last_seen_str = f"{time_ago // 60}m ago"
                                elif time_ago < 86400:
                                    last_seen_str = f"{time_ago // 3600}h ago"
                                else:
                                    last_seen_str = f"{time_ago // 86400}d ago"
                            else:
                                last_seen_str = "Unknown"

                            # Determine band capability label
                            if is_wifi7_capable:
                                capability = "WiFi 7 capable"
                            else:
                                capability = "6GHz capable"

                            results["misplaced_clients"].append(
                                {
                                    "hostname": client.get(
                                        "hostname", client.get("name", "Unknown")
                                    ),
                                    "mac": client.get("mac"),
                                    "ap": ap_display,
                                    "last_seen": last_seen_str,
                                    "rssi": client.get("rssi"),
                                    "radio_proto": radio_proto,
                                    "tx_rate": tx_rate,
                                    "detection_reason": detection_reason,
                                    "current_band": "5GHz",
                                    "capability": capability,
                                    "is_6ghz_capable": is_6ghz_capable,
                                    "is_wifi7_capable": is_wifi7_capable,
                                }
                            )

            # Generate recommendations
            total_misplaced = results["dual_band_clients_on_2ghz"]
            has_6ghz_clients = results["tri_band_clients_suboptimal"] > 0
            has_wifi7_clients = results["wifi7_clients_suboptimal"] > 0

            if total_misplaced > 5:
                results["severity"] = "high"
                message = f"{total_misplaced} capable clients on suboptimal bands"
                if has_wifi7_clients:
                    message += f" ({results['wifi7_clients_suboptimal']} WiFi 7)"
                elif has_6ghz_clients:
                    message += f" ({results['tri_band_clients_suboptimal']} WiFi 6E)"
                results["recommendations"].append(
                    {
                        "type": "band_steering_critical",
                        "message": message,
                        "recommendation": "Enable band steering on all APs and verify 5GHz/6GHz signal coverage",
                        "priority": "high",
                        "affected_clients": total_misplaced,
                    }
                )
            elif total_misplaced > 0:
                results["severity"] = "medium"
                message = f"{total_misplaced} clients on suboptimal bands"
                if has_wifi7_clients:
                    message += f" ({results['wifi7_clients_suboptimal']} WiFi 7)"
                elif has_6ghz_clients:
                    message += f" ({results['tri_band_clients_suboptimal']} WiFi 6E)"
                results["recommendations"].append(
                    {
                        "type": "band_steering_warning",
                        "message": message,
                        "recommendation": "Review band steering settings and higher band coverage (5GHz/6GHz)",
                        "priority": "medium",
                        "affected_clients": total_misplaced,
                    }
                )

            # Separate recommendation for 6GHz-specific optimization
            if has_6ghz_clients and results["tri_band_clients_suboptimal"] >= 3:
                if has_wifi7_clients:
                    message = (
                        f"{results['wifi7_clients_suboptimal']} WiFi 7 clients not using 6GHz band"
                    )
                    recommendation = "Enable and optimize 6GHz band on compatible APs - WiFi 7 clients can utilize 320MHz channels and MLO for maximum performance"
                else:
                    message = f"{results['tri_band_clients_suboptimal']} WiFi 6E clients not using 6GHz band"
                    recommendation = (
                        "Enable and optimize 6GHz band on compatible APs for best performance"
                    )

                results["recommendations"].append(
                    {
                        "type": "6ghz_optimization",
                        "message": message,
                        "recommendation": recommendation,
                        "priority": "medium",
                        "affected_clients": results["tri_band_clients_suboptimal"],
                    }
                )

        except Exception as e:
            results["error"] = str(e)

        return results

    def analyze_fast_roaming(self, devices):
        """
        Validate fast roaming (802.11r/k/v) configuration

        NOTE: 802.11r/k/v settings are configured per-WLAN (network), not per-AP.
        We query WLAN groups to get accurate configuration. If the WLAN endpoint
        is unavailable, we report that roaming config could not be determined
        rather than guessing incorrectly.
        """
        results = {
            "roaming_features": {
                "802.11r": {"enabled_count": 0, "disabled_count": 0, "wlans": []},
                "802.11k": {"enabled_count": 0, "disabled_count": 0, "wlans": []},
                "802.11v": {"enabled_count": 0, "disabled_count": 0, "wlans": []},
            },
            "consistent_config": True,
            "recommendations": [],
            "severity": "ok",
            "wlan_count": 0,
        }

        try:
            # 802.11r/k/v are WLAN-level settings, not device-level
            wlan_response = self.client.get(f"s/{self.site}/rest/wlanconf")
            wlans = wlan_response.get("data", []) if wlan_response else []

            if not wlans:
                results["recommendations"].append(
                    {
                        "type": "roaming_unknown",
                        "message": "Could not retrieve WLAN configuration to check 802.11r/k/v",
                        "recommendation": "Verify fast roaming settings manually in UniFi Controller under WiFi settings",
                        "priority": "low",
                    }
                )
                return results

            results["wlan_count"] = len(wlans)

            for wlan in wlans:
                wlan_name = wlan.get("name", "Unnamed WLAN")
                is_enabled = wlan.get("enabled", True)
                if not is_enabled:
                    continue

                # 802.11r (Fast BSS Transition)
                ft_enabled = wlan.get("fast_roaming_enabled", False)
                if ft_enabled:
                    results["roaming_features"]["802.11r"]["enabled_count"] += 1
                    results["roaming_features"]["802.11r"]["wlans"].append(wlan_name)
                else:
                    results["roaming_features"]["802.11r"]["disabled_count"] += 1

                # 802.11k (Radio Resource Management / Neighbor Reports)
                # UniFi calls this "rrm_enabled" or enables it implicitly
                rrm_enabled = wlan.get("rrm_enabled", False)
                if rrm_enabled:
                    results["roaming_features"]["802.11k"]["enabled_count"] += 1
                    results["roaming_features"]["802.11k"]["wlans"].append(wlan_name)
                else:
                    results["roaming_features"]["802.11k"]["disabled_count"] += 1

                # 802.11v (BSS Transition Management)
                bss_transition = wlan.get("bss_transition", False)
                if bss_transition:
                    results["roaming_features"]["802.11v"]["enabled_count"] += 1
                    results["roaming_features"]["802.11v"]["wlans"].append(wlan_name)
                else:
                    results["roaming_features"]["802.11v"]["disabled_count"] += 1

            # Check consistency and generate recommendations
            for feature, data in results["roaming_features"].items():
                if data["enabled_count"] > 0 and data["disabled_count"] > 0:
                    results["consistent_config"] = False
                    results["recommendations"].append(
                        {
                            "type": "roaming_inconsistent",
                            "feature": feature,
                            "message": f'{feature} enabled on {data["enabled_count"]} WLAN(s) but disabled on {data["disabled_count"]}',
                            "recommendation": f"Enable {feature} on all WLANs for consistent roaming behavior",
                            "priority": "high",
                        }
                    )

            for feature, data in results["roaming_features"].items():
                if data["enabled_count"] == 0 and data["disabled_count"] > 0:
                    results["severity"] = "medium"
                    results["recommendations"].append(
                        {
                            "type": "roaming_disabled",
                            "feature": feature,
                            "message": f"{feature} disabled on all WLANs",
                            "recommendation": f"Enable {feature} to improve roaming performance and VoIP quality",
                            "priority": "medium",
                        }
                    )

        except Exception as e:
            results["error"] = str(e)

        return results

    def analyze_mesh_necessity(self, devices):
        """
        Analyze if mesh configuration is needed or can be optimized

        Checks:
        - Are any APs actually using wireless mesh?
        - Are all APs wired (PoE)?
        - Should mesh be disabled for performance?

        Benefits of disabling mesh when not needed:
        - Reduced radio overhead (no mesh beaconing)
        - Better client performance (less airtime used for mesh)
        - Simplified network management
        - Lower power consumption

        Trade-offs:
        - Flexibility: Can't quickly add wireless mesh APs without re-enabling
        - Redundancy: Mesh can provide backup if wired uplink fails (if supported by model)

        Args:
            devices: List of AP device dicts

        Returns:
            dict: Analysis results with recommendations
        """
        results = {
            "mesh_enabled_site_wide": None,  # Cannot determine from device API alone
            "mesh_aps_count": 0,
            "wired_aps_count": 0,
            "mesh_aps": [],
            "wired_aps": [],
            "recommendations": [],
            "severity": "ok",
        }

        try:
            aps = [d for d in devices if d.get("type") == "uap"]

            for ap in aps:
                ap_name = ap.get("name", "Unnamed AP")
                uplink_type = ap.get("uplink", {}).get("type", "wire")
                is_mesh = uplink_type == "wireless"
                uplink_rssi = ap.get("uplink", {}).get("rssi")

                if is_mesh:
                    results["mesh_aps_count"] += 1
                    results["mesh_aps"].append(
                        {
                            "name": ap_name,
                            "uplink_rssi": uplink_rssi,
                            "mac": ap.get("mac"),
                        }
                    )
                else:
                    results["wired_aps_count"] += 1
                    results["wired_aps"].append(
                        {
                            "name": ap_name,
                            "mac": ap.get("mac"),
                        }
                    )

            # If NO mesh APs are detected, recommend disabling mesh
            if results["mesh_aps_count"] == 0 and results["wired_aps_count"] > 0:
                results["recommendations"].append(
                    {
                        "type": "disable_unused_mesh",
                        "message": f"All {results['wired_aps_count']} APs are wired (PoE) - mesh not in use",
                        "recommendation": (
                            "Consider disabling mesh/wireless uplink in site settings to reduce radio overhead and improve client performance. "
                            "This frees up airtime currently used for mesh beaconing."
                        ),
                        "priority": "low",
                        "benefits": [
                            "Reduced radio overhead (no mesh beaconing)",
                            "More airtime for client traffic",
                            "Simplified network management",
                            "Slightly lower power consumption",
                        ],
                        "tradeoffs": [
                            "Cannot quickly deploy wireless mesh APs without re-enabling",
                            "Loss of potential wired uplink failover (model-dependent)",
                        ],
                        "best_for": "Networks where all APs are hardwired via PoE and mesh won't be needed",
                        "not_recommended_if": "Planning to add mesh APs or want failover flexibility",
                    }
                )
                results["severity"] = "info"
            elif results["mesh_aps_count"] > 0:
                # Mesh is actively used - note this in results
                results["recommendations"].append(
                    {
                        "type": "mesh_in_use",
                        "message": f"{results['mesh_aps_count']} AP(s) using wireless mesh - mesh configuration is needed",
                        "recommendation": "Mesh is actively used and should remain enabled. Ensure mesh APs have optimal uplink signal.",
                        "priority": "info",
                        "mesh_aps": results["mesh_aps"],
                    }
                )
                results["severity"] = "ok"

        except Exception as e:
            results["error"] = str(e)

        return results

    def analyze_min_rssi(self, devices, clients=None, strategy="optimal"):
        """
        Analyze minimum RSSI configuration across APs

        Min RSSI forces weak clients to roam, preventing sticky client problems
        and improving overall network performance

        **CRITICAL: Mesh AP Protection**
        Mesh APs with wireless uplinks are EXCLUDED from min RSSI recommendations.
        Aggressive min RSSI can break mesh uplink connections, causing AP drops.
        Mesh uplinks need to maintain connection under all conditions.

        **iPhone/iOS Consideration:**
        iPhones are known to disconnect more frequently with aggressive min RSSI
        thresholds. iOS devices tend to hold onto APs longer than Android and may
        experience higher disconnect rates at -75/-72 dBm thresholds.

        Args:
            devices: List of AP device dicts
            clients: Optional list of client dicts (for iOS detection)

        Returns:
            dict: Min RSSI analysis with recommendations
        """
        results = {
            "radios_with_min_rssi": [],
            "radios_without_min_rssi": [],
            "mesh_aps_with_min_rssi": [],  # Track mesh APs with min RSSI (warning!)
            "mesh_aps_detected": [],
            "total_radios": 0,
            "enabled_count": 0,
            "disabled_count": 0,
            "recommendations": [],
            "severity": "ok",
            "ios_devices_detected": False,
            "ios_device_count": 0,
            "strategy": strategy,  # Store strategy for display
        }

        # Detect iOS/iPhone devices on network
        ios_count = 0
        if clients:
            for client in clients:
                hostname = client.get("hostname", "").lower()
                name = client.get("name", "").lower()
                oui = client.get("oui", "").lower()

                # Check for iPhone/iPad/iOS indicators
                is_ios = (
                    "iphone" in hostname
                    or "ipad" in hostname
                    or "iphone" in name
                    or "ipad" in name
                    or "apple" in oui
                )

                if is_ios:
                    ios_count += 1

        results["ios_devices_detected"] = ios_count > 0
        results["ios_device_count"] = ios_count

        # Load thresholds from config (user-customizable via data/config.yaml)
        from utils.config import get_threshold

        OPTIMAL_MIN_RSSI_24GHZ = get_threshold("min_rssi.optimal.2.4ghz", -75)
        OPTIMAL_MIN_RSSI_5GHZ = get_threshold("min_rssi.optimal.5ghz", -72)
        OPTIMAL_MIN_RSSI_6GHZ = get_threshold("min_rssi.optimal.6ghz", -70)

        CONSERVATIVE_MIN_RSSI_24GHZ = get_threshold("min_rssi.max_connectivity.2.4ghz", -80)
        CONSERVATIVE_MIN_RSSI_5GHZ = get_threshold("min_rssi.max_connectivity.5ghz", -77)
        CONSERVATIVE_MIN_RSSI_6GHZ = get_threshold("min_rssi.max_connectivity.6ghz", -75)

        # Calculate iOS percentage for recommendations
        total_clients = len(clients) if clients else 0
        ios_percentage = (ios_count / total_clients * 100) if total_clients > 0 else 0

        # Choose thresholds based on STRATEGY parameter (user's choice)
        if strategy == "max_connectivity":
            # CONSERVATIVE: Stay connected as long as possible
            recommended_24 = CONSERVATIVE_MIN_RSSI_24GHZ
            recommended_5 = CONSERVATIVE_MIN_RSSI_5GHZ
            recommended_6 = CONSERVATIVE_MIN_RSSI_6GHZ
            threshold_type = "conservative"
        else:  # strategy == "optimal" (default)
            # AGGRESSIVE: Force early roaming for better performance
            recommended_24 = OPTIMAL_MIN_RSSI_24GHZ
            recommended_5 = OPTIMAL_MIN_RSSI_5GHZ
            recommended_6 = OPTIMAL_MIN_RSSI_6GHZ
            threshold_type = "optimal"

        results["threshold_type"] = threshold_type
        results["ios_percentage"] = ios_percentage

        # Note: Values depend on environment:
        # - High-density: -67 to -72 dBm (force aggressive roaming)
        # - Standard office: -72 to -75 dBm (balanced, good for Android/Windows)
        # - iOS-heavy networks: -75 to -78 dBm (prevents iPhone disconnect issues)
        # - Large coverage areas: -75 to -80 dBm (avoid premature disconnects)
        #
        # 6GHz-specific considerations (CRITICAL - line-of-sight sensitive!):
        # - MUCH worse propagation than 5GHz (~30-50% less range)
        # - Poor wall penetration - highly line-of-sight dependent
        # - Optimal: -68 dBm (ensures good throughput before roaming)
        # - Conservative: -72 dBm (realistic limit for usable performance)
        # - Beyond -72 dBm, 6GHz throughput degrades rapidly
        # - Monitor client RSSI carefully (< -70 dBm = edge of coverage)

        try:
            for device in devices:
                if device.get("type") != "uap":
                    continue

                ap_name = device.get("name", "Unnamed AP")
                ap_id = device.get("_id")
                radio_table = device.get("radio_table", [])

                # **CRITICAL: Detect mesh APs (wireless uplink)**
                # Mesh APs must maintain connection under all conditions
                # Min RSSI can cause mesh uplinks to drop
                uplink_type = device.get("uplink", {}).get("type", "wire")
                is_mesh_child = uplink_type == "wireless"

                # **CRITICAL: Detect mesh PARENT APs (have mesh children connecting to them)**
                # Mesh parent APs with min RSSI could KICK OFF their mesh children!
                # This is SUPER CRITICAL - breaks the entire mesh network downstream
                ap_mac = device.get("mac")
                is_mesh_parent = False
                mesh_children_count = 0
                if ap_mac:
                    for other_device in devices:
                        if other_device.get("type") == "uap":
                            other_uplink = other_device.get("uplink", {})
                            if other_uplink.get("type") == "wireless" and (
                                other_uplink.get("uplink_remote_mac") == ap_mac
                            ):
                                is_mesh_parent = True
                                mesh_children_count += 1  # Any mesh involvement = protection
                is_mesh = is_mesh_child or is_mesh_parent

                # **COVERAGE EXTENSION DETECTION**
                # Mesh nodes with many weak-signal clients are extending coverage
                # to remote areas. These need MAXIMUM connectivity tolerance.
                is_coverage_extender = False
                weak_client_count = 0
                mesh_client_count = 0
                avg_client_rssi = None
                clients_with_no_alternative = 0

                # Check coverage extension for ANY mesh AP (child or parent)
                if is_mesh and clients:
                    # Find clients connected to this mesh AP
                    ap_mac = device.get("mac")
                    ap_clients = [c for c in clients if c.get("ap_mac") == ap_mac]
                    mesh_client_count = len(ap_clients)

                    # Build list of all other AP MACs (potential alternatives)
                    other_ap_macs = set()
                    for other_device in devices:
                        if other_device.get("type") == "uap":
                            other_mac = other_device.get("mac")
                            if other_mac and other_mac != ap_mac:
                                other_ap_macs.add(other_mac)

                    if ap_clients:
                        # Analyze client signal strength
                        client_rssi_values = []
                        for client in ap_clients:
                            rssi = client.get("rssi")
                            if rssi:
                                client_rssi_values.append(rssi)
                                # Weak signal: worse than -75 dBm
                                if rssi < -75:
                                    weak_client_count += 1

                            # Check if client has ever seen any other AP
                            # If client has no radio_table_stats or only sees this AP, they have no alternative
                            radio_table = client.get("radio_table_stats", [])
                            if not radio_table:
                                # Client has never reported seeing any other AP
                                clients_with_no_alternative += 1
                            else:
                                # Check if client has ever seen another AP with decent signal
                                has_alternative = False
                                for radio_entry in radio_table:
                                    radio_ap_mac = radio_entry.get("ap_mac")
                                    radio_rssi = radio_entry.get("signal")
                                    # Alternative AP must have at least -80 dBm (barely usable)
                                    if (
                                        radio_ap_mac in other_ap_macs
                                        and radio_rssi
                                        and radio_rssi > -80
                                    ):
                                        has_alternative = True
                                        break

                                if not has_alternative:
                                    clients_with_no_alternative += 1

                        if client_rssi_values:
                            avg_client_rssi = sum(client_rssi_values) / len(client_rssi_values)

                        # Coverage extender criteria (more comprehensive):
                        # - >50% of clients have weak signal, OR
                        # - Average client RSSI < -70 dBm, OR
                        # - >30% of clients have no alternative AP
                        if mesh_client_count > 0:
                            weak_percentage = (weak_client_count / mesh_client_count) * 100
                            no_alternative_percentage = (
                                clients_with_no_alternative / mesh_client_count
                            ) * 100

                            # Check if this is a coverage extender
                            is_weak_signals = weak_percentage > 50
                            is_low_avg_rssi = avg_client_rssi and avg_client_rssi < -70
                            is_no_alternatives = no_alternative_percentage > 30

                            if is_weak_signals or is_low_avg_rssi or is_no_alternatives:
                                is_coverage_extender = True

                if is_mesh:
                    uplink_rssi = device.get("uplink", {}).get("rssi")
                    results["mesh_aps_detected"].append(
                        {
                            "name": ap_name,
                            "is_mesh_child": is_mesh_child,
                            "is_mesh_parent": is_mesh_parent,
                            "mesh_children_count": mesh_children_count,
                            "uplink_rssi": uplink_rssi,
                            "is_coverage_extender": is_coverage_extender,
                            "weak_client_count": weak_client_count,
                            "total_client_count": mesh_client_count,
                            "avg_client_rssi": avg_client_rssi,
                            "clients_with_no_alternative": clients_with_no_alternative,
                        }
                    )

                for radio in radio_table:
                    radio_name = radio.get("radio", "unknown")
                    band = (
                        "2.4GHz" if radio_name == "ng" else "5GHz" if radio_name == "na" else "6GHz"
                    )

                    min_rssi_enabled = radio.get("min_rssi_enabled", False)
                    min_rssi_value = radio.get("min_rssi", None)

                    results["total_radios"] += 1

                    radio_info = {
                        "device": ap_name,
                        "device_id": ap_id,
                        "radio": radio_name,
                        "band": band,
                        "enabled": min_rssi_enabled,
                        "value": min_rssi_value,
                        "is_mesh": is_mesh,
                    }

                    if min_rssi_enabled:
                        results["enabled_count"] += 1
                        results["radios_with_min_rssi"].append(radio_info)

                        # **CRITICAL: Warn if mesh AP has min RSSI enabled**
                        # This can break the mesh uplink connection!
                        if is_mesh:
                            results["mesh_aps_with_min_rssi"].append(radio_info)
                            uplink_rssi = device.get("uplink", {}).get("rssi", "Unknown")

                            # Enhanced messaging for coverage extender nodes
                            if is_coverage_extender:
                                # Build detailed warning about clients
                                client_warning = []
                                if weak_client_count > 0:
                                    client_warning.append(
                                        f"{weak_client_count}/{mesh_client_count} clients with weak signal"
                                    )
                                if clients_with_no_alternative > 0:
                                    client_warning.append(
                                        f"{clients_with_no_alternative}/{mesh_client_count} clients with NO alternative AP"
                                    )

                                client_detail = (
                                    ", ".join(client_warning)
                                    if client_warning
                                    else f"{mesh_client_count} clients"
                                )

                                message = (
                                    f"🚨 COVERAGE EXTENDER {ap_name} {band} has min RSSI enabled ({min_rssi_value} dBm)\n"
                                    f"   This mesh node serves: {client_detail}"
                                )
                                recommendation = (
                                    f"DISABLE min RSSI immediately! This mesh node is extending coverage to a remote area "
                                    f"(avg client RSSI: {avg_client_rssi:.0f} dBm). Min RSSI will disconnect clients who have "
                                    f"NOWHERE ELSE TO GO and can break the mesh uplink (current: {uplink_rssi} dBm). "
                                    f"Coverage extension requires MAXIMUM connectivity tolerance."
                                )
                                priority = "critical"
                            else:
                                # Build role-specific warning
                                role_parts = []
                                if is_mesh_child:
                                    role_parts.append("MESH CHILD")
                                if is_mesh_parent:
                                    role_parts.append(
                                        f"MESH PARENT (has {mesh_children_count} mesh children)"
                                    )

                                role_text = " + ".join(role_parts) if role_parts else "MESH AP"

                                message = f"🚨 {role_text} {ap_name} {band} has min RSSI enabled ({min_rssi_value} dBm)"
                                if mesh_client_count > 0:
                                    message += f"\n   Serves {mesh_client_count} client{'s' if mesh_client_count != 1 else ''}"
                                    if clients_with_no_alternative > 0:
                                        message += f" ({clients_with_no_alternative} with NO alternative AP)"

                                # Build role-specific recommendation
                                danger_parts = []
                                if is_mesh_child:
                                    danger_parts.append(
                                        f"break THIS AP's wireless uplink (current: {uplink_rssi} dBm)"
                                    )
                                if is_mesh_parent:
                                    danger_parts.append(
                                        f"KICK OFF {mesh_children_count} mesh children connecting to this AP"
                                    )
                                if mesh_client_count > 0 and clients_with_no_alternative > 0:
                                    danger_parts.append(
                                        f"disconnect {clients_with_no_alternative} clients with NO alternative AP"
                                    )

                                danger_text = (
                                    ", ".join(danger_parts)
                                    if danger_parts
                                    else "break mesh connectivity"
                                )

                                recommendation = (
                                    f"DISABLE min RSSI immediately! This AP is critical to mesh network. "
                                    f"Min RSSI will {danger_text}. Mesh networks require MAXIMUM connectivity tolerance."
                                )
                                priority = "critical"

                            results["recommendations"].append(
                                {
                                    "type": "mesh_min_rssi_danger",
                                    "device": ap_name,
                                    "radio": radio_name,
                                    "band": band,
                                    "message": message,
                                    "recommendation": recommendation,
                                    "priority": priority,
                                    "current_value": min_rssi_value,
                                    "is_mesh": True,
                                    "is_coverage_extender": is_coverage_extender,
                                    "uplink_rssi": uplink_rssi,
                                    "weak_clients": weak_client_count,
                                    "total_clients": mesh_client_count,
                                }
                            )
                            continue  # Skip other recommendations for this radio

                        # Check if value is optimal (using adaptive thresholds)
                        if radio_name == "ng":
                            recommended = recommended_24
                        elif radio_name == "na":
                            recommended = recommended_5
                        else:  # 6GHz (radio_name == "6e" or similar)
                            recommended = recommended_6

                        if min_rssi_value and abs(min_rssi_value - recommended) > 10:
                            # Build recommendation message
                            rec_msg = f"Consider adjusting to {recommended} dBm"
                            if ios_count > 0 or strategy == "max_connectivity":
                                rec_msg += f" ({threshold_type} strategy"
                                if ios_count > 0:
                                    rec_msg += f" - {ios_count} iOS devices detected"
                                rec_msg += ")"

                            results["recommendations"].append(
                                {
                                    "type": "min_rssi_suboptimal",
                                    "device": ap_name,
                                    "radio": radio_name,
                                    "band": band,
                                    "message": f"{ap_name} {band} has min RSSI at {min_rssi_value} dBm",
                                    "recommendation": rec_msg,
                                    "priority": "low",
                                    "current_value": min_rssi_value,
                                    "recommended_value": recommended,
                                    "strategy": strategy,
                                }
                            )

                        # Warn if using aggressive threshold with iOS devices
                        elif (
                            strategy == "optimal"
                            and ios_percentage > 20
                            and min_rssi_value
                            and min_rssi_value >= -72
                        ):
                            results["recommendations"].append(
                                {
                                    "type": "min_rssi_ios_warning",
                                    "device": ap_name,
                                    "radio": radio_name,
                                    "band": band,
                                    "message": f"{ap_name} {band} has aggressive min RSSI ({min_rssi_value} dBm) with {ios_count} iOS devices",
                                    "recommendation": f"iOS devices may disconnect frequently at {min_rssi_value} dBm. Consider relaxing to {recommended} dBm to reduce disconnects.",
                                    "priority": "medium",
                                    "current_value": min_rssi_value,
                                    "recommended_value": recommended,
                                    "ios_friendly": True,
                                }
                            )
                    else:
                        results["disabled_count"] += 1
                        results["radios_without_min_rssi"].append(radio_info)

                        # **Skip recommendation to enable min RSSI on mesh APs**
                        # Mesh APs should NEVER have min RSSI enabled (child OR parent!)
                        if is_mesh:
                            # Good! Mesh AP has min RSSI disabled (as it should be)
                            # Add positive confirmation for coverage extenders or mesh parents
                            if is_coverage_extender or is_mesh_parent:
                                # Build role description
                                role_parts = []
                                if is_mesh_child:
                                    role_parts.append("MESH CHILD")
                                if is_mesh_parent:
                                    role_parts.append(
                                        f"MESH PARENT ({mesh_children_count} children)"
                                    )

                                role_text = " + ".join(role_parts) if role_parts else "MESH AP"

                                # Build detail about clients
                                client_detail = []
                                if weak_client_count > 0:
                                    client_detail.append(
                                        f"{weak_client_count}/{mesh_client_count} weak-signal clients"
                                    )
                                if clients_with_no_alternative > 0:
                                    client_detail.append(
                                        f"{clients_with_no_alternative} with NO alternative AP"
                                    )

                                detail_text = (
                                    ", ".join(client_detail)
                                    if client_detail
                                    else f"{mesh_client_count} clients"
                                )
                                avg_text = (
                                    f" (avg RSSI: {avg_client_rssi:.0f} dBm)"
                                    if avg_client_rssi
                                    else ""
                                )

                                # Build protection reason
                                protection_reason = []
                                if is_mesh_child:
                                    protection_reason.append("protect wireless uplink")
                                if is_mesh_parent:
                                    protection_reason.append(
                                        f"prevent kicking off {mesh_children_count} mesh children"
                                    )
                                if clients_with_no_alternative > 0:
                                    protection_reason.append(
                                        f"maintain connectivity for {clients_with_no_alternative} clients with no alternative AP"
                                    )

                                reason_text = (
                                    ", ".join(protection_reason)
                                    if protection_reason
                                    else "protect mesh connectivity"
                                )

                                display_type = (
                                    "COVERAGE EXTENDER" if is_coverage_extender else role_text
                                )

                                results["recommendations"].append(
                                    {
                                        "type": "mesh_config_good",
                                        "device": ap_name,
                                        "radio": radio_name,
                                        "band": band,
                                        "message": f"✅ {display_type} {ap_name} {band} correctly has min RSSI disabled",
                                        "recommendation": f"KEEP DISABLED! This AP serves {detail_text}{avg_text}. Min RSSI must remain disabled to {reason_text}.",
                                        "priority": "info",
                                        "is_mesh_child": is_mesh_child,
                                        "is_mesh_parent": is_mesh_parent,
                                        "mesh_children_count": mesh_children_count,
                                    }
                                )
                            continue

                        if radio_name == "ng":
                            recommended = recommended_24
                        elif radio_name == "na":
                            recommended = recommended_5
                        else:  # 6GHz
                            recommended = recommended_6

                        # Build recommendation message
                        rec_msg = f"Enable min RSSI at {recommended} dBm to improve roaming"
                        if ios_count > 0 or strategy == "max_connectivity":
                            rec_msg += f" ({threshold_type} strategy"
                            if ios_count > 0:
                                rec_msg += f" - optimized for {ios_count} iOS devices"
                            rec_msg += ")"

                        results["recommendations"].append(
                            {
                                "type": "min_rssi_disabled",
                                "device": ap_name,
                                "radio": radio_name,
                                "band": band,
                                "message": f"Min RSSI disabled on {ap_name} {band}",
                                "recommendation": rec_msg,
                                "priority": "medium",
                                "recommended_value": recommended,
                                "strategy": strategy,
                            }
                        )

            # Set overall severity
            if results["disabled_count"] > results["enabled_count"]:
                results["severity"] = "high"
            elif results["disabled_count"] > 0:
                results["severity"] = "medium"

        except Exception as e:
            results["error"] = str(e)

        return results

    def analyze_airtime_utilization(self, devices, lookback_hours=24):
        """
        Analyze airtime utilization per AP with time-series data

        High airtime = saturated AP even with few clients
        Identifies airtime hogs and overloaded APs

        Args:
            devices: List of device dicts
            lookback_hours: Hours of historical data to collect (default 24)
        """
        results = {
            "ap_utilization": {},
            "saturated_aps": [],
            "airtime_hogs": [],
            "recommendations": [],
            "severity": "ok",
            "time_series": {},  # New: historical airtime data
        }

        try:
            for device in devices:
                if device.get("type") != "uap":
                    continue

                ap_name = device.get("name", "Unnamed AP")
                ap_id = device.get("_id")

                # Get radio stats
                radio_table_stats = device.get("radio_table_stats", [])

                for radio_stat in radio_table_stats:
                    radio_name = radio_stat.get("name", "unknown")
                    radio = radio_stat.get("radio", "unknown")

                    # Determine band including 6GHz support
                    if radio == "ng":
                        band = "2.4GHz"
                    elif radio == "na":
                        band = "5GHz"
                    elif radio in ["6e", "ax", "6g"]:
                        band = "6GHz"
                    else:
                        band = "Unknown"

                    # Channel utilization
                    cu_total = radio_stat.get("cu_total", 0)
                    cu_self_tx = radio_stat.get("cu_self_tx", 0)
                    cu_self_rx = radio_stat.get("cu_self_rx", 0)

                    # Calculate airtime percentage
                    airtime_pct = cu_total

                    # Get channel information
                    channel = radio_stat.get("channel", device.get("channel", "Unknown"))

                    key = f"{ap_name} ({band})"
                    results["ap_utilization"][key] = {
                        "airtime_pct": airtime_pct,
                        "tx_pct": cu_self_tx,
                        "rx_pct": cu_self_rx,
                        "clients": radio_stat.get("num_sta", 0),
                        "channel": channel,
                        "ap_name": ap_name,
                        "band": band,
                    }

                    # Flag saturated APs (>70% airtime)
                    if airtime_pct > 70:
                        results["saturated_aps"].append(
                            {
                                "ap": ap_name,
                                "band": band,
                                "airtime": airtime_pct,
                                "clients": radio_stat.get("num_sta", 0),
                            }
                        )

                        results["severity"] = "high"
                        results["recommendations"].append(
                            {
                                "type": "airtime_saturation",
                                "device": ap_name,
                                "message": f"{ap_name} {band} at {airtime_pct}% airtime utilization",
                                "recommendation": "Add another AP or move clients to reduce load",
                                "priority": "high",
                                "airtime": airtime_pct,
                            }
                        )
                    elif airtime_pct > 50:
                        results["severity"] = (
                            "medium" if results["severity"] == "ok" else results["severity"]
                        )
                        results["recommendations"].append(
                            {
                                "type": "airtime_warning",
                                "device": ap_name,
                                "message": f"{ap_name} {band} at {airtime_pct}% airtime utilization",
                                "recommendation": "Monitor airtime, consider load balancing",
                                "priority": "medium",
                                "airtime": airtime_pct,
                            }
                        )

            # Collect historical airtime data for trending
            try:
                self._collect_airtime_history(devices, results, lookback_hours)
            except Exception as e:
                print(f"Warning: Could not collect airtime history: {e}")

        except Exception as e:
            results["error"] = str(e)

        return results

    def _collect_airtime_history(self, devices, results, lookback_hours):
        """
        Collect historical airtime data for time-series visualization

        Queries device stats at multiple time points to build trend data
        """
        # Get hourly stats for the lookback period
        # Note: UniFi API provides hourly aggregated data
        try:
            # Get the current timestamp in milliseconds
            end_time = int(datetime.now().timestamp() * 1000)
            start_time = int((datetime.now() - timedelta(hours=lookback_hours)).timestamp() * 1000)

            # For each AP, collect historical data
            for device in devices:
                if device.get("type") != "uap":
                    continue

                ap_name = device.get("name", "Unnamed AP")
                ap_mac = device.get("mac")

                if not ap_mac:
                    continue

                # Query hourly stats endpoint
                # API format: /api/s/{site}/stat/report/hourly.ap/{mac}
                # Build URL with query parameters
                hourly_stats = self.client.get(
                    f"s/{self.site}/stat/report/hourly.ap/{ap_mac}?start={start_time}&end={end_time}"
                )

                if not hourly_stats or "data" not in hourly_stats:
                    continue

                # Process time-series data
                # Support all radio types: 2.4GHz (ng), 5GHz (na), 6GHz (6e/ax/6g)
                for radio_idx, radio_stats in enumerate(["ng", "na", "6e"]):
                    if radio_stats == "ng":
                        band = "2.4GHz"
                    elif radio_stats == "na":
                        band = "5GHz"
                    elif radio_stats in ["6e", "ax", "6g"]:
                        band = "6GHz"
                    else:
                        band = "Unknown"

                    key = f"{ap_name} ({band})"

                    time_series_data = []
                    for stat in hourly_stats["data"]:
                        # Extract radio-specific data
                        radio_table = stat.get("radio_table_stats", [])
                        if radio_idx < len(radio_table):
                            radio_stat = radio_table[radio_idx]
                            time_series_data.append(
                                {
                                    "timestamp": stat.get("time", 0),
                                    "datetime": datetime.fromtimestamp(
                                        stat.get("time", 0) / 1000
                                    ).isoformat(),
                                    "airtime_pct": radio_stat.get("cu_total", 0),
                                    "tx_pct": radio_stat.get("cu_self_tx", 0),
                                    "rx_pct": radio_stat.get("cu_self_rx", 0),
                                    "clients": radio_stat.get("num_sta", 0),
                                }
                            )

                    if time_series_data:
                        results["time_series"][key] = time_series_data

        except Exception as e:
            # If hourly endpoint fails, create simulated historical data
            # based on current readings with some variation
            print(f"Could not fetch hourly stats (API may not support it): {e}")
            print(f"Creating simulated trend data based on current readings...")

            # For each AP with current utilization, create a trend
            for key, current_data in results["ap_utilization"].items():
                base_airtime = current_data["airtime_pct"]
                base_tx = current_data["tx_pct"]
                base_rx = current_data["rx_pct"]
                base_clients = current_data["clients"]

                # Create 24 hourly data points with variation
                import random

                time_series_data = []
                now = datetime.now()

                for hour in range(24):
                    timestamp = now - timedelta(hours=24 - hour)

                    # Add realistic variation (±10% of base value)
                    variation = random.uniform(-0.1, 0.1)
                    airtime = max(0, min(100, base_airtime + (base_airtime * variation)))
                    tx = max(0, min(100, base_tx + (base_tx * variation)))
                    rx = max(0, min(100, base_rx + (base_rx * variation)))
                    clients = max(0, int(base_clients + random.randint(-2, 2)))

                    time_series_data.append(
                        {
                            "timestamp": int(timestamp.timestamp() * 1000),
                            "datetime": timestamp.isoformat(),
                            "airtime_pct": round(airtime, 1),
                            "tx_pct": round(tx, 1),
                            "rx_pct": round(rx, 1),
                            "clients": clients,
                        }
                    )

                results["time_series"][key] = time_series_data

            results["time_series_note"] = (
                "Historical data simulated from current readings (API endpoint unavailable)"
            )

    def analyze_radio_performance(self, devices, clients=None):
        """
        Analyze radio performance metrics with focus on TX retry rates

        Critical for 6GHz networks where high retry rates (>15%) indicate:
        - Too-wide channels (320MHz → 160MHz)
        - LPI power limitations (recommend AFC/Standard Power)
        - Coverage/range issues

        Thresholds:
        - <5%: Excellent
        - 5-10%: Good
        - 10-15%: Warning
        - >15%: Critical (especially on 6GHz)

        Args:
            devices: List of AP devices with radio_table_stats
            clients: Optional list of clients for coverage analysis

        Returns:
            dict: Radio performance analysis with retry rate warnings
        """
        results = {
            "radios_analyzed": 0,
            "high_retry_radios": [],
            "excellent_radios": [],
            "recommendations": [],
            "severity": "ok",
            "retry_rate_distribution": {
                "excellent": 0,  # <5%
                "good": 0,  # 5-10%
                "warning": 0,  # 10-15%
                "critical": 0,  # >15%
            },
        }

        try:
            aps = [d for d in devices if d.get("type") == "uap"]

            for device in aps:
                ap_name = device.get("name", "Unnamed AP")
                ap_mac = device.get("mac")
                radio_table_stats = device.get("radio_table_stats", [])

                for radio_stat in radio_table_stats:
                    radio_name = radio_stat.get("radio", "unknown")
                    radio_label = radio_stat.get("name", "unknown")

                    # Determine band (including 6GHz support)
                    if radio_name == "ng":
                        band = "2.4GHz"
                    elif radio_name == "na":
                        band = "5GHz"
                    elif radio_name in ["6e", "ax", "6g"]:
                        band = "6GHz"
                    else:
                        band = "Unknown"

                    # Get retry rate
                    tx_retries_pct = radio_stat.get("tx_retries_pct", 0)
                    tx_packets = radio_stat.get("tx_packets", 0)
                    tx_retries = radio_stat.get("tx_retries", 0)
                    num_clients = radio_stat.get("num_sta", 0)
                    channel = radio_stat.get("channel", 0)
                    tx_power = radio_stat.get("tx_power", 0)

                    results["radios_analyzed"] += 1

                    # Classify retry rate
                    if tx_retries_pct < 5:
                        results["retry_rate_distribution"]["excellent"] += 1
                        results["excellent_radios"].append(
                            {"ap": ap_name, "band": band, "retry_pct": tx_retries_pct}
                        )
                    elif tx_retries_pct < 10:
                        results["retry_rate_distribution"]["good"] += 1
                    elif tx_retries_pct < 15:
                        results["retry_rate_distribution"]["warning"] += 1
                    else:
                        results["retry_rate_distribution"]["critical"] += 1

                    # Flag high retry rates (WARNING: >10%, CRITICAL: >15%)
                    if tx_retries_pct > 10:
                        # Get additional radio details for root cause analysis
                        radio_table = device.get("radio_table", [])
                        radio_config = next(
                            (r for r in radio_table if r.get("radio") == radio_name), {}
                        )

                        channel_width = radio_config.get("ht", 20)
                        power_mode = radio_config.get(
                            "ap_pwr_type", "Unknown"
                        )  # LPI/VLP/SP for 6GHz

                        # Build issue description
                        priority = "critical" if tx_retries_pct > 15 else "medium"

                        issue_data = {
                            "ap": ap_name,
                            "band": band,
                            "radio": radio_name,
                            "retry_pct": tx_retries_pct,
                            "tx_packets": tx_packets,
                            "tx_retries": tx_retries,
                            "clients": num_clients,
                            "channel": channel,
                            "channel_width": channel_width,
                            "tx_power": tx_power,
                            "priority": priority,
                        }

                        results["high_retry_radios"].append(issue_data)

                        # Update severity
                        if priority == "critical":
                            results["severity"] = "high"
                        elif priority == "medium" and results["severity"] == "ok":
                            results["severity"] = "medium"

                        # Generate recommendations based on band and root cause
                        if band == "6GHz":
                            # 6GHz-specific analysis
                            issue_data["power_mode"] = power_mode

                            message = (
                                f"🚨 {ap_name} {band} has HIGH TX retry rate: {tx_retries_pct:.1f}%"
                            )
                            if tx_retries_pct > 20:
                                message = f"🔴 {ap_name} {band} has CRITICAL TX retry rate: {tx_retries_pct:.1f}%"

                            # Build root cause analysis
                            causes = []
                            recommendations_list = []

                            # Check channel width
                            if channel_width >= 320:
                                causes.append(f"320MHz channel width requires very clean spectrum")
                                recommendations_list.append(
                                    "Try reducing to 160MHz for better reliability"
                                )
                            elif channel_width >= 160:
                                causes.append(f"160MHz channel width may be too aggressive")
                                recommendations_list.append("Consider 80MHz if retry rate persists")

                            # Check power mode
                            if power_mode == "LPI":
                                causes.append("LPI power mode limited to low power (+24dBm)")
                                recommendations_list.append(
                                    "Enable AFC to use Standard Power mode for better range"
                                )

                            # Check client count vs retry rate
                            if num_clients > 0:
                                # Get client RSSI data if available
                                if clients:
                                    ap_clients = [
                                        c
                                        for c in clients
                                        if c.get("ap_mac") == ap_mac
                                        and c.get("radio") == radio_name
                                    ]
                                    if ap_clients:
                                        avg_rssi = sum(c.get("rssi", 0) for c in ap_clients) / len(
                                            ap_clients
                                        )
                                        if avg_rssi < -70:
                                            causes.append(
                                                f"Weak client signals (avg RSSI: {avg_rssi:.0f} dBm)"
                                            )
                                            recommendations_list.append(
                                                "Clients may be at edge of 6GHz coverage - consider adding AP"
                                            )

                            if not causes:
                                causes.append("High interference or signal quality issues")
                                recommendations_list.append(
                                    "Check for interference sources, verify antenna connections"
                                )

                            recommendation = (
                                "ROOT CAUSE: "
                                + "; ".join(causes)
                                + ". "
                                + " | ".join(recommendations_list)
                            )

                            results["recommendations"].append(
                                {
                                    "type": "6ghz_high_retry",
                                    "device": ap_name,
                                    "band": band,
                                    "message": message,
                                    "recommendation": recommendation,
                                    "priority": priority,
                                    "retry_pct": tx_retries_pct,
                                    "channel_width": channel_width,
                                    "power_mode": power_mode,
                                    "clients": num_clients,
                                }
                            )

                        else:
                            # 2.4GHz / 5GHz analysis
                            message = f"⚠️ {ap_name} {band} has elevated TX retry rate: {tx_retries_pct:.1f}%"

                            causes = []
                            recommendations_list = []

                            if band == "2.4GHz":
                                causes.append("2.4GHz band congestion/interference likely")
                                recommendations_list.append(
                                    "Check for channel overlap with neighbors"
                                )
                                recommendations_list.append("Move clients to 5GHz/6GHz if possible")
                            else:  # 5GHz
                                if channel_width >= 80:
                                    causes.append(f"{channel_width}MHz width may cause contention")
                                    recommendations_list.append(
                                        "Consider 40MHz in dense environments"
                                    )
                                else:
                                    causes.append("Interference or signal quality issues")
                                    recommendations_list.append(
                                        "Check for DFS events, non-WiFi interference"
                                    )

                            recommendation = (
                                "LIKELY CAUSE: "
                                + "; ".join(causes)
                                + ". "
                                + " | ".join(recommendations_list)
                            )

                            results["recommendations"].append(
                                {
                                    "type": "high_retry_rate",
                                    "device": ap_name,
                                    "band": band,
                                    "message": message,
                                    "recommendation": recommendation,
                                    "priority": priority,
                                    "retry_pct": tx_retries_pct,
                                    "channel_width": channel_width,
                                    "clients": num_clients,
                                }
                            )

        except Exception as e:
            results["error"] = str(e)

        return results

    def analyze_6ghz_psc_channels(self, devices):
        """
        Analyze 6GHz channel usage and recommend PSC (Preferred Scanning Channels)

        PSC channels are scanned first by clients, leading to faster network discovery
        and connection times. Non-PSC channels may result in slower initial connections.

        PSC Channels (20MHz center frequencies):
        5, 21, 37, 53, 69, 85, 101, 117, 133, 149, 165, 181, 197

        For wider channels (40/80/160/320 MHz), the PRIMARY channel should be PSC.

        Args:
            devices: List of AP devices with radio_table configuration

        Returns:
            dict: PSC channel analysis with recommendations
        """
        # PSC channel list (20MHz spacing in 6GHz band)
        PSC_CHANNELS = [5, 21, 37, 53, 69, 85, 101, 117, 133, 149, 165, 181, 197]

        results = {
            "radios_6ghz": 0,
            "psc_compliant": 0,
            "non_psc": 0,
            "psc_radios": [],
            "non_psc_radios": [],
            "recommendations": [],
            "severity": "ok",
        }

        try:
            aps = [d for d in devices if d.get("type") == "uap"]

            for device in aps:
                ap_name = device.get("name", "Unnamed AP")
                radio_table = device.get("radio_table", [])

                for radio in radio_table:
                    radio_name = radio.get("radio", "unknown")

                    # Only analyze 6GHz radios
                    if radio_name not in ["6e", "ax", "6g"]:
                        continue

                    results["radios_6ghz"] += 1

                    channel = radio.get("channel")
                    channel_width = radio.get("ht", 20)
                    tx_power = radio.get("tx_power", "auto")

                    # For wider channels, check the primary channel
                    # In UniFi, 'channel' typically refers to the primary/control channel
                    primary_channel = channel

                    is_psc = primary_channel in PSC_CHANNELS

                    radio_info = {
                        "ap": ap_name,
                        "channel": channel,
                        "channel_width": channel_width,
                        "tx_power": tx_power,
                        "is_psc": is_psc,
                    }

                    if is_psc:
                        results["psc_compliant"] += 1
                        results["psc_radios"].append(radio_info)
                    else:
                        results["non_psc"] += 1
                        results["non_psc_radios"].append(radio_info)

                        # Generate recommendation
                        # Find nearest PSC channel
                        nearest_psc = min(PSC_CHANNELS, key=lambda x: abs(x - channel))

                        message = (
                            f"{ap_name} 6GHz on non-PSC channel {channel} "
                            f"({channel_width}MHz width)"
                        )

                        recommendation = (
                            f"Consider switching to PSC channel {nearest_psc} for faster "
                            f"client discovery. PSC channels are scanned first by WiFi 6E/7 clients, "
                            f"reducing connection time and improving user experience."
                        )

                        # Non-PSC is not critical, but recommended for optimal performance
                        priority = "low"

                        results["recommendations"].append(
                            {
                                "type": "non_psc_channel",
                                "device": ap_name,
                                "band": "6GHz",
                                "message": message,
                                "recommendation": recommendation,
                                "priority": priority,
                                "current_channel": channel,
                                "recommended_channel": nearest_psc,
                                "channel_width": channel_width,
                            }
                        )

                        # Update severity (low priority issue)
                        if results["severity"] == "ok":
                            results["severity"] = "info"

            # Summary message
            if results["radios_6ghz"] > 0:
                psc_pct = (results["psc_compliant"] / results["radios_6ghz"]) * 100
                results["psc_compliance_pct"] = round(psc_pct, 1)

                if psc_pct == 100:
                    results["summary"] = (
                        f"✅ All {results['radios_6ghz']} 6GHz radios using PSC channels "
                        f"for optimal client discovery"
                    )
                elif psc_pct >= 50:
                    results["summary"] = (
                        f"⚠️ {results['psc_compliant']}/{results['radios_6ghz']} 6GHz radios "
                        f"using PSC channels ({psc_pct:.0f}%)"
                    )
                else:
                    results["summary"] = (
                        f"🔴 Only {results['psc_compliant']}/{results['radios_6ghz']} 6GHz radios "
                        f"using PSC channels ({psc_pct:.0f}%)"
                    )
            else:
                results["summary"] = "No 6GHz radios detected"

        except Exception as e:
            results["error"] = str(e)

        return results

    def analyze_6ghz_power_modes(self, devices):
        """
        Analyze 6GHz power modes and recommend AFC/Standard Power where beneficial

        6GHz Power Modes (FCC):
        - LPI (Low Power Indoor): +24dBm EIRP, no AFC required, limited range
        - VLP (Very Low Power): +14dBm EIRP, portable devices, very limited range
        - SP (Standard Power): +36dBm EIRP, requires AFC, significantly better range

        AFC (Automated Frequency Coordination):
        - Enables Standard Power mode (+12dB more than LPI = ~4x range)
        - Free registration via FCC/regulatory body
        - Requires AP location coordinates
        - Dramatically improves coverage and reduces retry rates

        Args:
            devices: List of AP devices with radio_table configuration

        Returns:
            dict: Power mode analysis with AFC recommendations
        """
        results = {
            "radios_6ghz": 0,
            "lpi_mode": 0,
            "vlp_mode": 0,
            "sp_mode": 0,
            "unknown_mode": 0,
            "power_mode_details": [],
            "recommendations": [],
            "severity": "ok",
        }

        try:
            aps = [d for d in devices if d.get("type") == "uap"]

            for device in aps:
                ap_name = device.get("name", "Unnamed AP")
                ap_model = device.get("model", "Unknown")
                radio_table = device.get("radio_table", [])
                radio_table_stats = device.get("radio_table_stats", [])

                for radio in radio_table:
                    radio_name = radio.get("radio", "unknown")

                    # Only analyze 6GHz radios
                    if radio_name not in ["6e", "ax", "6g"]:
                        continue

                    results["radios_6ghz"] += 1

                    # Get power mode (UniFi field names may vary)
                    power_mode = radio.get(
                        "tx_power_mode",
                        radio.get("ap_pwr_type", radio.get("power_mode", "Unknown")),
                    )

                    channel = radio.get("channel")
                    channel_width = radio.get("ht", 20)
                    tx_power = radio.get("tx_power", "auto")

                    # Get stats for this radio if available
                    radio_stats = next(
                        (r for r in radio_table_stats if r.get("radio") == radio_name), {}
                    )

                    tx_retries_pct = radio_stats.get("tx_retries_pct", 0)
                    num_clients = radio_stats.get("num_sta", 0)

                    # Normalize power mode string
                    power_mode_normalized = str(power_mode).upper()

                    # Classify power mode
                    is_lpi = "LPI" in power_mode_normalized or power_mode_normalized == "LOW"
                    is_vlp = "VLP" in power_mode_normalized or power_mode_normalized == "VERY_LOW"
                    is_sp = "SP" in power_mode_normalized or "STANDARD" in power_mode_normalized

                    if is_lpi:
                        results["lpi_mode"] += 1
                        mode_label = "LPI"
                    elif is_vlp:
                        results["vlp_mode"] += 1
                        mode_label = "VLP"
                    elif is_sp:
                        results["sp_mode"] += 1
                        mode_label = "SP"
                    else:
                        results["unknown_mode"] += 1
                        mode_label = "Unknown"

                    radio_info = {
                        "ap": ap_name,
                        "model": ap_model,
                        "channel": channel,
                        "channel_width": channel_width,
                        "power_mode": mode_label,
                        "tx_power": tx_power,
                        "tx_retries_pct": tx_retries_pct,
                        "clients": num_clients,
                    }

                    results["power_mode_details"].append(radio_info)

                    # Generate recommendations for LPI mode with issues
                    if is_lpi or results["unknown_mode"] > 0:
                        # Check for indicators that SP would help
                        needs_sp = False
                        reasons = []

                        # Reason 1: High retry rate (coverage issue)
                        if tx_retries_pct > 15:
                            needs_sp = True
                            reasons.append(
                                f"High TX retry rate ({tx_retries_pct:.1f}%) indicates coverage issues"
                            )

                        # Reason 2: Wide channels + high retry (spectrum too aggressive for power)
                        if channel_width >= 160 and tx_retries_pct > 10:
                            needs_sp = True
                            reasons.append(
                                f"{channel_width}MHz width with elevated retries - insufficient power/range"
                            )

                        # Reason 3: Multiple clients with suboptimal performance
                        if num_clients >= 3 and tx_retries_pct > 12:
                            needs_sp = True
                            reasons.append(
                                f"{num_clients} clients with {tx_retries_pct:.1f}% retry rate - coverage limitation"
                            )

                        if needs_sp:
                            message = f"{ap_name} 6GHz using {mode_label} power mode (+24dBm limit)"

                            recommendation = (
                                f"Enable AFC and Standard Power mode for +12dB gain (+36dBm EIRP). "
                                f"This provides ~4x effective range and will significantly reduce retry rate. "
                                f"REASON: {'; '.join(reasons)}. "
                                f"AFC registration is FREE via FCC database - requires AP coordinates."
                            )

                            # Priority based on severity
                            if tx_retries_pct > 20:
                                priority = "high"
                            elif tx_retries_pct > 15:
                                priority = "medium"
                            else:
                                priority = "low"

                            results["recommendations"].append(
                                {
                                    "type": "6ghz_power_mode_upgrade",
                                    "device": ap_name,
                                    "band": "6GHz",
                                    "message": message,
                                    "recommendation": recommendation,
                                    "priority": priority,
                                    "current_mode": mode_label,
                                    "recommended_mode": "SP (Standard Power)",
                                    "power_gain_db": 12,
                                    "range_multiplier": "~4x",
                                    "retry_pct": tx_retries_pct,
                                    "channel_width": channel_width,
                                    "clients": num_clients,
                                }
                            )

                            # Update severity
                            if priority == "high":
                                results["severity"] = "high"
                            elif priority == "medium" and results["severity"] != "high":
                                results["severity"] = "medium"

                    # Warn about VLP mode (even more limited)
                    if is_vlp:
                        message = f"{ap_name} 6GHz using VLP mode (+14dBm - severely limited)"
                        recommendation = (
                            "VLP mode is designed for portable devices, not fixed APs. "
                            "Switch to LPI (+24dBm) minimum, or SP (+36dBm) with AFC for best performance."
                        )

                        results["recommendations"].append(
                            {
                                "type": "6ghz_vlp_warning",
                                "device": ap_name,
                                "message": message,
                                "recommendation": recommendation,
                                "priority": "high",
                                "current_mode": "VLP",
                                "recommended_mode": "SP or LPI",
                            }
                        )

                        results["severity"] = "high"

            # Generate summary
            if results["radios_6ghz"] > 0:
                total = results["radios_6ghz"]
                sp_pct = (results["sp_mode"] / total) * 100
                lpi_pct = (results["lpi_mode"] / total) * 100

                if results["sp_mode"] == total:
                    results["summary"] = (
                        f"✅ All {total} 6GHz radios using Standard Power (optimal)"
                    )
                elif results["sp_mode"] > 0:
                    results["summary"] = (
                        f"⚠️ {results['sp_mode']}/{total} 6GHz radios using Standard Power "
                        f"({sp_pct:.0f}%), {results['lpi_mode']} on LPI"
                    )
                elif results["lpi_mode"] == total:
                    results["summary"] = (
                        f"⚠️ All {total} 6GHz radios on LPI mode (+24dBm limited). "
                        f"Consider AFC for Standard Power (+36dBm)"
                    )
                else:
                    results["summary"] = (
                        f"📊 6GHz Power Modes: {results['lpi_mode']} LPI, "
                        f"{results['vlp_mode']} VLP, {results['sp_mode']} SP"
                    )
            else:
                results["summary"] = "No 6GHz radios detected"

        except Exception as e:
            results["error"] = str(e)

        return results

    def analyze_client_capabilities(self, clients, devices=None):
        """
        Analyze client device capabilities

        Identifies:
        - 802.11 standard support (ac, ax, etc)
        - Channel width capability
        - Spatial streams
        - Problem devices holding back network

        Args:
            clients: List of client dicts
            devices: Optional list of device dicts for AP name lookup
        """
        # Build AP MAC to name mapping
        ap_map = {}
        if devices:
            for device in devices:
                if device.get("type") == "uap":
                    ap_map[device.get("mac")] = device.get("name", "Unknown")

        results = {
            "capability_distribution": {"802.11ax": 0, "802.11ac": 0, "802.11n": 0, "legacy": 0},
            "channel_width": {"20MHz": 0, "40MHz": 0, "80MHz": 0, "160MHz": 0},
            "spatial_streams": {"1x1": 0, "2x2": 0, "3x3": 0, "4x4": 0},
            "problem_devices": [],
            "recommendations": [],
            "severity": "ok",
        }

        try:
            for client in clients:
                if client.get("is_wired"):
                    continue

                # Determine 802.11 standard
                radio_proto = client.get("radio_proto", "").lower()

                if "ax" in radio_proto or client.get("is_11ax"):
                    results["capability_distribution"]["802.11ax"] += 1
                elif "ac" in radio_proto or client.get("is_11ac"):
                    results["capability_distribution"]["802.11ac"] += 1
                elif "n" in radio_proto or client.get("is_11n"):
                    results["capability_distribution"]["802.11n"] += 1
                else:
                    results["capability_distribution"]["legacy"] += 1

                    # Flag legacy devices with AP information
                    ap_mac = client.get("ap_mac", "")
                    ap_name = ap_map.get(ap_mac, "Unknown") if ap_map else "Unknown"

                    results["problem_devices"].append(
                        {
                            "hostname": client.get("hostname", client.get("name", "Unknown")),
                            "mac": client.get("mac"),
                            "ap_mac": ap_mac,
                            "ap_name": ap_name,
                            "rssi": client.get("rssi", 0),
                            "radio_proto": radio_proto,
                            "issue": "Legacy 802.11a/b/g device",
                        }
                    )

                # Channel width - check both possible field names
                channel_width = client.get("channel_width") or client.get("channelWidth", 20)
                if channel_width >= 160:
                    results["channel_width"]["160MHz"] += 1
                elif channel_width >= 80:
                    results["channel_width"]["80MHz"] += 1
                elif channel_width >= 40:
                    results["channel_width"]["40MHz"] += 1
                else:
                    results["channel_width"]["20MHz"] += 1

                # Spatial streams (NSS)
                nss = client.get("nss", 1)
                if nss >= 4:
                    results["spatial_streams"]["4x4"] += 1
                elif nss >= 3:
                    results["spatial_streams"]["3x3"] += 1
                elif nss >= 2:
                    results["spatial_streams"]["2x2"] += 1
                else:
                    results["spatial_streams"]["1x1"] += 1

            # Generate recommendations
            legacy_count = results["capability_distribution"]["legacy"]
            if legacy_count > 0:
                results["severity"] = "medium"
                results["recommendations"].append(
                    {
                        "type": "legacy_devices",
                        "message": f"{legacy_count} legacy 802.11a/b/g devices detected",
                        "recommendation": "Consider upgrading or isolating legacy devices to prevent network slowdown",
                        "priority": "medium",
                        "affected_count": legacy_count,
                    }
                )

        except Exception as e:
            results["error"] = str(e)

        return results

    def analyze_client_security(self, clients):
        """
        Analyze client security status including isolation/blocking

        Returns:
            dict: Security analysis with isolated/blocked clients
        """
        results = {
            "isolated_clients": [],
            "blocked_clients": [],
            "guest_clients": [],
            "severity": "ok",
            "recommendations": [],
        }

        try:
            for client in clients:
                # Check for blocked clients (various field names used)
                is_blocked = (
                    client.get("blocked", False)
                    or client.get("is_blocked", False)
                    or client.get("blocked_by_dpi", False)
                )

                # Check for isolated clients
                is_guest = client.get("is_guest", False)
                use_fixedip = client.get("use_fixedip", False)
                note = client.get("note", "").lower()

                # Client in isolation if marked as guest or has isolation in note
                is_isolated = is_guest or "isolat" in note or "quarantine" in note

                hostname = client.get("hostname", client.get("name", "Unknown"))
                mac = client.get("mac", "Unknown")

                if is_blocked:
                    results["blocked_clients"].append(
                        {
                            "hostname": hostname,
                            "mac": mac,
                            "reason": note if note else "Unknown",
                            "is_wired": client.get("is_wired", False),
                        }
                    )

                if is_isolated and not is_blocked:
                    results["isolated_clients"].append(
                        {
                            "hostname": hostname,
                            "mac": mac,
                            "is_guest": is_guest,
                            "network": client.get("network", "Unknown"),
                            "vlan": client.get("vlan", "Unknown"),
                            "note": note if note else "Guest network",
                        }
                    )

                if is_guest:
                    results["guest_clients"].append(
                        {
                            "hostname": hostname,
                            "mac": mac,
                            "network": client.get("network", "Unknown"),
                        }
                    )

            # Determine severity
            blocked_count = len(results["blocked_clients"])
            isolated_count = len(results["isolated_clients"])

            if blocked_count > 0:
                results["severity"] = "high"
                results["recommendations"].append(
                    {
                        "type": "blocked_clients_critical",
                        "message": f"{blocked_count} client(s) are blocked - possible security threat",
                        "recommendation": "Review blocked clients immediately. These may be security threats or legitimate devices incorrectly blocked.",
                        "priority": "critical",
                        "affected_count": blocked_count,
                    }
                )

            if isolated_count > 5:
                results["severity"] = "high" if results["severity"] == "ok" else results["severity"]
                results["recommendations"].append(
                    {
                        "type": "high_isolation",
                        "message": f"{isolated_count} client(s) in isolation/guest network",
                        "recommendation": "Verify isolation settings are intentional. Consider moving trusted devices to main network.",
                        "priority": "medium",
                        "affected_count": isolated_count,
                    }
                )

        except Exception as e:
            results["error"] = str(e)

        return results

    def calculate_network_health_score(self, analysis_data):
        """
        Calculate overall network health score (0-100)

        Factors:
        - Average client RSSI (30%)
        - Channel utilization / airtime (20%)
        - Client distribution (20%)
        - Mesh reliability (15%)
        - Disconnect/roaming issues (15%)

        Returns None if critical data is missing due to API errors
        """
        # Check if we have incomplete data from API errors
        api_errors = analysis_data.get("api_errors")
        if api_errors:
            critical_errors = api_errors.get("critical_errors", [])
            if critical_errors:
                # Don't calculate score if we have auth/permission errors
                return {
                    "score": None,
                    "grade": "N/A",
                    "summary": "Unable to calculate due to incomplete data",
                    "incomplete_data": True,
                    "reason": "Critical API errors (authentication/permissions) prevented data collection",
                }

            # Check if critical endpoints failed
            failed_endpoints = api_errors.get("failed_endpoints", [])
            critical_endpoint_patterns = ["stat/device", "stat/sta", "stat/health"]
            has_critical_failure = any(
                any(pattern in endpoint for pattern in critical_endpoint_patterns)
                for endpoint in failed_endpoints
            )

            if has_critical_failure:
                return {
                    "score": None,
                    "grade": "N/A",
                    "summary": "Unable to calculate due to missing critical data",
                    "incomplete_data": True,
                    "reason": "Failed to retrieve devices, clients, or health metrics",
                }

        score = 100
        details = {}

        try:
            # Factor 1: Average Client RSSI (30 points)
            signal_dist = analysis_data.get("signal_distribution", {})
            total_clients = sum(
                [
                    signal_dist.get("excellent", 0),
                    signal_dist.get("good", 0),
                    signal_dist.get("fair", 0),
                    signal_dist.get("poor", 0),
                    signal_dist.get("very_poor", 0),
                ]
            )

            if total_clients > 0:
                rssi_score = (
                    (
                        signal_dist.get("excellent", 0) * 1.0
                        + signal_dist.get("good", 0) * 0.8
                        + signal_dist.get("fair", 0) * 0.6
                        + signal_dist.get("poor", 0) * 0.3
                        + signal_dist.get("very_poor", 0) * 0.0
                    )
                    / total_clients
                    * 30
                )
            else:
                rssi_score = 30

            details["rssi_score"] = round(rssi_score, 1)

            # Factor 2: Airtime Utilization (20 points)
            airtime_data = analysis_data.get("airtime_analysis", {})
            saturated_count = len(airtime_data.get("saturated_aps", []))
            total_aps = len(airtime_data.get("ap_utilization", {}))

            if total_aps > 0:
                airtime_score = max(0, 20 - (saturated_count / total_aps * 20))
            else:
                airtime_score = 20

            details["airtime_score"] = round(airtime_score, 1)

            # Factor 3: Client Distribution (20 points)
            # Penalize if clients are unevenly distributed
            client_health = analysis_data.get("client_health", {})
            # Simplified: assume good distribution if no major imbalance detected
            distribution_score = 20  # Full points for balanced distribution
            details["distribution_score"] = distribution_score

            # Factor 4: Mesh Reliability (15 points)
            mesh_analysis = analysis_data.get("mesh_analysis", {})
            mesh_issues = len(mesh_analysis.get("findings", []))
            mesh_score = max(0, 15 - (mesh_issues * 3))
            details["mesh_score"] = mesh_score

            # Factor 5: DFS/Roaming Issues (15 points)
            dfs_data = analysis_data.get("dfs_analysis", {})
            band_steering = analysis_data.get("band_steering_analysis", {})

            issues_score = 15
            if dfs_data.get("severity") == "high":
                issues_score -= 10
            elif dfs_data.get("severity") == "medium":
                issues_score -= 5

            if band_steering.get("severity") == "high":
                issues_score -= 5

            details["issues_score"] = max(0, issues_score)

            # Calculate total
            total_score = (
                rssi_score
                + airtime_score
                + distribution_score
                + mesh_score
                + details["issues_score"]
            )

            score = round(total_score)

        except Exception as e:
            details["error"] = str(e)

        # Determine grade
        if score >= 90:
            grade = "A"
            status = "Excellent"
        elif score >= 80:
            grade = "B"
            status = "Good"
        elif score >= 70:
            grade = "C"
            status = "Fair"
        elif score >= 60:
            grade = "D"
            status = "Poor"
        else:
            grade = "F"
            status = "Critical"

        return {"score": score, "grade": grade, "status": status, "details": details}

    def analyze_firmware_consistency(self, devices):
        """
        Analyze firmware version consistency across APs

        Detects:
        - Mixed firmware versions within same model
        - Outdated firmware (based on newest version present)
        - Different firmware across different models

        Returns recommendations for uniform deployment
        """
        results = {
            "total_aps": 0,
            "firmware_by_model": {},
            "inconsistencies": [],
            "recommendations": [],
            "severity": "ok",
        }

        # Collect firmware versions by model
        for device in devices:
            if device.get("type") not in ["uap", "ubb"]:
                continue

            model = device.get("model", "Unknown")
            firmware = device.get("version", "Unknown")
            name = device.get("name", device.get("mac", "Unknown"))

            results["total_aps"] += 1

            if model not in results["firmware_by_model"]:
                results["firmware_by_model"][model] = {"versions": {}, "aps": []}

            if firmware not in results["firmware_by_model"][model]["versions"]:
                results["firmware_by_model"][model]["versions"][firmware] = []

            results["firmware_by_model"][model]["versions"][firmware].append(name)
            results["firmware_by_model"][model]["aps"].append({"name": name, "firmware": firmware})

        # Analyze each model for inconsistencies
        for model, data in results["firmware_by_model"].items():
            versions = data["versions"]

            if len(versions) > 1:
                # Mixed firmware versions detected
                version_list = list(versions.keys())
                version_list.sort(reverse=True)  # Newest first

                newest_version = version_list[0]
                outdated_count = sum(
                    len(aps) for ver, aps in versions.items() if ver != newest_version
                )

                inconsistency = {
                    "model": model,
                    "issue": "mixed_versions",
                    "versions_found": version_list,
                    "newest_version": newest_version,
                    "outdated_count": outdated_count,
                    "total_count": len(data["aps"]),
                    "details": {},
                }

                # Detail which APs have which versions
                for version, ap_list in versions.items():
                    inconsistency["details"][version] = {
                        "aps": ap_list,
                        "count": len(ap_list),
                        "status": "current" if version == newest_version else "outdated",
                    }

                results["inconsistencies"].append(inconsistency)

                # Generate recommendation
                severity = "high" if outdated_count > 1 else "medium"
                results["recommendations"].append(
                    {
                        "type": "firmware_upgrade",
                        "severity": severity,
                        "model": model,
                        "action": f"Upgrade {outdated_count} AP(s) to version {newest_version}",
                        "current_state": f"{outdated_count}/{len(data['aps'])} APs on older firmware",
                        "target_state": f"All {len(data['aps'])} APs on {newest_version}",
                        "aps_to_upgrade": [
                            {"name": ap, "current_version": ver}
                            for ver, aps in versions.items()
                            if ver != newest_version
                            for ap in aps
                        ],
                        "rationale": [
                            "Uniform firmware improves stability",
                            "Prevents subtle compatibility issues",
                            "Ensures consistent feature set",
                            "Simplifies troubleshooting",
                        ],
                    }
                )

        # Determine overall severity
        if results["inconsistencies"]:
            high_severity_count = sum(
                1 for r in results["recommendations"] if r.get("severity") == "high"
            )
            medium_severity_count = sum(
                1 for r in results["recommendations"] if r.get("severity") == "medium"
            )

            if high_severity_count > 0 or medium_severity_count > 0:
                results["severity"] = "warning"
            else:
                results["severity"] = "info"

        # Add WiFi 6E/7 feature compatibility check
        wifi6e_models = ["U6-Enterprise", "U6-Mesh", "U6-Extender", "U7-Pro"]
        wifi7_models = ["U7-Pro"]

        for model, data in results["firmware_by_model"].items():
            if any(w6e in model for w6e in wifi6e_models):
                for version in data["versions"].keys():
                    # Check if firmware is old enough to lack 6GHz features
                    # Version format: X.Y.Z.buildnum
                    try:
                        major_minor = ".".join(version.split(".")[:2])
                        major, minor = map(int, major_minor.split("."))

                        # Example: Versions < 6.0 may lack full 6GHz support
                        if major < 6:
                            results["recommendations"].append(
                                {
                                    "type": "feature_compatibility",
                                    "severity": "high",
                                    "model": model,
                                    "action": f"Upgrade firmware to enable full 6GHz features",
                                    "current_state": f"Firmware {version} may lack WiFi 6E optimizations",
                                    "target_state": "Firmware 6.0+ for full 6GHz support",
                                    "rationale": [
                                        "Enables PSC channel optimization",
                                        "Unlocks AFC/Standard Power modes",
                                        "Improves 6GHz client compatibility",
                                        "Required for MLO (WiFi 7) if applicable",
                                    ],
                                }
                            )
                    except (ValueError, IndexError):
                        # Can't parse version number, skip
                        pass

        return results


def run_advanced_analysis(
    client, site="default", devices=None, clients=None, lookback_days=3, min_rssi_strategy="optimal"
):
    """
    Run all advanced analysis features

    Returns comprehensive analysis results
    """
    analyzer = AdvancedNetworkAnalyzer(client, site)

    # Get devices and clients if not provided
    if devices is None:
        response = client.get(f"s/{site}/stat/device")
        devices = response.get("data", []) if response else []
    if clients is None:
        response = client.get(f"s/{site}/stat/sta")
        clients = response.get("data", []) if response else []

    # Run switch analysis
    from core.switch_analyzer import SwitchAnalyzer

    switch_analyzer = SwitchAnalyzer(client, site)

    # Collect switch port packet loss history FIRST (7 days) - this populates cache for inline graphs
    switch_port_history = switch_analyzer.analyze_switch_port_history(lookback_hours=168)

    # Then analyze current switch state (uses cached data for inline 24h graphs)
    switch_analysis = switch_analyzer.analyze_switches()

    results = {
        "dfs_analysis": analyzer.analyze_dfs_events(lookback_days),
        "band_steering_analysis": analyzer.analyze_band_steering(devices, clients),
        "mesh_necessity_analysis": analyzer.analyze_mesh_necessity(devices),
        "min_rssi_analysis": analyzer.analyze_min_rssi(devices, clients, min_rssi_strategy),
        "fast_roaming_analysis": analyzer.analyze_fast_roaming(devices),
        "airtime_analysis": analyzer.analyze_airtime_utilization(devices),
        "radio_performance_analysis": analyzer.analyze_radio_performance(devices, clients),
        "psc_channel_analysis": analyzer.analyze_6ghz_psc_channels(devices),
        "power_mode_analysis": analyzer.analyze_6ghz_power_modes(devices),
        "firmware_analysis": analyzer.analyze_firmware_consistency(devices),
        "client_capabilities": analyzer.analyze_client_capabilities(clients, devices),
        "client_security": analyzer.analyze_client_security(clients),
        "switch_analysis": switch_analysis,
        "switch_port_history": switch_port_history,
    }

    # Calculate overall health score
    # Note: Need to pass full analysis data including signal distribution
    # This will be integrated with main analysis

    return results
