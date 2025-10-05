# Developer Setup Guide

Complete setup instructions for contributing to UnifiOptimizer.

---

## Prerequisites

- **Python 3.7+** installed
- **Git** installed
- **VS Code** (recommended) or any text editor

---

## Initial Setup

### 1. Clone the Repository

```bash
git clone https://github.com/gneitzke/UnifiOptimizer.git
cd UnifiOptimizer
```

### 2. Install Runtime Dependencies

```bash
pip3 install -r requirements.txt
```

This installs:
- `requests` - HTTP client
- `rich` - Terminal formatting
- `urllib3` - HTTP utilities
- `matplotlib` - Graphing

### 3. Install Development Tools (Required for Contributing)

```bash
pip3 install -r requirements-dev.txt
```

This installs:
- **Black** - Code formatter
- **Flake8** - Linter
- **isort** - Import organizer

**⚠️ Important:** These tools are required before submitting any code changes.

### 4. Verify Installation

```bash
# Test the main script
python3 optimizer.py --help

# Test development tools
black --version
flake8 --version
isort --version
```

---

## VS Code Setup (Recommended)

### 1. Install VS Code

Download from: https://code.visualstudio.com/

### 2. Open Project in VS Code

```bash
code .
```

### 3. Install Recommended Extensions

VS Code will prompt you to install recommended extensions. Click **"Install All"**.

Or manually install:
- **Python** (ms-python.python)
- **Pylance** (ms-python.vscode-pylance)
- **Black Formatter** (ms-python.black-formatter)
- **isort** (ms-python.isort)
- **Flake8** (ms-python.flake8)

### 4. Verify Configuration

The project includes `.vscode/settings.json` which automatically:
- ✅ Formats code on save (Black)
- ✅ Organizes imports on save (isort)
- ✅ Shows linting errors inline (Flake8)
- ✅ Shows 100-character ruler
- ✅ Trims trailing whitespace

**Test it:**
1. Open any `.py` file
2. Add some messy code:
   ```python
   def test(  x,y,   z  ):
       return x+y+z
   ```
3. Save the file (`Cmd+S` / `Ctrl+S`)
4. Code should auto-format to:
   ```python
   def test(x, y, z):
       return x + y + z
   ```

If auto-format doesn't work, check:
- Python extension is installed
- Black is installed: `pip3 install black`
- Check VS Code Output panel (View → Output → Python)

---

## Development Workflow

### Daily Workflow

1. **Write code** ✍️
   ```bash
   vim api/cloudkey_gen2_client.py
   # or
   code api/cloudkey_gen2_client.py
   ```

2. **Format code** (if not using VS Code auto-format) 🎨
   ```bash
   ./format_code.sh
   ```

3. **Check code quality** ✅
   ```bash
   ./check_code.sh
   ```

4. **Fix any issues** 🔧
   - Address Flake8 errors shown
   - Re-run check script

5. **Test your changes** 🧪
   ```bash
   python3 optimizer.py analyze --host https://192.168.1.1 --username admin --dry-run
   ```

6. **Commit** 🚀
   ```bash
   git add .
   git commit -m "Add feature X"
   git push
   ```

### Before Every Commit

**Always run these two commands:**

```bash
./format_code.sh   # Auto-format all code
./check_code.sh    # Verify quality
```

Output should be:
```
✅ All checks passed!
```

If you see errors, fix them before committing.

---

## Code Quality Scripts

### `./format_code.sh` - Auto-Format

**What it does:**
- Sorts imports (isort)
- Formats code (Black)
- Makes code compliant with style guide

**When to use:**
- Before committing
- After making changes
- When code looks messy

**Example:**
```bash
$ ./format_code.sh
🎨 Formatting code...

📋 Sorting imports with isort...
✓ Imports sorted

🎨 Formatting code with Black...
✓ Code formatted

✅ Formatting complete!
```

### `./check_code.sh` - Verify Quality

**What it does:**
- Checks import sorting
- Checks code formatting
- Runs linter (Flake8)

**When to use:**
- Before committing
- After formatting
- To verify code quality

