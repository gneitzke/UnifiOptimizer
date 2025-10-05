#!/usr/bin/env python3
"""
Client Health Diagnostics Module
Analyzes client signal strength, disconnections, and overall health
"""

from collections import defaultdict

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


class ClientHealthAnalyzer:
    """Analyzes client connection health and signal quality"""

    # RSSI thresholds
    RSSI_EXCELLENT = -50
    RSSI_GOOD = -60
    RSSI_FAIR = -70
    RSSI_POOR = -80

    def __init__(self):
        self.clients = []
        self.disconnection_events = []
        self.roaming_events = []

    def analyze_clients(self, clients, events=None):
        """
        Analyze client health metrics

        Args:
            clients: List of client dicts from UniFi API
            events: Optional list of event dicts for disconnection tracking

        Returns:
            dict: Analysis results
        """
        self.clients = clients

        if events:
            self._process_events(events)

        results = {
            "total_clients": len(clients),
            "by_signal_quality": self._categorize_by_signal(),
            "by_band": self._categorize_by_band(),
            "weak_clients": self._identify_weak_clients(),
            "disconnection_prone": self._identify_disconnection_prone(),
            "roaming_issues": self._identify_roaming_issues(),
            "power_distribution": self._analyze_power_distribution(),
            "health_scores": self._calculate_health_scores(),
        }

        return results

    def _process_events(self, events):
        """Process events to identify disconnections and roaming"""
        for event in events:
            event_type = event.get("key", "")

            if "EVT_WU_Disconnected" in event_type or "EVT_WU_DISCONNECTED" in event_type:
                self.disconnection_events.append(event)

            elif "EVT_WU_Roam" in event_type or "EVT_WU_ROAM" in event_type:
                self.roaming_events.append(event)

    def _categorize_by_signal(self):
        """Categorize clients by signal strength (wireless only)"""
        categories = {
            "excellent": [],  # > -50 dBm
            "good": [],  # -50 to -60 dBm
            "fair": [],  # -60 to -70 dBm
            "poor": [],  # -70 to -80 dBm
            "critical": [],  # < -80 dBm
            "wired": [],  # Wired clients (no RSSI)
        }

        for client in self.clients:
            # Skip wired clients for signal categorization
            if client.get("is_wired", False):
                categories["wired"].append(client)
                continue

            rssi = client.get("rssi", -100)

            # FIX: Some UniFi controllers return positive RSSI values
            # RSSI should always be negative in dBm for WiFi
            if rssi > 0:
                rssi = -rssi

            if rssi > self.RSSI_EXCELLENT:
                categories["excellent"].append(client)
            elif rssi > self.RSSI_GOOD:
                categories["good"].append(client)
            elif rssi > self.RSSI_FAIR:
                categories["fair"].append(client)
            elif rssi > self.RSSI_POOR:
                categories["poor"].append(client)
            else:
                categories["critical"].append(client)

        return categories

    def _categorize_by_band(self):
        """Categorize clients by frequency band"""
        bands = {"2.4GHz": [], "5GHz": [], "6GHz": [], "unknown": []}

        for client in self.clients:
            channel = client.get("channel", 0)

            if 1 <= channel <= 14:
                bands["2.4GHz"].append(client)
            elif 36 <= channel <= 165:
                bands["5GHz"].append(client)
            elif channel > 165:
                bands["6GHz"].append(client)
            else:
                bands["unknown"].append(client)

        return bands

    def _identify_weak_clients(self):
        """Identify clients with weak signal (wireless only)"""
        weak = []

        for client in self.clients:
            # Skip wired clients
            if client.get("is_wired", False):
                continue

            rssi = client.get("rssi", 0)

            # FIX: Some UniFi controllers return positive RSSI values
            if rssi > 0:
                rssi = -rssi

            if rssi < self.RSSI_FAIR:
                client_info = {
                    "mac": client.get("mac", "Unknown"),
                    "hostname": client.get("hostname", "Unknown"),
                    "ip": client.get("ip", "Unknown"),
                    "rssi": rssi,
                    "ap_mac": client.get("ap_mac", "Unknown"),
                    "channel": client.get("channel", 0),
                    "tx_rate": client.get("tx_rate", 0),
                    "rx_rate": client.get("rx_rate", 0),
                    "signal_quality": self._get_signal_quality(rssi),
                }
                weak.append(client_info)

        # Sort by RSSI (worst first)
        weak.sort(key=lambda x: x["rssi"])

        return weak

    def _identify_disconnection_prone(self):
        """Identify clients with frequent disconnections"""
        # Count disconnections per MAC
        disconnect_count = defaultdict(int)

        for event in self.disconnection_events:
            mac = event.get("user", event.get("client_mac", ""))
            if mac:
                disconnect_count[mac] += 1

        # Find clients with multiple disconnections
        prone = []
        for client in self.clients:
            mac = client.get("mac", "")
            count = disconnect_count.get(mac, 0)

            if count >= 3:  # 3+ disconnections
                prone.append(
                    {
                        "mac": mac,
                        "hostname": client.get("hostname", "Unknown"),
                        "ip": client.get("ip", "Unknown"),
                        "disconnect_count": count,
                        "rssi": client.get("rssi", 0),
                        "ap_mac": client.get("ap_mac", "Unknown"),
                    }
                )

        # Sort by disconnect count (most first)
        prone.sort(key=lambda x: x["disconnect_count"], reverse=True)

        return prone

    def _identify_roaming_issues(self):
        """Identify clients with roaming problems"""
        # Count roams per MAC
        roam_count = defaultdict(int)

        for event in self.roaming_events:
            mac = event.get("user", event.get("client_mac", ""))
            if mac:
                roam_count[mac] += 1

        issues = []
        for client in self.clients:
            mac = client.get("mac", "")
            count = roam_count.get(mac, 0)
            rssi = client.get("rssi", 0)

            # Excessive roaming or roaming while signal is good
            if count >= 5 or (count >= 2 and rssi > self.RSSI_GOOD):
                issues.append(
                    {
                        "mac": mac,
                        "hostname": client.get("hostname", "Unknown"),
                        "roam_count": count,
                        "rssi": rssi,
                        "issue_type": "excessive" if count >= 5 else "sticky_client",
                    }
                )

        return issues

    def _analyze_power_distribution(self):
        """Analyze client power/signal distribution (wireless only)"""
        distribution = {"excellent": 0, "good": 0, "fair": 0, "poor": 0, "critical": 0, "wired": 0}

        rssi_values = []

        for client in self.clients:
            # Count wired clients separately
            if client.get("is_wired", False):
                distribution["wired"] += 1
                continue

            rssi = client.get("rssi", -100)

            # FIX: Some UniFi controllers return positive RSSI values
            if rssi > 0:
                rssi = -rssi

            rssi_values.append(rssi)

            if rssi > self.RSSI_EXCELLENT:
                distribution["excellent"] += 1
            elif rssi > self.RSSI_GOOD:
                distribution["good"] += 1
            elif rssi > self.RSSI_FAIR:
                distribution["fair"] += 1
            elif rssi > self.RSSI_POOR:
                distribution["poor"] += 1
            else:
                distribution["critical"] += 1

        # Calculate statistics
        if rssi_values:
            distribution["avg_rssi"] = sum(rssi_values) / len(rssi_values)
            distribution["min_rssi"] = min(rssi_values)
            distribution["max_rssi"] = max(rssi_values)

        return distribution

    def _calculate_health_scores(self):
        """Calculate overall health score for each client"""
        scores = []

        for client in self.clients:
            mac = client.get("mac", "")
            rssi = client.get("rssi", 0)
            is_wired = client.get("is_wired", False)

            # FIX: Some UniFi controllers return positive RSSI values
            if rssi > 0:
                rssi = -rssi

            # Wired clients get perfect signal score (no wireless)
            if is_wired:
                signal_score = 100
            # Base score from signal strength (0-100) for wireless clients
            elif rssi > self.RSSI_EXCELLENT:
                signal_score = 100
            elif rssi > self.RSSI_GOOD:
                signal_score = 80
            elif rssi > self.RSSI_FAIR:
                signal_score = 60
            elif rssi > self.RSSI_POOR:
                signal_score = 40
            elif rssi < -90:  # Very weak or no signal
                signal_score = 20
            else:
                signal_score = 20

            # Penalty for disconnections
            disconnect_penalty = (
                len(
                    [
                        e
                        for e in self.disconnection_events
                        if e.get("user", "") == mac or e.get("client_mac", "") == mac
                    ]
                )
                * 5
            )

            # Penalty for excessive roaming
            roam_penalty = (
                len(
                    [
                        e
                        for e in self.roaming_events
                        if e.get("user", "") == mac or e.get("client_mac", "") == mac
                    ]
                )
                * 2
            )

            # Calculate final score
            final_score = max(0, signal_score - disconnect_penalty - roam_penalty)

            scores.append(
                {
                    "mac": mac,
                    "hostname": client.get("hostname", "Unknown"),
                    "ip": client.get("ip", "Unknown"),
                    "score": final_score,
                    "signal_score": signal_score,
                    "disconnect_penalty": disconnect_penalty,
                    "roam_penalty": roam_penalty,
                    "grade": self._get_grade(final_score),
                    "is_wired": is_wired,
                }
            )

        # Sort by score (worst first)
        scores.sort(key=lambda x: x["score"])

        return scores

    def _get_signal_quality(self, rssi):
        """Get signal quality label"""
        if rssi > self.RSSI_EXCELLENT:
            return "Excellent"
        elif rssi > self.RSSI_GOOD:
            return "Good"
        elif rssi > self.RSSI_FAIR:
            return "Fair"
        elif rssi > self.RSSI_POOR:
            return "Poor"
        else:
            return "Critical"

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

    def generate_rssi_histogram(self, clients=None, width=60):
        """
        Generate colored RSSI histogram

        Args:
            clients: List of clients (uses self.clients if None)
            width: Width of histogram bars

        Returns:
            str: Formatted histogram
        """
        if clients is None:
            clients = self.clients

        if not clients:
            return "[yellow]No clients connected[/yellow]"

        # Collect RSSI values and fix positive values
        rssi_values = []
        for c in clients:
            rssi = c.get("rssi", -100)
            # FIX: Some UniFi controllers return positive RSSI values
            if rssi > 0:
                rssi = -rssi
            rssi_values.append(rssi)

        # Create bins
        bins = {
            "Excellent (> -50)": [],
            "Good (-50 to -60)": [],
            "Fair (-60 to -70)": [],
            "Poor (-70 to -80)": [],
            "Critical (< -80)": [],
        }

        for rssi in rssi_values:
            if rssi > self.RSSI_EXCELLENT:
                bins["Excellent (> -50)"].append(rssi)
            elif rssi > self.RSSI_GOOD:
                bins["Good (-50 to -60)"].append(rssi)
            elif rssi > self.RSSI_FAIR:
                bins["Fair (-60 to -70)"].append(rssi)
            elif rssi > self.RSSI_POOR:
                bins["Poor (-70 to -80)"].append(rssi)
            else:
                bins["Critical (< -80)"].append(rssi)

        # Create histogram
        max_count = max(len(v) for v in bins.values()) if bins else 1

        histogram = []
        colors = {
            "Excellent (> -50)": "green",
            "Good (-50 to -60)": "blue",
            "Fair (-60 to -70)": "yellow",
            "Poor (-70 to -80)": "magenta",
            "Critical (< -80)": "red",
        }

        # Always show all categories in order
        ordered_labels = [
            "Excellent (> -50)",
            "Good (-50 to -60)",
            "Fair (-60 to -70)",
            "Poor (-70 to -80)",
            "Critical (< -80)",
        ]

        for label in ordered_labels:
            values = bins[label]
            count = len(values)

            if len(rssi_values) > 0:
                percentage = (count / len(rssi_values)) * 100
            else:
                percentage = 0

            bar_width = int((count / max_count) * width) if max_count > 0 else 0
            color = colors.get(label, "white")

            # Show bar even if empty (with different style)
            if count > 0:
                bar = "█" * bar_width
                histogram.append(
                    f"[{color}]{label:20s} {bar} {count:3d} ({percentage:5.1f}%)[/{color}]"
                )
            else:
                histogram.append(
                    f"[dim]{label:20s} [/dim][dim]{count:3d} ({percentage:5.1f}%)[/dim]"
                )

        return "\n".join(histogram)


