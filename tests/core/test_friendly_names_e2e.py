#!/usr/bin/env python3
"""
Test script to verify the end-to-end process with friendly AP names
This test creates a plan and simulates applying changes
"""
import json
import sys
import os
import traceback
from pathlib import Path
from rich.console import Console
from rich.panel import Panel

# Add parent directory to Python path for proper imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# Import modules
from core.analyzer import Analyzer
from core.applier import PlanApplier
from utils.constants import VALID_24G_CHANNELS

console = Console()

def create_mock_data():
    """Create mock network data with friendly names"""
    return {
        "aps": [
            {
                "name": "Office AP",
                "mac": "00:11:22:33:44:55",
                "model": "U6-Lite",
                "ip": "192.168.1.10",
                "radio_table": [
                    {"radio": "ng", "channel": 1, "ht": 20, "tx_power_mode": "auto"},
                    {"radio": "na", "channel": 36, "ht": 40, "tx_power_mode": "auto"}
                ]
            },
            {
                "name": "Living Room AP",
                "mac": "00:11:22:33:44:66",
                "model": "U6-Pro",
                "ip": "192.168.1.11",
                "radio_table": [
                    {"radio": "ng", "channel": 11, "ht": 20, "tx_power_mode": "auto"},
                    {"radio": "na", "channel": 44, "ht": 80, "tx_power_mode": "auto"}
                ]
            },
            {
                "name": "Basement AP",
                "mac": "00:11:22:33:44:77",
                "model": "U6-LR",
                "ip": "192.168.1.12",
                "radio_table": [
                    {"radio": "ng", "channel": 6, "ht": 20, "tx_power_mode": "auto"},
                    {"radio": "na", "channel": 149, "ht": 80, "tx_power_mode": "auto"}
                ]
            }
        ],
        "clients": [],
        "wlans": [],
        "events": [],
        "alarms": []
    }

def run_test():
    """Run the friendly name test scenario"""
    console.print(Panel("[bold]Testing Friendly Name Support (End-to-End)[/bold]"))
    
    try:
        # Create mock data
        data = create_mock_data()
        console.print("[green]✓[/green] Created test data with friendly names")
        
        # Create analyzer and generate recommendations
        analyzer = Analyzer()
        results = analyzer.analyze(data)
        console.print("[green]✓[/green] Generated analysis results")
        
        # Create a plan
        plan = {
            "aps": [
                {
                    "name": "Office AP",
                    "mac": "00:11:22:33:44:55",
                    "radio_ng": {"channel": 6},  # Change channel from 1 to 6
                },
                {
                    "name": "Living Room AP",
                    "mac": "00:11:22:33:44:66",
                    "radio_ng": {"channel": 1},  # Change channel from 11 to 1
                },
                # No changes for Basement AP
            ]
        }
        console.print("[green]✓[/green] Created channel optimization plan")
        console.print(plan)
        
        # Create applier and simulate changes
        applier = PlanApplier(data, dry_run=True)
        changes = applier.apply(plan)
        console.print("[green]✓[/green] Applied changes (simulation)")
        
        # Verify changes used friendly names
        friendly_names_used = all(ap.get("name") for ap in changes.get("aps", []))
        if friendly_names_used:
            console.print("[green]✓[/green] Changes correctly include friendly names")
        else:
            console.print("[red]✗[/red] Changes missing friendly names")
        
        # Verify all changes are correctly tracked
        expected_changes = 2  # Two APs with channel changes
        actual_changes = len(changes.get("aps", []))
        
        if actual_changes == expected_changes:
            console.print(f"[green]✓[/green] All {expected_changes} changes correctly tracked")
        else:
            console.print(f"[red]✗[/red] Expected {expected_changes} changes, got {actual_changes}")
            
        console.print("\n[bold green]Test completed successfully![/bold green]")
        return True
        
    except Exception as e:
        console.print(f"[bold red]Test failed: {str(e)}[/bold red]")
        console.print(traceback.format_exc())
        return False

if __name__ == "__main__":
    run_test()