**Example:**
```bash
$ ./check_code.sh
🔍 Running code quality checks...

📦 Checking for required tools...
✓ All tools installed

📋 Checking import sorting with isort...
✓ Import sorting looks good

🎨 Checking code formatting with Black...
✓ Code formatting looks good

🔍 Running Flake8 linter...
✓ No linting issues found

✅ All checks passed!
```

---

## Common Issues & Solutions

### Issue: "Command not found: black"
**Solution:**
```bash
pip3 install -r requirements-dev.txt
```

### Issue: "Format on save not working in VS Code"
**Solutions:**
1. Install Black: `pip3 install black`
2. Install Python extension in VS Code
3. Reload VS Code window (Cmd+Shift+P → "Reload Window")
4. Check Output panel: View → Output → Select "Python"

### Issue: "Flake8 errors in VS Code"
**This is good!** It means the linter is working.

**To fix:**
- Read the error message
- Fix the issue in your code
- Common errors:
  - `F401` - Unused import (remove it)
  - `E501` - Line too long (Black usually fixes this)
  - `F821` - Undefined variable (fix the bug)

### Issue: "isort and Black conflict"
**Solution:** This shouldn't happen with our config.

If it does:
```bash
# Run isort first, then Black
isort api/
black api/
```

Our configuration ensures they work together.

### Issue: Scripts fail with "Permission denied"
**Solution:**
```bash
chmod +x check_code.sh format_code.sh
```

---

## Testing Your Changes

### Manual Testing

1. **Test analyze mode:**
   ```bash
   python3 optimizer.py analyze --host https://192.168.1.1 --username admin
   ```

2. **Test verbose mode:**
   ```bash
   python3 optimizer.py analyze --host https://192.168.1.1 --username admin --verbose
   ```

3. **Test dry-run mode:**
   ```bash
   python3 optimizer.py apply --host https://192.168.1.1 --username admin --dry-run
   ```

### Checking Syntax

```bash
# Check Python syntax
python3 -m py_compile api/*.py core/*.py utils/*.py

# Or specific file
python3 -m py_compile api/cloudkey_gen2_client.py
```

### Running Existing Tests

```bash
# Run all tests (if available)
python3 run_all_tests.py

# Run specific test
python3 test_advanced_features.py
```

---

## Project Structure

```
UnifiOptimizer/
├── .vscode/                    # VS Code configuration
│   ├── settings.json          # Auto-format, linting settings
│   └── extensions.json        # Recommended extensions
├── api/                       # API client modules
│   ├── __init__.py
│   ├── cloudkey_gen2_client.py
│   ├── csrf_token_manager.py
│   └── cloudkey_jwt_helper.py
├── core/                      # Core analysis modules
│   ├── __init__.py
│   ├── optimize_network.py
│   ├── advanced_analyzer.py
│   ├── client_health.py
│   └── ...
├── utils/                     # Utility modules
│   ├── __init__.py
│   └── keychain.py
├── docs/                      # Documentation
│   ├── CODE_QUALITY.md
│   ├── API_ACCESS_VERIFICATION.md
│   ├── VERBOSE_LOGGING.md
│   └── ...
├── tests/                     # Test files
├── .flake8                    # Flake8 configuration
├── pyproject.toml             # Black & isort configuration
├── requirements.txt           # Runtime dependencies
├── requirements-dev.txt       # Development tools
├── check_code.sh              # Code quality checker
├── format_code.sh             # Code formatter
├── optimizer.py               # Main entry point
└── README.md                  # User documentation
```

---

## Code Style Guidelines

### Line Length
**Maximum: 100 characters**

Black will automatically wrap long lines, but sometimes you need to manually break them:

```python
# Good - manually wrapped for readability
result = very_long_function_name(
    argument1,
    argument2,
    argument3,
    argument4
)

# Avoid - too long
result = very_long_function_name(argument1, argument2, argument3, argument4, argument5, argument6)
```

### Import Organization

Imports are automatically organized by isort into groups:

```python
# 1. Standard library
import os
import sys
from datetime import datetime

# 2. Third-party packages
import requests
from rich.console import Console

# 3. Local modules
from api.cloudkey_gen2_client import CloudKeyGen2Client
from core.network_analyzer import NetworkAnalyzer
```