def display_client_health_report(analysis):
    """Display comprehensive client health report"""

    console.print("\n")
    console.print(Panel("[bold cyan]Client Health Diagnostic Report[/bold cyan]", style="cyan"))

    # Overview
    console.print("\n[bold]Overview[/bold]")
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("Total Clients", str(analysis["total_clients"]))

    by_band = analysis["by_band"]
    table.add_row("2.4GHz Clients", str(len(by_band["2.4GHz"])))
    table.add_row("5GHz Clients", str(len(by_band["5GHz"])))

    dist = analysis["power_distribution"]
    if dist.get("wired", 0) > 0:
        table.add_row("[green]Wired Clients[/green]", f"[green]{dist['wired']}[/green]")

    if "avg_rssi" in dist:
        table.add_row("Average RSSI", f"{dist['avg_rssi']:.1f} dBm")
        table.add_row("Best RSSI", f"{dist['max_rssi']} dBm")
        table.add_row("Worst RSSI", f"{dist['min_rssi']} dBm")

    console.print(table)

    # RSSI Distribution Histogram
    console.print("\n[bold cyan]📊 Signal Strength Distribution (Wireless Clients)[/bold cyan]")
    console.print("[dim]Visual representation of client signal quality across your network[/dim]\n")

    analyzer = ClientHealthAnalyzer()
    analyzer.clients = []  # Will use clients from analysis

    # Reconstruct clients from categories
    all_clients = []
    for category, clients in analysis["by_signal_quality"].items():
        if category != "wired":  # Exclude wired clients from RSSI histogram
            all_clients.extend(clients)

    if all_clients:
        histogram = analyzer.generate_rssi_histogram(all_clients)
        console.print(histogram)

        # Add a summary bar
        total_wireless = len(all_clients)
        console.print(f"\n[dim]Total Wireless Clients: {total_wireless}[/dim]")
    else:
        console.print("[yellow]No wireless clients connected[/yellow]")

    # Show wired client count separately
    wired_count = len(analysis["by_signal_quality"].get("wired", []))
    if wired_count > 0:
        console.print(f"[green]Wired Clients: {wired_count} (not shown in histogram)[/green]")

    # Weak Clients
    weak = analysis["weak_clients"]
    if weak:
        console.print("\n[bold red]⚠️  Clients with Weak Signal[/bold red]")
        table = Table(show_header=True, header_style="bold red")
        table.add_column("Hostname", style="cyan")
        table.add_column("IP", style="white")
        table.add_column("RSSI", style="red")
        table.add_column("Quality", style="yellow")
        table.add_column("Channel", style="white")

        for client in weak[:10]:  # Top 10 worst
            table.add_row(
                client["hostname"][:20],
                client["ip"],
                f"{client['rssi']} dBm",
                client["signal_quality"],
                str(client["channel"]),
            )

        console.print(table)

        if len(weak) > 10:
            console.print(f"[dim]... and {len(weak) - 10} more[/dim]")

    # Disconnection-Prone Clients
    prone = analysis["disconnection_prone"]
    if prone:
        console.print("\n[bold yellow]⚠️  Clients with Frequent Disconnections[/bold yellow]")
        table = Table(show_header=True, header_style="bold yellow")
        table.add_column("Hostname", style="cyan")
        table.add_column("IP", style="white")
        table.add_column("Disconnects", style="red")
        table.add_column("Current RSSI", style="white")

        for client in prone[:10]:
            table.add_row(
                client["hostname"][:20],
                client["ip"],
                str(client["disconnect_count"]),
                f"{client['rssi']} dBm",
            )

        console.print(table)

    # Health Scores
    scores = analysis["health_scores"]
    if scores:
        console.print("\n[bold]Client Health Scores (Worst First)[/bold]")
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Hostname", style="cyan")
        table.add_column("IP", style="white")
        table.add_column("Grade", style="white")
        table.add_column("Score", style="white")
        table.add_column("Issues", style="yellow")

        for client in scores[:15]:  # Top 15 worst
            grade_color = {
                "A": "green",
                "B": "blue",
                "C": "yellow",
                "D": "magenta",
                "F": "red",
            }.get(client["grade"], "white")

            issues = []
            if client.get("is_wired", False):
                issues.append("[green]Wired[/green]")
            if client["disconnect_penalty"] > 0:
                issues.append(f"Disconnects: {client['disconnect_penalty']//5}")
            if client["roam_penalty"] > 0:
                issues.append(f"Roams: {client['roam_penalty']//2}")

            table.add_row(
                client["hostname"][:20],
                client["ip"],
                f"[{grade_color}]{client['grade']}[/{grade_color}]",
                f"{client['score']}/100",
                ", ".join(issues) if issues else "None",
            )

        console.print(table)

    # Summary Recommendations
    console.print("\n[bold green]Recommendations[/bold green]")

    recommendations = []

    if len(weak) > analysis["total_clients"] * 0.2:
        recommendations.append(
            "• Over 20% of clients have weak signal - consider adjusting AP placement or power levels"
        )

    if prone:
        recommendations.append(
            f"• {len(prone)} clients experiencing frequent disconnections - investigate interference or AP issues"
        )

    critical_count = len(analysis["by_signal_quality"]["critical"])
    if critical_count > 0:
        recommendations.append(
            f"• {critical_count} clients in critical signal range (< -80 dBm) - immediate attention needed"
        )

    if not recommendations:
        recommendations.append("• Client health looks good! No major issues detected.")

    for rec in recommendations:
        console.print(rec)

    console.print()


# Example usage function
def analyze_client_health(client, site="default"):
    """
    Run client health analysis

    Args:
        client: CloudKey Gen2+ API client
        site: Site name

    Returns:
        dict: Analysis results
    """
    console.print("[cyan]Collecting client data...[/cyan]")

    # Get devices
    devices_response = client.get(f"s/{site}/stat/device")

    # Get clients
    clients_response = client.get(f"s/{site}/stat/sta")
    clients = clients_response.get("data", []) if clients_response else []

    # Get events
    events_response = client.get(f"s/{site}/stat/event")
    events = events_response.get("data", []) if events_response else []

    console.print(f"[green]Found {len(clients)} clients and {len(events)} events[/green]\n")

    # Analyze
    analyzer = ClientHealthAnalyzer()
    analysis = analyzer.analyze_clients(clients, events)

    # Display report
    display_client_health_report(analysis)

    return analysis
