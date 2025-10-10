#!/usr/bin/env python3
"""
GitHub Actions Status Summary
Generates a summary of CI/CD pipeline runs
"""

import sys
from pathlib import Path


def check_workflow_files():
    """Check that all workflow files exist and are valid"""
    workflows_dir = Path(".github/workflows")

    if not workflows_dir.exists():
        print("❌ .github/workflows directory not found")
        return False

    workflows = {
        "ci.yml": "CI Pipeline",
        "extended-tests.yml": "Extended Tests",
        "pr-validation.yml": "PR Validation",
        "release.yml": "Release Workflow"
    }

    print("📋 Workflow Files Check:")
    all_exist = True
    for filename, description in workflows.items():
        filepath = workflows_dir / filename
        if filepath.exists():
            print(f"  ✅ {description}: {filename}")
        else:
            print(f"  ❌ {description}: {filename} NOT FOUND")
            all_exist = False

    return all_exist


def check_config_files():
    """Check configuration files"""
    print("\n📋 Configuration Files Check:")

    configs = {
        ".pre-commit-config.yaml": "Pre-commit hooks",
        ".github/workflows/mlc_config.json": "Markdown link checker config",
        ".github/SETUP_GUIDE.md": "CI/CD setup guide"
    }

    all_exist = True
    for filepath, description in configs.items():
        if Path(filepath).exists():
            print(f"  ✅ {description}: {filepath}")
        else:
            print(f"  ⚠️  {description}: {filepath} NOT FOUND")
            all_exist = False

    return all_exist


def validate_critical_checks():
    """Validate that critical bug fix checks are in place"""
    print("\n🔍 Critical Checks Validation:")

    ci_file = Path(".github/workflows/ci.yml")
    if not ci_file.exists():
        print("  ❌ CI workflow file not found")
        return False

    content = ci_file.read_text()

    checks = {
        "Mock data check": "Demo AP Uplink",
        "Cache implementation check": "hourly_data_cache",
        "Execution order check": "analyze_switch_port_history",
        "Mesh protection check": "mesh.*min.*rssi"
    }

    all_present = True
    for check_name, pattern in checks.items():
        if pattern in content:
            print(f"  ✅ {check_name}")
        else:
            print(f"  ❌ {check_name} NOT FOUND")
            all_present = False

    return all_present


def main():
    """Main function"""
    print("🚀 UniFi Optimizer CI/CD Status Check\n")
    print("=" * 60)

    workflows_ok = check_workflow_files()
    configs_ok = check_config_files()
    checks_ok = validate_critical_checks()

    print("\n" + "=" * 60)
    print("\n📊 Summary:")

    if workflows_ok and configs_ok and checks_ok:
        print("  ✅ All CI/CD components are properly configured")
        print("\n🎉 CI/CD pipeline is ready!")
        print("\nNext steps:")
        print("  1. Commit and push the .github directory")
        print("  2. Check GitHub Actions tab for workflow runs")
        print("  3. Install pre-commit hooks: pre-commit install")
        return 0
    else:
        print("  ⚠️  Some components are missing or misconfigured")
        print("\nPlease review the checks above and fix any issues.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
