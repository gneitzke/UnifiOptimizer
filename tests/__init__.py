"""
Test runner module for UniFi Audit tests.
This module provides functions for running the different test types:
- Unit tests: Testing individual functions and components
- Integration tests: Testing how components work together
- CloudKey compatibility tests: Testing with CloudKey Gen1 and Gen2 controllers
"""

import unittest
import sys
import os
from rich.console import Console

console = Console()

def run_unit_tests():
    """
    Run all unit tests in the tests directory.
    
    Returns:
        bool: True if all tests passed, False otherwise
    """
    console.print("[bold]Running unit tests...[/bold]")
    
    # Ensure tests directory is in path
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    
    # Find all test modules
    test_loader = unittest.TestLoader()
    test_suite = test_loader.discover(os.path.dirname(__file__), pattern="test_*.py")
    
    # Run tests
    test_runner = unittest.TextTestRunner(verbosity=2)
    result = test_runner.run(test_suite)
    
    # Return True if all tests passed
    return result.wasSuccessful()

def run_cloudkey_tests(host, username, password, site="default"):
    """
    Run CloudKey compatibility tests against a live controller.
    
    Args:
        host: Controller hostname or IP
        username: Login username
        password: Login password
        site: Site name (default: "default")
        
    Returns:
        dict: Test results
    """
    console.print("[bold]Running CloudKey compatibility tests...[/bold]")
    
    # Import test modules
    from tests.cloudkey import test_cloudkey_auth, test_cloudkey_api
    
    results = {
        "auth_test": None,
        "api_test": None,
        "overall_success": False
    }
    
    # Run authentication test
    console.print("[cyan]Testing CloudKey authentication...[/cyan]")
    auth_success = test_cloudkey_auth(host, username, password, site)
    results["auth_test"] = {
        "success": auth_success,
        "message": "Authentication successful" if auth_success else "Authentication failed"
    }
    
    # If authentication succeeded, run API test
    if auth_success:
        console.print("[cyan]Testing CloudKey API...[/cyan]")
        from api.cloudkey_compat import setup_cloudkey_session, test_cloudkey_api
        
        session, _, base_url = setup_cloudkey_session(host, username, password, site)
        if session:
            api_results = test_cloudkey_api(session, base_url, site)
            results["api_test"] = api_results
            results["overall_success"] = api_results.get("success", False)
        else:
            results["api_test"] = {
                "success": False,
                "message": "Could not establish session for API test"
            }
    
    return results

def run_integration_tests():
    """
    Run integration tests that verify components work together.
    
    Returns:
        bool: True if all tests passed, False otherwise
    """
    console.print("[bold]Running integration tests...[/bold]")
    
    # Import integration test modules
    from tests.test_integration import run_integration_tests
    
    return run_integration_tests()

if __name__ == "__main__":
    # If run directly, run all unit tests
    success = run_unit_tests()
    sys.exit(0 if success else 1)
