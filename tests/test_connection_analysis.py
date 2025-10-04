#!/usr/bin/env python3
"""
Verify the enhanced connection log analysis functionality
"""
import json
import sys
from pathlib import Path
from rich.console import Console

# Add parent directory to path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import our modules
from core.analyzer import Analyzer
from utils.rssi import analyze_connection_logs

console = Console()

def load_test_data(file_path):
    """Load test data from JSON file."""
    try:
        with open(file_path, 'r') as file:
            return json.load(file)
    except Exception as e:
        console.print(f"[red]Error loading test data: {e}[/red]")
        return None

def run_test_case(test_data, case_name):
    """Run analysis on a specific test case."""
    console.print(f"[bold blue]Running test case: {case_name}[/bold blue]")
    
    if "connections" not in test_data or not test_data["connections"]:
        console.print("[red]Error: Test data missing 'connections' array[/red]")
        return False
    
    # Run the analysis
    results = analyze_connection_logs(test_data["connections"])
    
    # Check expected results if provided
    if "expected" in test_data:
        expected = test_data["expected"]
        
        # Check for expected issues count
        if "issue_count" in expected:
            actual_issues = len(results.get("issues", []))
            expected_issues = expected["issue_count"]
            
            if actual_issues == expected_issues:
                console.print(f"[green]✓ Found expected number of issues: {actual_issues}[/green]")
            else:
                console.print(f"[red]✗ Issue count mismatch: Expected {expected_issues}, found {actual_issues}[/red]")
                return False
        
        # Check for specific issue types
        if "issue_types" in expected:
            actual_types = [issue["type"] for issue in results.get("issues", [])]
            expected_types = expected["issue_types"]
            
            # Check if all expected types are found
            all_found = True
            for exp_type in expected_types:
                if exp_type not in actual_types:
                    console.print(f"[red]✗ Missing expected issue type: {exp_type}[/red]")
                    all_found = False
            
            if all_found:
                console.print(f"[green]✓ Found all expected issue types[/green]")
            else:
                return False
    
    # Display results summary
    console.print(f"Analysis completed with {len(results.get('issues', []))} issues found")
    
    # Display the first few issues if any
    issues = results.get("issues", [])
    if issues:
        console.print("[bold]Sample issues found:[/bold]")
        for i, issue in enumerate(issues[:3]):  # Show first 3 issues
            console.print(f"  {i+1}. {issue['type']}: {issue['description']}")
        
        if len(issues) > 3:
            console.print(f"  ... and {len(issues) - 3} more issues")
    
    console.print(f"[green]✓ Test case {case_name} completed successfully[/green]")
    return True

def main():
    """Main entry point for verification script."""
    console.print("[bold blue]======================================[/bold blue]")
    console.print("[bold blue]Connection Analysis Verification Tool[/bold blue]")
    console.print("[bold blue]======================================[/bold blue]")
    
    # Check for test data file path
    if len(sys.argv) > 1:
        test_file = sys.argv[1]
    else:
        # Default to test data file in the same directory
        test_file = Path(__file__).parent / "data" / "connection_test_data.json"
    
    console.print(f"Loading test data from: {test_file}")
    
    # Load the test data
    test_data = load_test_data(test_file)
    if not test_data:
        sys.exit(1)
    
    # Check if it's a collection of test cases
    if "test_cases" in test_data:
        # Run each test case
        all_passed = True
        for name, case_data in test_data["test_cases"].items():
            result = run_test_case(case_data, name)
            all_passed = all_passed and result
        
        # Final result
        if all_passed:
            console.print("[bold green]All test cases passed![/bold green]")
        else:
            console.print("[bold red]Some test cases failed.[/bold red]")
            sys.exit(1)
    else:
        # Single test case
        result = run_test_case(test_data, "default")
        if not result:
            sys.exit(1)

if __name__ == "__main__":
    main()
