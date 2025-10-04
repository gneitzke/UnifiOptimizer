#!/usr/bin/env python3
"""
Test script for session management functionality
"""

import sys
import requests
from pathlib import Path
from rich.console import Console

# Add parent directory to path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    # Import session manager functions
    from api.auth_manager import get_session, refresh_session, clear_session
    SESSION_MANAGER_AVAILABLE = True
except ImportError as e:
    console = Console()
    console.print(f"[yellow]Session manager not available: {e}[/yellow]")
    SESSION_MANAGER_AVAILABLE = False

console = Console()

def test_session_management(host):
    """Test session caching and reuse"""
    if not SESSION_MANAGER_AVAILABLE:
        console.print("[red]Session manager module not available. Cannot run test.[/red]")
        return

    console.print("[bold blue]Testing Session Management[/bold blue]")
    
    # Make sure we start fresh
    console.print("Clearing any existing sessions...")
    clear_session(host)
    
    # First login should require credentials
    console.print("\n[bold]Test 1: First login should prompt for credentials[/bold]")
    console.print("Getting session (should ask for credentials)...")
    session1 = get_session(host)
    
    if not session1:
        console.print("[red]Failed to get initial session[/red]")
        return
        
    # Check if we have cookies
    if len(session1.cookies) > 0:
        console.print("[green]✓ Session has cookies[/green]")
    else:
        console.print("[red]✗ Session has no cookies[/red]")
        return
        
    # Save session ID for later comparison
    session1_id = id(session1)
    
    # Second login should reuse the session
    console.print("\n[bold]Test 2: Second login should reuse cached session[/bold]")
    console.print("Getting session again (should use cached session)...")
    session2 = get_session(host)
    
    if id(session2) == session1_id:
        console.print("[green]✓ Session was reused from cache[/green]")
    else:
        console.print("[red]✗ New session was created instead of using cache[/red]")
        
    # Test session refresh
    console.print("\n[bold]Test 3: Session refresh[/bold]")
    console.print("Refreshing session...")
    refreshed = refresh_session(host)
    
    if refreshed:
        console.print("[green]✓ Session refreshed successfully[/green]")
    else:
        console.print("[yellow]! Session refresh returned False[/yellow]")
        
    # Test session after refresh
    console.print("\n[bold]Test 4: Get session after refresh[/bold]")
    console.print("Getting session after refresh...")
    session3 = get_session(host)
    
    if session3:
        console.print("[green]✓ Got session after refresh[/green]")
    else:
        console.print("[red]✗ Failed to get session after refresh[/red]")
        
    # Test session clearing
    console.print("\n[bold]Test 5: Clear session[/bold]")
    console.print("Clearing session...")
    clear_session(host)
    
    # Final login should require credentials again
    console.print("\n[bold]Test 6: Login after clearing should prompt for credentials again[/bold]")
    console.print("Getting session after clearing (should ask for credentials again)...")
    session4 = get_session(host, prompt_if_needed=False)
    
    if session4 is None:
        console.print("[green]✓ Session was cleared successfully (requires login again)[/green]")
    else:
        console.print("[red]✗ Session was not properly cleared[/red]")
        
    console.print("\n[bold green]Session Management Tests Complete![/bold green]")

def main():
    """Main entry point"""
    console.print("[bold blue]==================================[/bold blue]")
    console.print("[bold blue]Session Management Test[/bold blue]")
    console.print("[bold blue]==================================[/bold blue]")
    
    if not SESSION_MANAGER_AVAILABLE:
        console.print("[red]Session manager module not available. Cannot run test.[/red]")
        return
    
    # Get host
    host = input("Enter controller hostname or IP (e.g., 192.168.1.1:8443): ")
    
    # Run tests
    test_session_management(host)

if __name__ == "__main__":
    main()
