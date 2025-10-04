#!/usr/bin/env python3
"""
Test to verify friendly AP names are used when applying changes
"""
import json
import sys
from pathlib import Path

# Add parent directory to path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent))

# Attempt to import needed modules
try:
    from rich.console import Console
    console = Console()
except ImportError:
    # Fallback to simple print if rich isn't available
    class SimpleConsole:
        def print(self, *args, **kwargs):
            print(*args)
    console = SimpleConsole()

try:
    from core.applier import apply_from_plan
    APPLY_AVAILABLE = True
except ImportError:
    console.print("[yellow]Warning: DeepAnalyse/applier module not available[/yellow]")
    APPLY_AVAILABLE = False

def test_apply_display(plan_file):
    """Test the display of AP names during plan application"""
    if not APPLY_AVAILABLE:
        console.print("[red]Error: apply_from_plan function not available[/red]")
        return False
    
    console.print(f"[bold blue]Testing apply display with plan: {plan_file}[/bold blue]")
    
    # Load the plan
    try:
        with open(plan_file, 'r') as f:
            plan = json.load(f)
    except Exception as e:
        console.print(f"[red]Error loading plan: {e}[/red]")
        return False
    
    # Check if plan has devices
    if 'devices' not in plan or not plan['devices']:
        console.print("[yellow]Warning: Plan does not contain any devices[/yellow]")
        return False
    
    # Count devices and changes
    device_count = len(plan['devices'])
    
    change_count = 0
    for device in plan['devices']:
        if 'changes' in device and device['changes']:
            change_count += len(device['changes'])
    
    console.print(f"Plan contains {device_count} devices with {change_count} total changes")
    
    # Check if devices have names
    named_devices = 0
    for device in plan['devices']:
        if 'name' in device and device['name']:
            named_devices += 1
    
    console.print(f"Plan has {named_devices}/{device_count} devices with names")
    
    # Simulate apply (dry run)
    console.print("\n[bold]Testing apply display (dry run):[/bold]")
    apply_from_plan(plan, dry_run=True)
    
    return True

def main():
    """Main entry point"""
    console.print("[bold blue]==================================[/bold blue]")
    console.print("[bold blue]Apply Display Test[/bold blue]")
    console.print("[bold blue]==================================[/bold blue]")
    
    # Get plan file
    if len(sys.argv) > 1:
        plan_file = sys.argv[1]
    else:
        # Look for plan files in current directory
        plan_files = list(Path('.').glob('plan-*.json'))
        if not plan_files:
            console.print("[red]No plan files found. Please specify a plan file.[/red]")
            return
        
        # Sort by modification time (newest first)
        plan_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        
        # Use the most recent plan
        plan_file = str(plan_files[0])
        console.print(f"Using most recent plan file: {plan_file}")
    
    test_apply_display(plan_file)

if __name__ == "__main__":
    main()
