#!/usr/bin/env python3
"""
UniFi Audit Tool - Configuration Change Applier
Supports dry-run mode and interactive approval for safe network changes
"""

import json
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

console = Console()


class ChangeImpactAnalyzer:
    """Analyzes the impact of proposed ne        host='https://YOUR_CONTROLLER_IP',
    username='YOUR_USERNAME',
    password='YOUR_PASSWORD',rk changes"""

    @staticmethod
    def analyze_channel_change(device, old_channel, new_channel, radio_band):
        """
        Analyze impact of changing radio channel

        Returns: dict with impact details
        """
        # Ensure channels are integers for comparison
        try:
            old_channel = int(old_channel)
            new_channel = int(new_channel)
        except (ValueError, TypeError):
            old_channel = 0
            new_channel = 0

        impact = {
            "type": "Channel Change",
            "severity": "MEDIUM",
            "client_impact": "Temporary disconnection (5-10 seconds)",
            "benefits": [],
            "risks": [],
            "estimated_downtime": "5-10 seconds",
        }

        # Analyze benefits
        if abs(new_channel - old_channel) >= 5:
            impact["benefits"].append("Moving to less congested channel")
            impact["benefits"].append("Should improve throughput and reduce interference")

        # Analyze risks
        if radio_band == "2.4GHz":
            impact["risks"].append("2.4GHz clients will briefly disconnect")
            impact["client_impact"] = "All 2.4GHz clients disconnect for 5-10 seconds"
        else:
            impact["risks"].append("5GHz clients will briefly disconnect")
            impact["client_impact"] = "All 5GHz clients disconnect for 5-10 seconds"

        # Calculate connected clients impact
        active_clients = device.get("num_sta", 0)
        if active_clients > 0:
            impact["affected_clients"] = active_clients
            impact["risks"].append(f"{active_clients} active clients will reconnect")

        return impact

    @staticmethod
    def analyze_power_change(device, old_power, new_power, radio_band):
        """Analyze impact of changing transmit power"""
        impact = {
            "type": "Power Level Change",
            "severity": "LOW",
            "client_impact": "Brief disruption (2-5 seconds)",
            "benefits": [],
            "risks": [],
            "estimated_downtime": "2-5 seconds",
        }

        if new_power < old_power:
            impact["benefits"].append("Reduces co-channel interference")
            impact["benefits"].append("Improves roaming behavior")
            impact["risks"].append("Slightly reduced coverage area")
            impact["client_impact"] = "Minimal - clients may need to reconnect"
        else:
            impact["benefits"].append("Increases coverage area")
            impact["risks"].append("May increase co-channel interference")
            impact["client_impact"] = "Minimal - brief signal adjustment"

        return impact

    @staticmethod
    def analyze_bandwidth_change(device, old_width, new_width, radio_band):
        """Analyze impact of changing channel bandwidth"""
        impact = {
            "type": "Bandwidth Change",
            "severity": "MEDIUM",
            "client_impact": "Temporary disconnection (5-10 seconds)",
            "benefits": [],
            "risks": [],
            "estimated_downtime": "5-10 seconds",
        }

        if new_width > old_width:
            impact["benefits"].append(f"Increased potential throughput")
            impact["risks"].append("Uses more spectrum (may increase interference)")
        else:
            impact["benefits"].append("Reduced interference with other networks")
            impact["benefits"].append("More spectrum available for neighbors")
            impact["risks"].append("Lower maximum throughput")

        return impact


