#!/usr/bin/env python3
"""
UniFi Network Optimizer - Interactive Menu

Comprehensive network optimization tool with multiple analysis and apply modes.
"""

import sys
import subprocess
# from getpass import getpass  # Disabled - showing password for easier input
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from api.cloudkey_gen2_client import CloudKeyGen2Client

console = Console()


def validate_credentials(host, username, password, site):
    """Validate credentials by attempting to login"""
    try:
        console.print("\n[dim]Validating credentials...[/dim]")
        client = CloudKeyGen2Client(host, username, password, site=site, verify_ssl=False)
        # Try to get basic info to confirm connection works
        client.get(f's/{site}/stat/device')
        console.print("[green]✓ Connection successful![/green]\n")
        return True
    except Exception as e:
        console.print(f"[red]✗ Login failed: {e}[/red]")
        return False


def run_expert_analysis(host, username, password, site):
    """Run complete expert analysis including client health"""
    console.print(Panel(
        "[bold green]EXPERT ANALYSIS[/bold green]\n"
        "Complete network assessment including:\n"
        "  • Client health diagnostics (RSSI, signal quality, disconnects)\n"
        "  • 3-day historical analysis\n"
        "  • Mesh AP reliability checks\n"
        "  • Channel and power optimization\n"
        "  • Disconnection pattern analysis\n"
        "  • Executive summary of findings and impact\n\n"
        "NO changes will be made to your network.",
        style="green"
    ))
    
    if not Confirm.ask("\nProceed with expert analysis?", default=True):
        return
    
    console.print("\n[dim]Running analysis modules...[/dim]\n")
    
    # Run analyze mode which includes everything
    cmd = [
        'python3', 'core/optimize_network.py', 'analyze',
        '--host', host,
        '--username', username,
        '--password', password,
        '--site', site,
        '--lookback', '3'
    ]
    subprocess.run(cmd)


def show_menu():
    """Show main menu"""
    console.print(Panel(
        "[bold cyan]UniFi Network Optimizer[/bold cyan]\n\n"
        "Comprehensive network analysis and optimization tool.\n"
        "Choose a mode to optimize your network:",
        title="Main Menu",
        border_style="cyan"
    ))
    
    console.print("\n[bold]Available Modes:[/bold]\n")
    console.print("[green]1. Expert Analysis[/green]")
    console.print("   Complete network assessment: Client health, RSSI, mesh reliability,")
    console.print("   channel/power optimization. 3-day lookback with executive summary.\n")
    
    console.print("[cyan]2. Dry-Run[/cyan]")
    console.print("   Simulate changes without applying them. Shows what would change")
    console.print("   with detailed impact analysis. Safe preview mode.\n")
    
    console.print("[yellow]3. Analyze and Apply[/yellow]")
    console.print("   Run full analysis, review findings, then apply changes.")
    console.print("   Choose: Apply all at once OR review each change individually.\n")
    
    console.print("[dim]4. Exit[/dim]\n")





def run_dry_run(host, username, password, site):
    """Run dry-run mode"""
    console.print(Panel(
        "[bold cyan]DRY-RUN MODE - Safe Change Simulation[/bold cyan]\n"
        "This will simulate applying changes and show detailed impact.\n"
        "NO actual changes will be made to your network.",
        style="cyan"
    ))
    
    if not Confirm.ask("\nProceed with dry-run?", default=True):
        return
    
    console.print("\n[dim]Running: core/optimize_network.py apply --dry-run --lookback 3[/dim]\n")
    
    import subprocess
    cmd = [
        'python3', 'core/optimize_network.py', 'apply',
        '--host', host,
        '--username', username,
        '--password', password,
        '--site', site,
        '--dry-run',
        '--lookback', '3'
    ]
    subprocess.run(cmd)


