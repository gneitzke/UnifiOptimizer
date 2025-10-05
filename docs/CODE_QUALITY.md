# Code Quality & Formatting Guide

## Overview

This project uses industry-standard Python code quality tools to maintain clean, consistent, and readable code:

- **Black** - Automatic code formatter (opinionated, zero-config)
- **isort** - Import statement organizer
- **Flake8** - Linter (style checker and bug detector)

## Installation

### Install Development Tools

```bash
# Install all dev tools at once
pip3 install -r requirements-dev.txt

# Or install individually
pip3 install black flake8 isort
```

## Quick Start

### Check Code Quality
```bash
./check_code.sh
```

This will:
- ✓ Check import sorting
- ✓ Check code formatting
- ✓ Run linter (flake8)
- ✓ Report any issues

### Auto-Format Code
```bash
./format_code.sh
```

This will:
- ✓ Sort all imports (isort)
- ✓ Format all code (Black)
- ✓ Make code compliant with style guide

## The Tools Explained

### 1. Black (Code Formatter)

**What it does:**
- Automatically formats Python code
- Enforces consistent style across entire codebase
- No configuration needed ("opinionated")

**Example:**
```python
# Before
def my_function(  x,y,   z  ):
    return x+y+z

# After Black
def my_function(x, y, z):
    return x + y + z
```

**Manual usage:**
```bash
# Format a specific file
black api/cloudkey_gen2_client.py

# Format entire directory
black core/

# Check without modifying
black --check api/

# Show what would change
black --diff api/
```

### 2. isort (Import Organizer)

**What it does:**
- Sorts and organizes import statements
- Groups imports by type (standard library, third-party, local)
- Removes duplicate imports

**Example:**
```python
# Before
from rich.console import Console
import sys
from api.cloudkey_gen2_client import CloudKeyGen2Client
import os
import requests

# After isort
import os
import sys

import requests
from rich.console import Console

from api.cloudkey_gen2_client import CloudKeyGen2Client
```

**Manual usage:**
```bash
# Sort imports in a file
isort api/cloudkey_gen2_client.py

# Sort entire directory
isort core/

# Check without modifying
isort --check-only api/

# Show what would change
isort --diff api/
```

### 3. Flake8 (Linter)

**What it checks:**
- PEP 8 style guide compliance
- Unused imports
- Undefined variables
- Syntax errors
- Potential bugs

**Example issues it catches:**
```python
# Unused import
import os  # F401: imported but unused

# Undefined variable
print(undefined_var)  # F821: undefined name

# Line too long
very_long_line_that_exceeds_100_characters_and_should_be_split  # E501

# Whitespace issues
x=1+2  # E225: missing whitespace around operator
```

**Manual usage:**
```bash
# Check a specific file
flake8 api/cloudkey_gen2_client.py

# Check entire directory
flake8 core/

# Show statistics
flake8 --statistics api/
```

## Configuration

### Black Configuration
**File:** `pyproject.toml`

```toml
[tool.black]
line-length = 100
target-version = ['py37', 'py38', 'py39', 'py310']
```

- **Line length:** 100 characters (balance between readability and brevity)
- **Python versions:** 3.7+ compatible

### isort Configuration
**File:** `pyproject.toml`

```toml
[tool.isort]
profile = "black"  # Compatible with Black
line_length = 100
known_first_party = ["api", "core", "utils"]
```

- **Profile:** "black" ensures compatibility with Black
- **First-party:** Treats api/, core/, utils/ as local modules

### Flake8 Configuration
**File:** `.flake8`

```ini
[flake8]
max-line-length = 100
ignore = E203, E501, W503
exclude = Archive, OldFiles, __pycache__
```

- **Max line length:** 100 (matches Black)
- **Ignored errors:** Those that conflict with Black
- **Excluded:** Archive folders, cache files

## Workflow

### Before Committing Code

1. **Format your code:**
   ```bash
   ./format_code.sh
   ```

2. **Check for issues:**
   ```bash
   ./check_code.sh
   ```

3. **Fix any linting errors manually**

