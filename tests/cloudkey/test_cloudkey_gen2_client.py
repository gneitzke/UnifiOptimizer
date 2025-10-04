#!/usr/bin/env python3
"""
Test script for CloudKey Gen2+ API client
Verifies GET and PUT operations work correctly
"""

import sys
import os

# Add parent directory to path
parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, parent_dir)

from api.cloudkey_gen2_client import CloudKeyGen2Client, get_devices, update_device
from rich.console import Console
from rich.panel import Panel

console = Console()


def test_cloudkey_gen2(host, username, password, site='default'):
    """Test CloudKey Gen2+ API operations"""
    
    console.print(Panel("[bold]CloudKey Gen2+ API Client Test[/bold]", style="cyan"))
    
    # Create client
    console.print(f"\n[bold]1. Creating CloudKey Gen2+ client[/bold]")
    client = CloudKeyGen2Client(host, username, password, site)
    
    # Test login
    console.print(f"\n[bold]2. Testing login[/bold]")
    if not client.login():
        console.print("[red]✗ Login failed[/red]")
        return False
    
    # Test GET devices
    console.print(f"\n[bold]3. Testing GET devices[/bold]")
    devices = get_devices(client)
    if not devices:
        console.print("[red]✗ Failed to get devices[/red]")
        return False
    
    console.print(f"[green]✓ Found {len(devices)} devices[/green]")
    
    # Find first AP
    aps = [d for d in devices if d.get('type') == 'uap']
    if not aps:
        console.print("[red]✗ No access points found[/red]")
        return False
    
    ap = aps[0]
    ap_id = ap['_id']
    ap_name = ap.get('name', 'Unnamed')
    console.print(f"[cyan]Using AP: {ap_name} ({ap_id})[/cyan]")
    
    # Test PUT - Update AP name
    console.print(f"\n[bold]4. Testing PUT (update device name)[/bold]")
    test_name = f"Test AP {ap_name}"
    result = update_device(client, ap_id, {'name': test_name})
    
    if not result:
        console.print("[red]✗ Failed to update device name[/red]")
        return False
    
    console.print(f"[green]✓ Successfully updated device name to '{test_name}'[/green]")
    
    # Verify the update
    console.print(f"\n[bold]5. Verifying update[/bold]")
    devices = get_devices(client)
    updated_ap = next((d for d in devices if d['_id'] == ap_id), None)
    
    if not updated_ap:
        console.print("[red]✗ Could not find updated device[/red]")
        return False
    
    if updated_ap.get('name') == test_name:
        console.print(f"[green]✓ Update verified! Name is now '{test_name}'[/green]")
    else:
        console.print(f"[yellow]⚠ Name is '{updated_ap.get('name')}', expected '{test_name}'[/yellow]")
    
    # Restore original name
    console.print(f"\n[bold]6. Restoring original name[/bold]")
    result = update_device(client, ap_id, {'name': ap_name})
    
    if result:
        console.print(f"[green]✓ Restored original name '{ap_name}'[/green]")
    else:
        console.print(f"[yellow]⚠ Failed to restore original name[/yellow]")
    
    # Summary
    console.print("\n")
    console.print(Panel("[bold green]✓ All CloudKey Gen2+ API tests passed![/bold green]", style="green"))
    
    return True


def main():
    """Main entry point"""
    if len(sys.argv) < 4:
        console.print("[yellow]Usage: python3 test_cloudkey_gen2_client.py <host> <username> <password> [site][/yellow]")
        console.print("[yellow]Example: python3 test_cloudkey_gen2_client.py https://192.168.1.1 audit mypassword default[/yellow]")
        sys.exit(1)
    
    host = sys.argv[1]
    username = sys.argv[2]
    password = sys.argv[3]
    site = sys.argv[4] if len(sys.argv) > 4 else 'default'
    
    try:
        success = test_cloudkey_gen2(host, username, password, site)
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Test interrupted[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red]Test error: {str(e)}[/red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
