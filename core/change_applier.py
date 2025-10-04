#!/usr/bin/env python3
"""
UniFi Audit Tool - Configuration Change Applier
Supports dry-run mode and interactive approval for safe network changes
"""

import sys
import json
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Confirm
from datetime import datetime

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
            'type': 'Channel Change',
            'severity': 'MEDIUM',
            'client_impact': 'Temporary disconnection (5-10 seconds)',
            'benefits': [],
            'risks': [],
            'estimated_downtime': '5-10 seconds'
        }
        
        # Analyze benefits
        if abs(new_channel - old_channel) >= 5:
            impact['benefits'].append('Moving to less congested channel')
            impact['benefits'].append('Should improve throughput and reduce interference')
        
        # Analyze risks
        if radio_band == '2.4GHz':
            impact['risks'].append('2.4GHz clients will briefly disconnect')
            impact['client_impact'] = 'All 2.4GHz clients disconnect for 5-10 seconds'
        else:
            impact['risks'].append('5GHz clients will briefly disconnect')
            impact['client_impact'] = 'All 5GHz clients disconnect for 5-10 seconds'
        
        # Calculate connected clients impact
        active_clients = device.get('num_sta', 0)
        if active_clients > 0:
            impact['affected_clients'] = active_clients
            impact['risks'].append(f'{active_clients} active clients will reconnect')
        
        return impact
    
    @staticmethod
    def analyze_power_change(device, old_power, new_power, radio_band):
        """Analyze impact of changing transmit power"""
        impact = {
            'type': 'Power Level Change',
            'severity': 'LOW',
            'client_impact': 'Brief disruption (2-5 seconds)',
            'benefits': [],
            'risks': [],
            'estimated_downtime': '2-5 seconds'
        }
        
        if new_power < old_power:
            impact['benefits'].append('Reduces co-channel interference')
            impact['benefits'].append('Improves roaming behavior')
            impact['risks'].append('Slightly reduced coverage area')
            impact['client_impact'] = 'Minimal - clients may need to reconnect'
        else:
            impact['benefits'].append('Increases coverage area')
            impact['risks'].append('May increase co-channel interference')
            impact['client_impact'] = 'Minimal - brief signal adjustment'
        
        return impact
    
    @staticmethod
    def analyze_bandwidth_change(device, old_width, new_width, radio_band):
        """Analyze impact of changing channel bandwidth"""
        impact = {
            'type': 'Bandwidth Change',
            'severity': 'MEDIUM',
            'client_impact': 'Temporary disconnection (5-10 seconds)',
            'benefits': [],
            'risks': [],
            'estimated_downtime': '5-10 seconds'
        }
        
        if new_width > old_width:
            impact['benefits'].append(f'Increased potential throughput')
            impact['risks'].append('Uses more spectrum (may increase interference)')
        else:
            impact['benefits'].append('Reduced interference with other networks')
            impact['benefits'].append('More spectrum available for neighbors')
            impact['risks'].append('Lower maximum throughput')
        
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
        device_id = device['_id']
        device_name = device.get('name', 'Unnamed AP')
        
        # Get current channel
        radio_table = device.get('radio_table', [])
        current_radio = next((r for r in radio_table if r.get('radio') == radio), None)
        
        if not current_radio:
            console.print(f"[red]Radio {radio} not found on {device_name}[/red]")
            return False
        
        old_channel = current_radio.get('channel', 'auto')
        radio_band = '2.4GHz' if radio == 'ng' else '5GHz'
        
        # Analyze impact
        impact = self.analyzer.analyze_channel_change(device, old_channel, new_channel, radio_band)
        
        # Display change details
        self._display_change_details(
            device_name=device_name,
            change_type='Channel Change',
            old_value=f'Channel {old_channel} ({radio_band})',
            new_value=f'Channel {new_channel} ({radio_band})',
            impact=impact
        )
        
        # Get approval if interactive
        if self.interactive and not self.dry_run:
            if not Confirm.ask("Apply this change?", default=False):
                console.print("[yellow]Change skipped[/yellow]")
                self._log_change(device_name, 'Channel Change', 'SKIPPED', 
                               f'{old_channel} → {new_channel}')
                return False
        
        # Apply or simulate
        if self.dry_run:
            console.print(f"[cyan]DRY RUN: Would change channel from {old_channel} to {new_channel}[/cyan]")
            self._log_change(device_name, 'Channel Change', 'DRY-RUN', 
                           f'{old_channel} → {new_channel}')
            return True
        else:
            console.print(f"[yellow]Applying channel change...[/yellow]")
            
            # Build complete radio_table with all existing fields
            # Must include all radios and preserve all existing settings
            updated_radio_table = []
            for r in radio_table:
                radio_entry = dict(r)  # Copy all existing fields
                if r.get('radio') == radio:
                    # Update only the channel for target radio
                    radio_entry['channel'] = new_channel
                updated_radio_table.append(radio_entry)
            
            update_data = {
                'radio_table': updated_radio_table
            }
            
            result = self.client.put(f's/{self.client.site}/rest/device/{device_id}', update_data)
            
            if result:
                console.print(f"[green]✓ Channel changed successfully![/green]")
                self._log_change(device_name, 'Channel Change', 'SUCCESS', 
                               f'{old_channel} → {new_channel}')
                return True
            else:
                console.print(f"[red]✗ Failed to change channel[/red]")
                self._log_change(device_name, 'Channel Change', 'FAILED', 
                               f'{old_channel} → {new_channel}')
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
        device_id = device['_id']
        device_name = device.get('name', 'Unnamed AP')
        
        # Get current power
        radio_table = device.get('radio_table', [])
        current_radio = next((r for r in radio_table if r.get('radio') == radio), None)
        
        if not current_radio:
            console.print(f"[red]Radio {radio} not found on {device_name}[/red]")
            return False
        
        old_power_mode = current_radio.get('tx_power_mode', 'auto')
        radio_band = '2.4GHz' if radio == 'ng' else '5GHz'
        
        # Analyze impact
        impact = self.analyzer.analyze_power_change(device, old_power_mode, new_power_mode, radio_band)
        
        # Display change details
        self._display_change_details(
            device_name=device_name,
            change_type='Power Level Change',
            old_value=f'{old_power_mode} ({radio_band})',
            new_value=f'{new_power_mode} ({radio_band})',
            impact=impact
        )
        
        # Get approval if interactive
        if self.interactive and not self.dry_run:
            if not Confirm.ask("Apply this change?", default=False):
                console.print("[yellow]Change skipped[/yellow]")
                self._log_change(device_name, 'Power Change', 'SKIPPED', 
                               f'{old_power_mode} → {new_power_mode}')
                return False
        
        # Apply or simulate
        if self.dry_run:
            console.print(f"[cyan]DRY RUN: Would change power from {old_power_mode} to {new_power_mode}[/cyan]")
            self._log_change(device_name, 'Power Change', 'DRY-RUN', 
                           f'{old_power_mode} → {new_power_mode}')
            return True
        else:
            console.print(f"[yellow]Applying power change...[/yellow]")
            
            # Build complete radio_table with all existing fields
            # Must include all radios and preserve all existing settings
            updated_radio_table = []
            for r in radio_table:
                radio_entry = dict(r)  # Copy all existing fields
                if r.get('radio') == radio:
                    # Update only the power mode for target radio
                    radio_entry['tx_power_mode'] = new_power_mode
                updated_radio_table.append(radio_entry)
            
            update_data = {
                'radio_table': updated_radio_table
            }
            
            result = self.client.put(f's/{self.client.site}/rest/device/{device_id}', update_data)
            
            if result:
                console.print(f"[green]✓ Power level changed successfully![/green]")
                self._log_change(device_name, 'Power Change', 'SUCCESS', 
                               f'{old_power_mode} → {new_power_mode}')
                return True
            else:
                console.print(f"[red]✗ Failed to change power level[/red]")
                self._log_change(device_name, 'Power Change', 'FAILED', 
                               f'{old_power_mode} → {new_power_mode}')
                return False
    
    def _display_change_details(self, device_name, change_type, old_value, new_value, impact):
        """Display detailed change information"""
        console.print("\n")
        console.print(Panel(
            f"[bold]{change_type}[/bold]\n"
            f"Device: [cyan]{device_name}[/cyan]\n"
            f"Current: [yellow]{old_value}[/yellow]\n"
            f"Proposed: [green]{new_value}[/green]",
            title="Proposed Change",
            border_style="cyan"
        ))
        
        # Impact table
        table = Table(title="Impact Analysis", show_header=True, header_style="bold magenta")
        table.add_column("Category", style="cyan", width=20)
        table.add_column("Details", style="white")
        
        # Severity
        severity_color = {
            'LOW': 'green',
            'MEDIUM': 'yellow',
            'HIGH': 'red'
        }.get(impact['severity'], 'white')
        table.add_row("Severity", f"[{severity_color}]{impact['severity']}[/{severity_color}]")
        
        # Client Impact
        table.add_row("Client Impact", impact['client_impact'])
        
        # Estimated Downtime
        table.add_row("Downtime", impact['estimated_downtime'])
        
        # Affected Clients
        if 'affected_clients' in impact:
            table.add_row("Affected Clients", str(impact['affected_clients']))
        
        console.print(table)
        
        # Benefits
        if impact['benefits']:
            console.print("\n[bold green]Benefits:[/bold green]")
            for benefit in impact['benefits']:
                console.print(f"  ✓ {benefit}")
        
        # Risks
        if impact['risks']:
            console.print("\n[bold yellow]Considerations:[/bold yellow]")
            for risk in impact['risks']:
                console.print(f"  ⚠ {risk}")
        
        console.print()
    
    def _log_change(self, device_name, change_type, status, details):
        """Log a change for later reporting"""
        self.changes_log.append({
            'timestamp': datetime.now().isoformat(),
            'device': device_name,
            'type': change_type,
            'status': status,
            'details': details
        })
    
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
                'SUCCESS': 'green',
                'FAILED': 'red',
                'SKIPPED': 'yellow',
                'DRY-RUN': 'cyan'
            }.get(change['status'], 'white')
            
            table.add_row(
                change['device'],
                change['type'],
                f"[{status_style}]{change['status']}[/{status_style}]",
                change['details']
            )
        
        console.print(table)
        
        # Statistics
        total = len(self.changes_log)
        success = sum(1 for c in self.changes_log if c['status'] == 'SUCCESS')
        failed = sum(1 for c in self.changes_log if c['status'] == 'FAILED')
        skipped = sum(1 for c in self.changes_log if c['status'] == 'SKIPPED')
        dry_run = sum(1 for c in self.changes_log if c['status'] == 'DRY-RUN')
        
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
            with open(log_file, 'w') as f:
                json.dump(self.changes_log, f, indent=2)
            console.print(f"\n[dim]Change log saved to: {log_file}[/dim]")


def main():
    """Example usage"""
    from api.cloudkey_gen2_client import CloudKeyGen2Client, get_devices
    
    # Example: Create client and applier
    client = CloudKeyGen2Client(
        host='https://192.168.1.119',
        username='audit',
        password='password',
        site='default'
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
    ap = next((d for d in devices if d.get('type') == 'uap'), None)
    if not ap:
        console.print("[red]No access points found[/red]")
        return
    
    # Create applier in dry-run mode
    console.print("[bold]DRY RUN MODE - No actual changes will be made[/bold]\n")
    applier = ChangeApplier(client, dry_run=True, interactive=False)
    
    # Simulate some changes
    applier.apply_channel_change(ap, 'ng', 6)
    applier.apply_power_change(ap, 'ng', 'medium')
    
    # Generate report
    applier.generate_report()


if __name__ == '__main__':
    main()
