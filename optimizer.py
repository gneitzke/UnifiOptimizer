#!/usr/bin/env python3
"""
UniFi Network Optimizer - CLI Entry Point

Two modes:
  analyze  - Full network analysis + HTML report (read-only, safe)
  optimize - Apply recommended changes (use --dry-run to preview)
"""

import subprocess
import sys

from version import __version__


def main():
    """Route to core/optimize_network.py with all arguments"""
    # Version flag
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-V"):
        print(f"UnifiOptimizer v{__version__}")
        return 0

    # If no arguments, show help
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(f"""UniFi Network Optimizer v{__version__}

Usage:
  python3 optimizer.py analyze  [options]   Full analysis + report (safe, read-only)
  python3 optimizer.py optimize [options]   Apply changes (--dry-run to preview)

Options:
  --host URL           Controller URL (e.g., https://192.168.1.1)
  --username USER      Username (default: audit)
  --password PASS      Password (will prompt if not provided)
  --site SITE          Site name (default: default)
  --profile NAME       Use saved profile
  --lookback DAYS      Days of historical data (default: 3)
  --dry-run            Preview changes without applying
  --yes                Apply all changes without prompts
  --min-rssi-strategy  optimal | max_connectivity
  --verbose            Show detailed API debugging
  --list-profiles      List saved profiles
  --save-profile NAME  Save credentials as named profile

Examples:
  python3 optimizer.py analyze --host https://192.168.1.1 --username audit
  python3 optimizer.py analyze --profile default
  python3 optimizer.py optimize --profile default --dry-run
  python3 optimizer.py optimize --profile default
""")
        return 0

    args = sys.argv[1:]

    # If no subcommand given (first arg starts with -), infer one:
    #   --dry-run present → optimize --dry-run
    #   otherwise         → analyze (safe default)
    if args[0].startswith("-"):
        if "--dry-run" in args:
            args = ["optimize"] + args
        else:
            args = ["analyze"] + args

    # Forward all arguments to core/optimize_network.py
    cmd = ["python3", "core/optimize_network.py"] + args
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nOperation cancelled")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback

        traceback.print_exc()