### Docstrings

Use descriptive docstrings for functions and classes:

```python
def analyze_network(client, site, lookback_days):
    """
    Analyze network configuration and health.

    Args:
        client: CloudKeyGen2Client instance
        site: Site name (default: 'default')
        lookback_days: Days of historical data to analyze

    Returns:
        dict: Analysis results with recommendations
    """
    # Implementation
```

### Error Handling

```python
# Good - specific exception, helpful message
try:
    result = client.get('s/default/stat/device')
except requests.exceptions.Timeout:
    console.print("[red]Connection timeout - controller not responding[/red]")
    return None

# Avoid - too broad
try:
    result = client.get('s/default/stat/device')
except:  # Don't do this
    pass
```

### Console Output

Use Rich console for formatted output:

```python
from rich.console import Console
console = Console()

# Success messages
console.print("[green]✓ Operation successful[/green]")

# Warnings
console.print("[yellow]⚠ Warning message[/yellow]")

# Errors
console.print("[red]✗ Error message[/red]")

# Info (verbose mode)
if verbose:
    console.print("[dim]ℹ Additional information[/dim]")
```

---

## Git Workflow

### Branch Naming

- `feature/description` - New features
- `fix/description` - Bug fixes
- `docs/description` - Documentation updates
- `refactor/description` - Code refactoring

### Commit Messages

**Format:**
```
<type>: <short description>

<optional longer description>
```

**Types:**
- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation
- `style:` - Code style (formatting, etc.)
- `refactor:` - Code refactoring
- `test:` - Adding tests
- `chore:` - Maintenance tasks

**Examples:**
```bash
git commit -m "feat: Add API access verification after login"
git commit -m "fix: Correct RSSI sign inversion bug"
git commit -m "docs: Update README with verbose logging info"
git commit -m "style: Format code with Black"
```

### Pre-Commit Checklist

Before every commit:

- [ ] Code is formatted: `./format_code.sh`
- [ ] Quality checks pass: `./check_code.sh`
- [ ] Code compiles: `python3 -m py_compile <files>`
- [ ] Manual testing done
- [ ] Documentation updated (if needed)
- [ ] Commit message is clear

---

## Getting Help

### Documentation

- **Code Quality:** [docs/CODE_QUALITY.md](docs/CODE_QUALITY.md)
- **API Access:** [docs/API_ACCESS_VERIFICATION.md](docs/API_ACCESS_VERIFICATION.md)
- **Verbose Logging:** [docs/VERBOSE_LOGGING.md](docs/VERBOSE_LOGGING.md)
- **Quick Reference:** [CODE_QUALITY_QUICKREF.md](CODE_QUALITY_QUICKREF.md)

### Common Commands Reference

```bash
# Setup
pip3 install -r requirements.txt
pip3 install -r requirements-dev.txt

# Development
./format_code.sh          # Format code
./check_code.sh           # Check quality
python3 -m py_compile <file>  # Check syntax

# Testing
python3 optimizer.py --help
python3 optimizer.py analyze --host <IP> --username <user> --verbose

# Git
git status
git add .
git commit -m "feat: description"
git push
```

---

## Summary

### One-Time Setup
1. Clone repo
2. Install dependencies: `pip3 install -r requirements.txt`
3. Install dev tools: `pip3 install -r requirements-dev.txt`
4. Open in VS Code
5. Install recommended extensions

### Every Time You Code
1. Write code
2. Format: `./format_code.sh`
3. Check: `./check_code.sh`
4. Test manually
5. Commit

### VS Code Benefits
✅ Auto-format on save
✅ Auto-organize imports
✅ Inline linting errors
✅ 100-char ruler visible
✅ Trailing whitespace removed

### Key Files
- `.vscode/settings.json` - VS Code configuration
- `.flake8` - Linter configuration
- `pyproject.toml` - Black & isort configuration
- `requirements-dev.txt` - Dev tool dependencies

**Questions?** Check [docs/CODE_QUALITY.md](docs/CODE_QUALITY.md) for detailed information.
