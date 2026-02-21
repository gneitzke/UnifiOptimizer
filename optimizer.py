#!/usr/bin/env python3
"""
UniFi Network Optimizer - CLI Entry Point

Two modes:
  analyze  - Full network analysis + HTML report (read-only, safe)
  optimize - Apply recommended changes (use --dry-run to preview)
"""

import os
import subprocess
import sys

from version import __version__


def _run_ask(args):
    """Handle the 'ask' subcommand — no controller connection required."""
    import argparse

    # Add project root to path so core/ and utils/ are importable
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    parser = argparse.ArgumentParser(
        prog="optimizer.py ask",
        description="Ask a natural-language question about your network using the latest analysis.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
backend setup:
  ollama (default)  brew install ollama && ollama pull llama3.2 && brew services start ollama
  claude            pip install anthropic  →  export ANTHROPIC_API_KEY=sk-ant-...
  openai            pip install openai     →  export OPENAI_API_KEY=sk-...

examples:
  python3 optimizer.py ask "why does my iPad keep dropping?"
  python3 optimizer.py ask "which AP is overloaded?" --backend claude
  python3 optimizer.py ask "summarize my network" --model llama3.1
        """,
    )
    parser.add_argument("question", help="Your question in plain English")
    parser.add_argument(
        "--backend",
        choices=["ollama", "claude", "openai"],
        help="AI backend to use (default: from config.yaml, fallback: ollama)",
    )
    parser.add_argument(
        "--model",
        help="Model name override (e.g. llama3.1, claude-opus-4-6, gpt-4o)",
    )
    parser.add_argument(
        "--cache",
        help="Path to a specific analysis_cache_*.json file (default: most recent)",
    )

    parsed = parser.parse_args(args[1:])  # strip 'ask' from args

    try:
        from core.ai_advisor import ask

        analysis_data = None
        recommendations = None

        if parsed.cache:
            import json

            with open(parsed.cache) as f:
                cached = json.load(f)
            analysis_data = cached.get("full_analysis")
            recommendations = cached.get("recommendations", [])
            if not analysis_data:
                print(f"Error: cache file '{parsed.cache}' contains no analysis data.")
                return 1

        answer = ask(
            parsed.question,
            analysis_data=analysis_data,
            recommendations=recommendations,
            backend=parsed.backend,
            model=parsed.model,
        )
        print(f"\n{answer}\n")
        return 0

    except (RuntimeError, ValueError) as e:
        print(f"\nError: {e}\n")
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 1
    except Exception as e:
        import traceback

        print(f"\nUnexpected error: {e}\n")
        traceback.print_exc()
        return 1


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
  python3 optimizer.py ask      "question"  Ask a question about your network (no controller needed)

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

Ask options:
  --backend NAME       AI backend: ollama (default), claude, openai
  --model NAME         Model override (e.g. llama3.1, claude-opus-4-6)
  --cache FILE         Use a specific analysis cache file

Examples:
  python3 optimizer.py analyze --host https://192.168.1.1 --username audit
  python3 optimizer.py analyze --profile default
  python3 optimizer.py optimize --profile default --dry-run
  python3 optimizer.py optimize --profile default
  python3 optimizer.py ask "why does my iPad keep dropping?"
  python3 optimizer.py ask "which AP is most overloaded?" --backend claude
""")
        return 0

    args = sys.argv[1:]

    # Handle 'ask' subcommand directly — no controller connection needed
    if args[0] == "ask":
        return _run_ask(args)

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