4. **Commit clean code** ✅

### During Development

Run checks periodically:
```bash
# Quick check
./check_code.sh

# Fix formatting issues
./format_code.sh
```

### Pre-Commit Hook (Optional)

Create `.git/hooks/pre-commit`:
```bash
#!/bin/bash
./check_code.sh
```

This runs checks automatically before each commit.

## Common Issues & Solutions

### Issue: "Command not found: black"
**Solution:** Install dev tools
```bash
pip3 install -r requirements-dev.txt
```

### Issue: "Line too long (E501)"
**Solution:** Black usually handles this, but sometimes you need to manually break lines:
```python
# Bad
very_long_function_call(arg1, arg2, arg3, arg4, arg5, arg6, arg7, arg8, arg9, arg10)

# Good
very_long_function_call(
    arg1, arg2, arg3, arg4, arg5,
    arg6, arg7, arg8, arg9, arg10
)
```

### Issue: "Undefined name (F821)"
**Solution:** Fix the actual bug - you're using a variable that doesn't exist
```python
# Bad
print(result)  # Where does 'result' come from?

# Good
result = calculate_something()
print(result)
```

### Issue: "Imported but unused (F401)"
**Solution:** Remove unused imports or use them
```python
# Bad
import os  # Not used anywhere

# Good - either remove it or use it
import os
os.path.exists('file.txt')
```

### Issue: "Trailing whitespace (W291)"
**Solution:** Black removes this automatically, run `./format_code.sh`

## Integration with VS Code

Add to `.vscode/settings.json`:
```json
{
    "python.formatting.provider": "black",
    "python.linting.flake8Enabled": true,
    "python.linting.enabled": true,
    "editor.formatOnSave": true,
    "[python]": {
        "editor.codeActionsOnSave": {
            "source.organizeImports": true
        }
    }
}
```

This will:
- ✓ Format with Black on save
- ✓ Sort imports on save
- ✓ Show Flake8 errors in editor

## Best Practices

### DO ✅
- Run `./format_code.sh` before committing
- Run `./check_code.sh` to verify quality
- Fix all Flake8 errors (red warnings)
- Keep imports organized
- Follow PEP 8 style guide

### DON'T ❌
- Ignore linting errors without understanding them
- Commit unformatted code
- Disable tools globally (only disable specific lines if needed)
- Fight with Black's formatting (just accept it)

### Disabling Checks (When Necessary)

**Disable specific line:**
```python
import example  # noqa: F401  (unused but needed for side effects)
very_long_url = "https://..."  # noqa: E501  (URL can't be wrapped)
```

**Disable for entire file:**
```python
# flake8: noqa
```

Only do this if absolutely necessary!

## Scripts Reference

### check_code.sh
**Purpose:** Check code quality without modifying files

**Output:**
```
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

### format_code.sh
**Purpose:** Automatically format all code

**Output:**
```
🎨 Formatting code...

📋 Sorting imports with isort...
✓ Imports sorted

🎨 Formatting code with Black...
✓ Code formatted

✅ Formatting complete!

Run './check_code.sh' to verify code quality
```

## CI/CD Integration

Add to GitHub Actions (`.github/workflows/lint.yml`):
```yaml
name: Lint

on: [push, pull_request]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.10'
      - name: Install dependencies
        run: |
          pip install -r requirements-dev.txt
      - name: Check code quality
        run: ./check_code.sh
```

This will automatically check code quality on every push/PR.

## Summary

### Quick Commands
```bash
# Install tools
pip3 install -r requirements-dev.txt

# Format code
./format_code.sh

# Check code
./check_code.sh
```

### What Each Tool Does
- **Black** → Formats code automatically
- **isort** → Organizes imports
- **Flake8** → Finds style issues and bugs

### Benefits
✅ Consistent code style across project  
✅ Fewer bugs (linter catches them)  
✅ Easier code reviews (no style debates)  
✅ Professional appearance  
✅ Better maintainability  

**Bottom line:** Run `./format_code.sh` before committing, run `./check_code.sh` to verify. Clean, consistent code with minimal effort!