def run_analyze_and_apply(host, username, password, site):
    """Run full analysis then offer apply options"""
    console.print(Panel(
        "[bold yellow]ANALYZE AND APPLY[/bold yellow]\n"
        "This will:\n"
        "  1. Run complete expert analysis (client health + network assessment)\n"
        "  2. Show executive summary of findings and impact\n"
        "  3. Let you choose: Apply all at once OR review each change",
        style="yellow"
    ))
    
    if not Confirm.ask("\nProceed with analysis?", default=True):
        return
    
    console.print("\n[dim]Running complete analysis with 3-day lookback...[/dim]\n")
    
    # Run apply mode which shows analysis + executive summary first
    cmd = [
        'python3', 'core/optimize_network.py', 'apply',
        '--host', host,
        '--username', username,
        '--password', password,
        '--site', site,
        '--dry-run',
        '--lookback', '3'
    ]
    subprocess.run(cmd)
    
    # Ask what to do next
    console.print("\n" + "="*60)
    console.print(Panel(
        "[bold]Analysis complete! Ready to apply changes?[/bold]\n\n"
        "1. Apply all changes at once\n"
        "2. Review and approve each change individually\n"
        "3. Cancel (no changes)",
        style="yellow"
    ))
    
    choice = input("\nSelect option (1-3): ").strip()
    
    if choice == '1':
        console.print("\n[bold red]⚠️  WARNING:[/bold red]")
        console.print("This will apply ALL recommended changes automatically.")
        console.print("Changes will cause brief client disconnections (5-10 seconds each).\n")
        
        if not Confirm.ask("Apply all changes now?", default=False):
            console.print("[green]Cancelled. No changes made.[/green]")
            return
        
        console.print("\n[dim]Applying all changes...[/dim]\n")
        cmd = [
            'python3', 'core/optimize_network.py', 'apply',
            '--host', host,
            '--username', username,
            '--password', password,
            '--site', site,
            '--lookback', '3',
            '--yes'
        ]
        subprocess.run(cmd)
        
    elif choice == '2':
        console.print("\n[bold yellow]INTERACTIVE MODE[/bold yellow]")
        console.print("You will be asked to approve each change individually.\n")
        
        console.print("\n[dim]Starting interactive apply...[/dim]\n")
        cmd = [
            'python3', 'core/optimize_network.py', 'apply',
            '--host', host,
            '--username', username,
            '--password', password,
            '--site', site,
            '--interactive',
            '--lookback', '3'
        ]
        subprocess.run(cmd)
        
    else:
        console.print("[green]Cancelled. No changes made.[/green]")


def main():
    """Main interactive loop"""
    console.print("\n[bold]UniFi Network Optimizer[/bold]\n")
    
    # Get connection details
    console.print("Connect to your UniFi Controller:\n")
    
    host = input("Controller URL (e.g., https://192.168.1.1): ").strip()
    if not host:
        console.print("[yellow]No host provided. Please enter your UniFi controller IP address.[/yellow]")
        return
    
    username = input("Username (audit): ").strip()
    if not username:
        username = "audit"
    
    # Validate credentials with retry logic
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        password = input("Password: ")  # Visible password input
        
        site = input("Site name (default): ").strip()
        if not site:
            site = "default"
        
        # Validate credentials
        if validate_credentials(host, username, password, site):
            break
        
        if attempt < max_attempts:
            console.print(f"\n[yellow]Attempt {attempt}/{max_attempts} failed. Please try again.[/yellow]\n")
        else:
            console.print(f"\n[red]Failed to authenticate after {max_attempts} attempts.[/red]")
            console.print("[yellow]Please check your credentials and try again.[/yellow]\n")
            return
    
    while True:
        show_menu()
        
        choice = input("Select mode (1-4): ").strip()
        
        if choice == '1':
            run_expert_analysis(host, username, password, site)
        elif choice == '2':
            run_dry_run(host, username, password, site)
        elif choice == '3':
            run_analyze_and_apply(host, username, password, site)
        elif choice == '4':
            console.print("\n[green]Optimization complete![/green]")
            console.print("[dim]Tip: Start with Expert Analysis, then use Analyze and Apply for changes.[/dim]\n")
            break
        else:
            console.print("[red]Invalid choice. Please select 1-4.[/red]")
        
        console.print()
        if not Confirm.ask("Return to menu?", default=True):
            break


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Operation cancelled[/yellow]")
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        import traceback
        traceback.print_exc()
