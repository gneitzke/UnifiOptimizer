"""
Client RSSI Tracking and Analysis Module

This module tracks client RSSI history and roaming patterns to inform
intelligent Min RSSI recommendations based on actual client behavior.

Key Features:
- Tracks client RSSI over configurable lookback period
- Analyzes roaming triggers (what RSSI caused clients to hop APs)
- Identifies manufacturer-specific behaviors (e.g., iPhone sensitivity)
- Provides strategy-based recommendations (optimal vs max connectivity)
"""

from typing import Dict, List, Optional, Tuple

from rich.console import Console

console = Console()


class ClientRSSITracker:
    """
    Tracks and analyzes client RSSI history and roaming patterns
    """

    def __init__(self, client, site):
        """
        Initialize the client RSSI tracker

        Args:
            client: UniFi API client instance
            site: Site name
        """
        self.client = client
        self.site = site

        # Manufacturer OUI patterns for identification
        self.MANUFACTURER_PATTERNS = {
            "Apple": ["apple", "iphone", "ipad"],  # Known for fragile roaming
            "Samsung": ["samsung"],
            "Google": ["google", "pixel"],
            "Microsoft": ["microsoft", "surface"],
            "Dell": ["dell"],
            "HP": ["hp", "hewlett"],
            "Lenovo": ["lenovo"],
        }

        # Strategy definitions
        self.STRATEGIES = {
            "optimal": {
                "description": "Aggressive roaming for optimal performance",
                "base_24ghz": -75,
                "base_5ghz": -72,
                "base_6ghz": -70,
                "ios_penalty": 0,  # No adjustment for iOS in optimal mode
            },
            "max_connectivity": {
                "description": "Conservative roaming for maximum connectivity",
                "base_24ghz": -80,
                "base_5ghz": -77,
                "base_6ghz": -75,
                "ios_penalty": -3,  # Additional 3 dBm tolerance for iOS
            },
        }

    def identify_manufacturer(self, client_data: Dict) -> Optional[str]:
        """
        Identify client device manufacturer from OUI and hostname

        Args:
            client_data: Client data dictionary

        Returns:
            Manufacturer name or None
        """
        oui = client_data.get("oui", "").lower()
        hostname = client_data.get("hostname", "").lower()
        name = client_data.get("name", "").lower()

        for manufacturer, patterns in self.MANUFACTURER_PATTERNS.items():
            for pattern in patterns:
                if pattern in oui or pattern in hostname or pattern in name:
                    return manufacturer

        return None

    def fetch_client_history(self, mac: str, lookback_hours: int = 168) -> List[Dict]:
        """
        Fetch historical RSSI data for a specific client

        Args:
            mac: Client MAC address
            lookback_hours: Hours to look back (default 7 days)

        Returns:
            List of historical data points with RSSI, AP, timestamp
        """
        from datetime import datetime, timedelta

        end_time = int(datetime.now().timestamp() * 1000)
        start_time = int((datetime.now() - timedelta(hours=lookback_hours)).timestamp() * 1000)

        try:
            # Fetch hourly client stats
            hourly_stats = self.client.get(
                f"s/{self.site}/stat/report/hourly.client/{mac}?start={start_time}&end={end_time}"
            )

            if not hourly_stats or "data" not in hourly_stats:
                return []

            history = []
            for stat in hourly_stats["data"]:
                timestamp = stat.get("time", 0)
                rssi = stat.get("signal", None)
                ap_mac = stat.get("ap_mac", None)
                ap_name = stat.get("ap_name", None)
                channel = stat.get("channel", None)
                radio = stat.get("radio", None)

                if rssi and ap_mac:
                    history.append(
                        {
                            "timestamp": timestamp,
                            "datetime": datetime.fromtimestamp(timestamp / 1000).isoformat(),
                            "rssi": rssi,
                            "ap_mac": ap_mac,
                            "ap_name": ap_name or "Unknown",
                            "channel": channel,
                            "radio": radio,
                        }
                    )

            return sorted(history, key=lambda x: x["timestamp"])

        except Exception as e:
            console.print(f"[dim red]Could not fetch client history for {mac}: {e}[/dim red]")
            return []

    def detect_roaming_events(self, history: List[Dict]) -> List[Dict]:
        """
        Detect roaming events (AP hops) and capture the RSSI at hop time

        Args:
            history: Client history list from fetch_client_history

        Returns:
            List of roaming events with RSSI before and after hop
        """
        if not history or len(history) < 2:
            return []

        roaming_events = []
        prev_entry = history[0]

        for entry in history[1:]:
            # Detect AP change
            if entry["ap_mac"] != prev_entry["ap_mac"]:
                roaming_events.append(
                    {
                        "timestamp": entry["timestamp"],
                        "datetime": entry["datetime"],
                        "from_ap": prev_entry["ap_name"],
                        "from_ap_mac": prev_entry["ap_mac"],
                        "to_ap": entry["ap_name"],
                        "to_ap_mac": entry["ap_mac"],
                        "rssi_before_hop": prev_entry["rssi"],
                        "rssi_after_hop": entry["rssi"],
                        "rssi_gain": entry["rssi"] - prev_entry["rssi"],
                        "from_channel": prev_entry.get("channel"),
                        "to_channel": entry.get("channel"),
                        "from_radio": prev_entry.get("radio"),
                        "to_radio": entry.get("radio"),
                    }
                )

            prev_entry = entry

        return roaming_events

    def analyze_roaming_triggers(self, roaming_events: List[Dict]) -> Dict:
        """
        Analyze roaming events to determine typical RSSI trigger points

        Args:
            roaming_events: List of roaming events from detect_roaming_events

        Returns:
            Statistics about roaming behavior
        """
        if not roaming_events:
            return {
                "roam_count": 0,
                "avg_trigger_rssi": None,
                "min_trigger_rssi": None,
                "max_trigger_rssi": None,
                "roam_too_late_count": 0,  # Roamed at very weak signal (<-80)
                "roam_too_early_count": 0,  # Roamed at strong signal (>-65)
                "roam_optimal_count": 0,  # Roamed at good signal (-65 to -80)
            }

        trigger_rssi_values = [event["rssi_before_hop"] for event in roaming_events]

        # Categorize roaming behavior
        roam_too_late = len([r for r in trigger_rssi_values if r < -80])
        roam_too_early = len([r for r in trigger_rssi_values if r > -65])
        roam_optimal = len([r for r in trigger_rssi_values if -80 <= r <= -65])

        return {
            "roam_count": len(roaming_events),
            "avg_trigger_rssi": (
                sum(trigger_rssi_values) / len(trigger_rssi_values) if trigger_rssi_values else None
            ),
            "min_trigger_rssi": min(trigger_rssi_values) if trigger_rssi_values else None,
            "max_trigger_rssi": max(trigger_rssi_values) if trigger_rssi_values else None,
            "roam_too_late_count": roam_too_late,
            "roam_too_early_count": roam_too_early,
            "roam_optimal_count": roam_optimal,
            "roam_too_late_pct": (
                (roam_too_late / len(roaming_events) * 100) if roaming_events else 0
            ),
        }

    def analyze_client_rssi_patterns(self, clients: List[Dict], lookback_hours: int = 168) -> Dict:
        """
        Analyze RSSI patterns for all non-mesh clients

        Args:
            clients: List of client dictionaries
            lookback_hours: Hours to analyze (default 7 days)

        Returns:
            Comprehensive analysis of client RSSI behavior
        """
        console.print(
            f"\n[cyan]📊 Analyzing client RSSI history ({lookback_hours} hours)...[/cyan]"
        )

        results = {
            "clients_analyzed": 0,
            "clients_with_roaming": 0,
            "total_roaming_events": 0,
            "manufacturer_stats": {},
            "roaming_trigger_distribution": {
                "too_late": 0,  # <-80 dBm
                "optimal": 0,  # -65 to -80 dBm
                "too_early": 0,  # >-65 dBm
            },
            "client_details": [],
            "avg_trigger_rssi_all": None,
        }

        all_trigger_rssi = []

        # Analyze each non-mesh client
        for idx, client in enumerate(clients):
            # Skip mesh APs (they're not typical clients)
            hostname = client.get("hostname", "").lower()
            name = client.get("name", "").lower()
            if "mesh" in hostname or "mesh" in name or client.get("is_wired", False):
                continue

            mac = client.get("mac")
            if not mac:
                continue

            console.print(
                f"  [dim]Analyzing client {idx + 1}/{len(clients)}: {client.get('hostname', mac)}[/dim]"
            )

            # Fetch historical data
            history = self.fetch_client_history(mac, lookback_hours)
            if not history:
                continue

            results["clients_analyzed"] += 1

            # Detect roaming events
            roaming_events = self.detect_roaming_events(history)
            if roaming_events:
                results["clients_with_roaming"] += 1
                results["total_roaming_events"] += len(roaming_events)

            # Analyze roaming triggers
            roaming_stats = self.analyze_roaming_triggers(roaming_events)

            # Categorize by manufacturer
            manufacturer = self.identify_manufacturer(client)
            if manufacturer:
                if manufacturer not in results["manufacturer_stats"]:
                    results["manufacturer_stats"][manufacturer] = {
                        "client_count": 0,
                        "roam_count": 0,
                        "avg_trigger_rssi": None,
                        "trigger_rssi_values": [],
                    }

                mfg_stats = results["manufacturer_stats"][manufacturer]
                mfg_stats["client_count"] += 1
                mfg_stats["roam_count"] += roaming_stats["roam_count"]
                if roaming_stats["avg_trigger_rssi"]:
                    mfg_stats["trigger_rssi_values"].append(roaming_stats["avg_trigger_rssi"])

            # Track distribution
            results["roaming_trigger_distribution"]["too_late"] += roaming_stats[
                "roam_too_late_count"
            ]
            results["roaming_trigger_distribution"]["optimal"] += roaming_stats[
                "roam_optimal_count"
            ]
            results["roaming_trigger_distribution"]["too_early"] += roaming_stats[
                "roam_too_early_count"
            ]

            # Collect all trigger RSSI values
            if roaming_stats["avg_trigger_rssi"]:
                all_trigger_rssi.append(roaming_stats["avg_trigger_rssi"])

            # Store client detail
            rssi_values = [h["rssi"] for h in history]
            results["client_details"].append(
                {
                    "mac": mac,
                    "hostname": client.get("hostname", "Unknown"),
                    "manufacturer": manufacturer,
                    "roam_count": roaming_stats["roam_count"],
                    "avg_trigger_rssi": roaming_stats["avg_trigger_rssi"],
                    "min_rssi": min(rssi_values) if rssi_values else None,
                    "max_rssi": max(rssi_values) if rssi_values else None,
                    "avg_rssi": (sum(rssi_values) / len(rssi_values) if rssi_values else None),
                    "roaming_behavior": {
                        "too_late_pct": roaming_stats["roam_too_late_pct"],
                        "optimal_count": roaming_stats["roam_optimal_count"],
                    },
                }
            )

        # Calculate overall average trigger RSSI
        if all_trigger_rssi:
            results["avg_trigger_rssi_all"] = sum(all_trigger_rssi) / len(all_trigger_rssi)

        # Calculate manufacturer averages
        for manufacturer, stats in results["manufacturer_stats"].items():
            if stats["trigger_rssi_values"]:
                stats["avg_trigger_rssi"] = sum(stats["trigger_rssi_values"]) / len(
                    stats["trigger_rssi_values"]
                )

        return results

    def calculate_informed_min_rssi(
        self,
        strategy: str,
        band: str,
        rssi_patterns: Dict,
        manufacturer: Optional[str] = None,
    ) -> Tuple[int, str]:
        """
        Calculate Min RSSI recommendation based on historical data and strategy

        Args:
            strategy: "optimal" or "max_connectivity"
            band: "2.4GHz", "5GHz", or "6GHz"
            rssi_patterns: Results from analyze_client_rssi_patterns
            manufacturer: Optional manufacturer for specific optimization

        Returns:
            Tuple of (recommended_rssi, reasoning)
        """
        if strategy not in self.STRATEGIES:
            raise ValueError(f"Invalid strategy: {strategy}")

        strategy_config = self.STRATEGIES[strategy]

        # Get base threshold for band
        if band == "2.4GHz":
            base_threshold = strategy_config["base_24ghz"]
        elif band == "5GHz":
            base_threshold = strategy_config["base_5ghz"]
        elif band == "6GHz":
            base_threshold = strategy_config["base_6ghz"]
        else:
            base_threshold = -75  # Default fallback

        # Adjust based on historical roaming behavior
        adjustments = []
        reasoning_parts = []

        # Check if clients are roaming too late (sticky client problem)
        total_roams = (
            rssi_patterns["roaming_trigger_distribution"]["too_late"]
            + rssi_patterns["roaming_trigger_distribution"]["optimal"]
            + rssi_patterns["roaming_trigger_distribution"]["too_early"]
        )

        if total_roams > 0:
            too_late_pct = (
                rssi_patterns["roaming_trigger_distribution"]["too_late"] / total_roams * 100
            )

            if too_late_pct > 40:  # More than 40% roaming too late
                # Need more aggressive Min RSSI
                adjustments.append(+3)
                reasoning_parts.append(
                    f"{too_late_pct:.0f}% of roaming happens too late (<-80 dBm)"
                )

        # Check manufacturer-specific behavior
        if manufacturer and manufacturer in rssi_patterns.get("manufacturer_stats", {}):
            mfg_stats = rssi_patterns["manufacturer_stats"][manufacturer]
            if mfg_stats["avg_trigger_rssi"]:
                # If this manufacturer typically roams at weaker signals, be more conservative
                if mfg_stats["avg_trigger_rssi"] < -78:
                    adjustments.append(-3)
                    reasoning_parts.append(
                        f"{manufacturer} devices roam late (avg {mfg_stats['avg_trigger_rssi']:.0f} dBm)"
                    )
                elif mfg_stats["avg_trigger_rssi"] > -70:
                    adjustments.append(+2)
                    reasoning_parts.append(
                        f"{manufacturer} devices roam early (avg {mfg_stats['avg_trigger_rssi']:.0f} dBm)"
                    )

        # Apply iOS penalty if in max connectivity mode
        if manufacturer == "Apple" and strategy == "max_connectivity":
            ios_penalty = strategy_config["ios_penalty"]
            if ios_penalty:
                adjustments.append(ios_penalty)
                reasoning_parts.append("iOS device sensitivity protection")

        # Calculate final threshold
        total_adjustment = sum(adjustments)
        final_threshold = base_threshold + total_adjustment

        # Build reasoning string
        reasoning = f"Strategy: {strategy_config['description']} (base: {base_threshold} dBm)"
        if reasoning_parts:
            reasoning += f" | Adjustments: {', '.join(reasoning_parts)} ({total_adjustment:+d} dBm)"
        reasoning += f" | Final: {final_threshold} dBm"

        return final_threshold, reasoning