class ChangeApplier:
    """Applies configuration changes with dry-run and interactive modes"""

    def __init__(self, client, dry_run=False, interactive=True):
        """
        Initialize change applier

        Args:
            client: CloudKey Gen2+ API client
            dry_run: If True, simulate changes without applying
            interactive: If True, prompt for approval before each change
        """
        self.client = client
        self.dry_run = dry_run
        self.interactive = interactive
        self.analyzer = ChangeImpactAnalyzer()
        self.changes_log = []

    def apply_channel_change(self, device, radio, new_channel):
        """
        Apply channel change with impact analysis and approval

        Args:
            device: Device dict from API
            radio: Radio band ('ng' for 2.4GHz, 'na' for 5GHz)
            new_channel: New channel number

        Returns:
            bool: True if change was applied (or would be in dry-run)
        """
        device_id = device["_id"]
        device_name = device.get("name", "Unnamed AP")

        # Get current channel
        radio_table = device.get("radio_table", [])
        current_radio = next((r for r in radio_table if r.get("radio") == radio), None)

        if not current_radio:
            console.print(f"[red]Radio {radio} not found on {device_name}[/red]")
            return False

        old_channel = current_radio.get("channel", "auto")
        radio_band = "2.4GHz" if radio == "ng" else "5GHz"

        # Analyze impact
        impact = self.analyzer.analyze_channel_change(device, old_channel, new_channel, radio_band)

        # Display change details
        self._display_change_details(
            device_name=device_name,
            change_type="Channel Change",
            old_value=f"Channel {old_channel} ({radio_band})",
            new_value=f"Channel {new_channel} ({radio_band})",
            impact=impact,
        )

        # Get approval if interactive
        if self.interactive and not self.dry_run:
            if not Confirm.ask("Apply this change?", default=False):
                console.print("[yellow]Change skipped[/yellow]")
                self._log_change(
                    device_name, "Channel Change", "SKIPPED", f"{old_channel} → {new_channel}"
                )
                return False

        # Apply or simulate
        if self.dry_run:
            console.print(
                f"[cyan]DRY RUN: Would change channel from {old_channel} to {new_channel}[/cyan]"
            )
            self._log_change(
                device_name, "Channel Change", "DRY-RUN", f"{old_channel} → {new_channel}"
            )
            return True
        else:
            console.print(f"[yellow]Applying channel change...[/yellow]")

            # Build complete radio_table with all existing fields
            # Must include all radios and preserve all existing settings
            updated_radio_table = []
            for r in radio_table:
                radio_entry = dict(r)  # Copy all existing fields
                if r.get("radio") == radio:
                    # Update only the channel for target radio
                    radio_entry["channel"] = new_channel
                updated_radio_table.append(radio_entry)

            update_data = {"radio_table": updated_radio_table}

            result = self.client.put(f"s/{self.client.site}/rest/device/{device_id}", update_data)

            if result:
                console.print(f"[green]✓ Channel changed successfully![/green]")
                self._log_change(
                    device_name, "Channel Change", "SUCCESS", f"{old_channel} → {new_channel}"
                )
                return True
            else:
                console.print(f"[red]✗ Failed to change channel[/red]")
                self._log_change(
                    device_name, "Channel Change", "FAILED", f"{old_channel} → {new_channel}"
                )
                return False

    def apply_power_change(self, device, radio, new_power_mode):
        """
        Apply transmit power change with impact analysis and approval

        Args:
            device: Device dict from API
            radio: Radio band ('ng' for 2.4GHz, 'na' for 5GHz)
            new_power_mode: New power mode ('auto', 'low', 'medium', 'high')

        Returns:
            bool: True if change was applied (or would be in dry-run)
        """
        device_id = device["_id"]
        device_name = device.get("name", "Unnamed AP")

        # Get current power
        radio_table = device.get("radio_table", [])
        current_radio = next((r for r in radio_table if r.get("radio") == radio), None)

        if not current_radio:
            console.print(f"[red]Radio {radio} not found on {device_name}[/red]")
            return False

        old_power_mode = current_radio.get("tx_power_mode", "auto")
        radio_band = "2.4GHz" if radio == "ng" else "5GHz"

        # Analyze impact
        impact = self.analyzer.analyze_power_change(
            device, old_power_mode, new_power_mode, radio_band
        )

        # Display change details
        self._display_change_details(
            device_name=device_name,
            change_type="Power Level Change",
            old_value=f"{old_power_mode} ({radio_band})",
            new_value=f"{new_power_mode} ({radio_band})",
            impact=impact,
        )

        # Get approval if interactive
        if self.interactive and not self.dry_run:
            if not Confirm.ask("Apply this change?", default=False):
                console.print("[yellow]Change skipped[/yellow]")
                self._log_change(
                    device_name, "Power Change", "SKIPPED", f"{old_power_mode} → {new_power_mode}"
                )
                return False

        # Apply or simulate
        if self.dry_run:
            console.print(
                f"[cyan]DRY RUN: Would change power from {old_power_mode} to {new_power_mode}[/cyan]"
            )
            self._log_change(
                device_name, "Power Change", "DRY-RUN", f"{old_power_mode} → {new_power_mode}"
            )
            return True
        else:
            console.print(f"[yellow]Applying power change...[/yellow]")

            # Build complete radio_table with all existing fields
            # Must include all radios and preserve all existing settings
            updated_radio_table = []
            for r in radio_table:
                radio_entry = dict(r)  # Copy all existing fields
                if r.get("radio") == radio:
                    # Update only the power mode for target radio
                    radio_entry["tx_power_mode"] = new_power_mode
                updated_radio_table.append(radio_entry)

            update_data = {"radio_table": updated_radio_table}

            result = self.client.put(f"s/{self.client.site}/rest/device/{device_id}", update_data)

            if result:
                console.print(f"[green]✓ Power level changed successfully![/green]")
                self._log_change(
                    device_name, "Power Change", "SUCCESS", f"{old_power_mode} → {new_power_mode}"
                )
                return True
            else:
                console.print(f"[red]✗ Failed to change power level[/red]")
                self._log_change(
                    device_name, "Power Change", "FAILED", f"{old_power_mode} → {new_power_mode}"
                )
                return False

    def apply_band_steering(self, device, new_mode="prefer_5g"):
        """
        Apply band steering configuration change

        Args:
            device: Device dict from API
            new_mode: Band steering mode ('off', 'prefer_5g', 'equal')
                     'prefer_5g' (recommended): Steers dual-band clients to 5GHz
                     'equal': Balances clients between bands
                     'off': Disables band steering

        Returns:
            bool: True if change was applied (or would be in dry-run)
        """
        device_id = device["_id"]
        device_name = device.get("name", "Unnamed AP")

        # Get current band steering mode
        old_mode = device.get("bandsteering_mode", "off")

        # Skip if already set to desired mode
        if old_mode == new_mode:
            console.print(f"[dim]{device_name} already has band steering set to '{new_mode}'[/dim]")
            return True

        # Create impact analysis
        impact = {
            "type": "Band Steering Configuration",
            "severity": "LOW",
            "client_impact": "No immediate disconnection - affects future connections",
            "benefits": [],
            "risks": [],
            "estimated_downtime": "None",
        }

        if new_mode == "prefer_5g":
            impact["benefits"].append("Dual-band clients will prefer 5GHz band")
            impact["benefits"].append("Reduces 2.4GHz congestion")
            impact["benefits"].append("Improves throughput for capable devices")
            impact["benefits"].append("Better airtime utilization")
            impact["client_impact"] = (
                "Currently connected clients stay on current band. New connections and roaming events will prefer 5GHz."
            )
        elif new_mode == "equal":
            impact["benefits"].append("Balances clients between 2.4GHz and 5GHz")
            impact["benefits"].append("Prevents band overload")
        elif new_mode == "off":
            impact["risks"].append("Dual-band clients may stay on 2.4GHz")
            impact["risks"].append("Reduced network performance")

        # Display change details
        self._display_change_details(
            device_name=device_name,
            change_type="Band Steering Configuration",
            old_value=old_mode if old_mode else "off",
            new_value=new_mode,
            impact=impact,
        )

        # Get approval if interactive
        if self.interactive and not self.dry_run:
            if not Confirm.ask("Apply this change?", default=False):
                console.print("[yellow]Change skipped[/yellow]")
                self._log_change(
                    device_name, "Band Steering", "SKIPPED", f"{old_mode} → {new_mode}"
                )
                return False

        # Apply or simulate
        if self.dry_run:
            console.print(f"[cyan]DRY RUN: Would enable band steering mode '{new_mode}'[/cyan]")
            self._log_change(device_name, "Band Steering", "DRY-RUN", f"{old_mode} → {new_mode}")
            return True
        else:
            console.print(f"[yellow]Applying band steering change...[/yellow]")

            # Update band steering mode
            update_data = {"bandsteering_mode": new_mode}

            result = self.client.put(f"s/{self.client.site}/rest/device/{device_id}", update_data)

            if result:
                console.print(f"[green]✓ Band steering configured successfully![/green]")
                self._log_change(
                    device_name, "Band Steering", "SUCCESS", f"{old_mode} → {new_mode}"
                )
                return True
            else:
                console.print(f"[red]✗ Failed to configure band steering[/red]")
                self._log_change(device_name, "Band Steering", "FAILED", f"{old_mode} → {new_mode}")
                return False

    def apply_min_rssi(self, device, radio_name, new_enabled=True, new_value=-75):
        """
        Apply minimum RSSI configuration change

        Minimum RSSI forces weak clients to roam, preventing sticky client problems

        Args:
            device: Device dict from API
            radio_name: Radio identifier ('ng' for 2.4GHz, 'na' for 5GHz)
            new_enabled: Whether to enable min RSSI
            new_value: Min RSSI threshold in dBm (e.g., -75)

        Returns:
            bool: True if change was applied (or would be in dry-run)
        """
        device_id = device["_id"]
        device_name = device.get("name", "Unnamed AP")
        band = "2.4GHz" if radio_name == "ng" else "5GHz" if radio_name == "na" else "6GHz"

        # Get current radio settings
        radio_table = device.get("radio_table", [])
        current_enabled = False
        current_value = None

        for radio in radio_table:
            if radio.get("radio") == radio_name:
                current_enabled = radio.get("min_rssi_enabled", False)
                current_value = radio.get("min_rssi", None)
                break

        # Skip if already configured correctly
        if current_enabled == new_enabled and current_value == new_value:
            console.print(f"[dim]{device_name} {band} already has min RSSI configured[/dim]")
            return True

        # Create impact analysis
        impact = {
            "type": "Minimum RSSI Configuration",
            "severity": "LOW",
            "client_impact": "Weak clients will be forced to roam",
            "benefits": [],
            "risks": [],
            "estimated_downtime": "None",
        }

        if new_enabled:
            impact["benefits"].append(f"Clients below {new_value} dBm will be forced to roam")
            impact["benefits"].append("Prevents sticky client problems")
            impact["benefits"].append("Improves overall network performance")
            impact["benefits"].append("Reduces airtime consumed by weak clients")
            impact["benefits"].append("Better VoIP and video quality")
            impact["client_impact"] = (
                f"Clients with signal below {new_value} dBm will be disconnected and forced to find a better AP. Strong clients unaffected."
            )
        else:
            impact["risks"].append("Weak clients may stay connected with poor signal")
            impact["risks"].append("Reduced network performance")

        # Display change details
        old_display = f"{'enabled' if current_enabled else 'disabled'}"
        if current_value:
            old_display += f" ({current_value} dBm)"
        new_display = f"{'enabled' if new_enabled else 'disabled'}"
        if new_enabled:
            new_display += f" ({new_value} dBm)"

        self._display_change_details(
            device_name=f"{device_name} {band}",
            change_type="Minimum RSSI",
            old_value=old_display,
            new_value=new_display,
            impact=impact,
        )

        # Get approval if interactive
        if self.interactive and not self.dry_run:
            if not Confirm.ask("Apply this change?", default=False):
                console.print("[yellow]Change skipped[/yellow]")
                self._log_change(
                    f"{device_name} {band}", "Min RSSI", "SKIPPED", f"{old_display} → {new_display}"
                )
                return False

        # Apply or simulate
        if self.dry_run:
            console.print(f"[cyan]DRY RUN: Would configure min RSSI to {new_display}[/cyan]")
            self._log_change(
                f"{device_name} {band}", "Min RSSI", "DRY-RUN", f"{old_display} → {new_display}"
            )
            return True
        else:
            console.print(f"[yellow]Applying min RSSI change...[/yellow]")

            # Update radio_table with new min RSSI settings
            updated_radio_table = []
            for radio in radio_table:
                radio_copy = radio.copy()
                if radio.get("radio") == radio_name:
                    radio_copy["min_rssi_enabled"] = new_enabled
                    if new_enabled:
                        radio_copy["min_rssi"] = new_value
                updated_radio_table.append(radio_copy)

            update_data = {"radio_table": updated_radio_table}

            result = self.client.put(f"s/{self.client.site}/rest/device/{device_id}", update_data)

            if result:
                console.print(f"[green]✓ Min RSSI configured successfully![/green]")
                self._log_change(
                    f"{device_name} {band}", "Min RSSI", "SUCCESS", f"{old_display} → {new_display}"
                )
                return True
            else:
                console.print(f"[red]✗ Failed to configure min RSSI[/red]")
                self._log_change(
                    f"{device_name} {band}", "Min RSSI", "FAILED", f"{old_display} → {new_display}"
                )
                return False

    def _display_change_details(self, device_name, change_type, old_value, new_value, impact):
        """Display detailed change information"""
        console.print("\n")
        console.print(
            Panel(
                f"[bold]{change_type}[/bold]\n"
                f"Device: [cyan]{device_name}[/cyan]\n"
                f"Current: [yellow]{old_value}[/yellow]\n"
                f"Proposed: [green]{new_value}[/green]",
                title="Proposed Change",
                border_style="cyan",
            )
        )

        # Impact table
        table = Table(title="Impact Analysis", show_header=True, header_style="bold magenta")
        table.add_column("Category", style="cyan", width=20)
        table.add_column("Details", style="white")

        # Severity
        severity_color = {"LOW": "green", "MEDIUM": "yellow", "HIGH": "red"}.get(
            impact["severity"], "white"
        )
        table.add_row("Severity", f"[{severity_color}]{impact['severity']}[/{severity_color}]")

        # Client Impact
        table.add_row("Client Impact", impact["client_impact"])

        # Estimated Downtime
        table.add_row("Downtime", impact["estimated_downtime"])

        # Affected Clients
        if "affected_clients" in impact:
            table.add_row("Affected Clients", str(impact["affected_clients"]))

        console.print(table)

        # Benefits
        if impact["benefits"]:
            console.print("\n[bold green]Benefits:[/bold green]")
            for benefit in impact["benefits"]:
                console.print(f"  ✓ {benefit}")

        # Risks
        if impact["risks"]:
            console.print("\n[bold yellow]Considerations:[/bold yellow]")
            for risk in impact["risks"]:
                console.print(f"  ⚠ {risk}")

        console.print()

    def _log_change(self, device_name, change_type, status, details):
        """Log a change for later reporting"""
        self.changes_log.append(
            {
                "timestamp": datetime.now().isoformat(),
                "device": device_name,
                "type": change_type,
                "status": status,
                "details": details,
            }
        )

    def generate_report(self):
        """Generate summary report of all changes"""
        console.print("\n")
        console.print(Panel("[bold]Change Summary Report[/bold]", style="cyan"))

        if not self.changes_log:
            console.print("[yellow]No changes were made[/yellow]")
            return

        # Summary table
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Device", style="cyan")
        table.add_column("Change Type", style="white")
        table.add_column("Status", style="white")
        table.add_column("Details", style="white")

        for change in self.changes_log:
            status_style = {
                "SUCCESS": "green",
                "FAILED": "red",
                "SKIPPED": "yellow",
                "DRY-RUN": "cyan",
            }.get(change["status"], "white")

            table.add_row(
                change["device"],
                change["type"],
                f"[{status_style}]{change['status']}[/{status_style}]",
                change["details"],
            )

        console.print(table)

        # Statistics
        total = len(self.changes_log)
        success = sum(1 for c in self.changes_log if c["status"] == "SUCCESS")
        failed = sum(1 for c in self.changes_log if c["status"] == "FAILED")
        skipped = sum(1 for c in self.changes_log if c["status"] == "SKIPPED")
        dry_run = sum(1 for c in self.changes_log if c["status"] == "DRY-RUN")

        console.print(f"\n[bold]Statistics:[/bold]")
        console.print(f"Total Changes: {total}")
        if success > 0:
            console.print(f"[green]Successful: {success}[/green]")
        if failed > 0:
            console.print(f"[red]Failed: {failed}[/red]")
        if skipped > 0:
            console.print(f"[yellow]Skipped: {skipped}[/yellow]")
        if dry_run > 0:
            console.print(f"[cyan]Simulated (Dry-Run): {dry_run}[/cyan]")

        # Save to file
        if not self.dry_run:
            log_file = f"changes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(log_file, "w") as f:
                json.dump(self.changes_log, f, indent=2)
            console.print(f"\n[dim]Change log saved to: {log_file}[/dim]")


def main():
    """Example usage"""
    from api.cloudkey_gen2_client import CloudKeyGen2Client, get_devices

    # Example: Create client and applier
    client = CloudKeyGen2Client(
        host="https://192.168.1.119", username="audit", password="password", site="default"
    )

    # Login
    if not client.login():
        console.print("[red]Failed to login[/red]")
        return

    # Get devices
    devices = get_devices(client)
    if not devices:
        console.print("[red]No devices found[/red]")
        return

    # Find first AP
    ap = next((d for d in devices if d.get("type") == "uap"), None)
    if not ap:
        console.print("[red]No access points found[/red]")
        return

    # Create applier in dry-run mode
    console.print("[bold]DRY RUN MODE - No actual changes will be made[/bold]\n")
    applier = ChangeApplier(client, dry_run=True, interactive=False)

    # Simulate some changes
    applier.apply_channel_change(ap, "ng", 6)
    applier.apply_power_change(ap, "ng", "medium")

    # Generate report
    applier.generate_report()


if __name__ == "__main__":
    main()
