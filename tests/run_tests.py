#!/usr/bin/env python3
"""
Test runner for UniFi Audit Tool
This script runs all the unit tests for the project.
"""
import unittest
import sys
import os
import argparse
from pathlib import Path

# Add the parent directory to sys.path so we can import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def show_test_banner():
    """Display ASCII art banner for test runner."""
    banner = """
    ╔═══════════════════════════════════════════════════════════════╗
    ║                                                               ║
    ║  🧪 UniFi Audit Test Laboratory 🧪                          ║
    ║                                                               ║
    ║     🔬 Running Diagnostics...                                ║
    ║     📊 Validating Functions...                               ║
    ║     ⚡ Testing Network Logic...                              ║
    ║     🔍 Checking Authentication...                            ║
    ║                                                               ║
    ║        Ensuring Your Network Doctor is Healthy! 🩺          ║
    ║                                                               ║
    ╚═══════════════════════════════════════════════════════════════╝
    """
    print(banner)

def show_test_results(success, total_tests):
    """Display ASCII art for test results."""
    if success:
        art = f"""
    ╔═══════════════════════════════════════════════════════════════╗
    ║                                                               ║
    ║   🎉 ALL TESTS PASSED! 🎉                                   ║
    ║                                                               ║
    ║   ✅ {total_tests} tests completed successfully                        ║
    ║   ✅ Network Doctor is healthy and ready!                    ║
    ║   ✅ UniFi toolkit functions verified                        ║
    ║                                                               ║
    ║        Ready to diagnose your network! 🚀                   ║
    ║                                                               ║
    ╚═══════════════════════════════════════════════════════════════╝
        """
    else:
        art = f"""
    ╔═══════════════════════════════════════════════════════════════╗
    ║                                                               ║
    ║   ⚠️  SOME TESTS FAILED ⚠️                                  ║
    ║                                                               ║
    ║   🔧 Please check the test output above                      ║
    ║   🔧 Fix any issues before deploying                         ║
    ║                                                               ║
    ║        The Network Doctor needs attention! 🩺               ║
    ║                                                               ║
    ╚═══════════════════════════════════════════════════════════════╝
        """
    print(art)

def run_tests(category=None):
    """
    Run tests in the tests directory
    
    Args:
        category: Optional category of tests to run (e.g., 'cloudkey', 'auth')
    """
    show_test_banner()
    
    loader = unittest.TestLoader()
    start_dir = Path(__file__).parent
    
    if category:
        # Run tests in a specific category
        if category == 'cloudkey':
            start_dir = start_dir / 'cloudkey'
        elif category == 'auth':
            # For auth tests, use a pattern to match auth-related test files
            pattern = "test_auth*.py"
        else:
            print(f"Unknown test category: {category}")
            print("Available categories: cloudkey, auth")
            return False
    
    # Default pattern matches all test files
    pattern = "test_*.py"
    
    # Discover and run the tests
    suite = loader.discover(str(start_dir), pattern=pattern)
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Count total tests
    total_tests = result.testsRun
    success = result.wasSuccessful()
    
    show_test_results(success, total_tests)
    
    return success

def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='Run UniFi Audit Tool tests')
    parser.add_argument('--category', type=str, help='Category of tests to run (e.g., cloudkey, auth)')
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_arguments()
    success = run_tests(args.category)
    sys.exit(0 if success else 1)
