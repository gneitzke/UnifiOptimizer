# CI/CD Pipeline Documentation

## Overview

This project uses GitHub Actions for continuous integration and deployment. The CI/CD pipeline automatically runs on every push and pull request to ensure code quality, security, and functionality.

## Workflows

### 1. CI Pipeline (`ci.yml`)
**Triggers**: Push to `main` or `develop`, Pull Requests

**Jobs**:
- **Lint and Test**: Runs on Python 3.9, 3.10, 3.11, 3.12
  - Syntax checking (py_compile)
  - Black code formatter check
  - Flake8 linting
  - Pylint on core modules
  - Unit tests
  - Module import verification
  - Mesh RSSI protection verification
  - Configuration file validation

- **Security Scan**:
  - Bandit security scanner
  - Safety dependency vulnerability check

- **Code Quality**:
  - Radon complexity analysis
  - Lines of code statistics

- **Validate Fixes**:
  - Checks mock data was removed
  - Verifies cache implementation
  - Validates execution order fix
  - Confirms recommendation format handler

- **Build Validation**:
  - Tests all core modules load correctly
  - Final integration check

### 2. Extended Tests (`extended-tests.yml`)
**Triggers**: Daily at 2 AM UTC, Manual dispatch

**Jobs**:
- Integration tests
- Cross-platform compatibility (Ubuntu, macOS, Windows)
- Performance validation
- Documentation checks

### 3. PR Validation (`pr-validation.yml`)
**Triggers**: Pull request opened/updated

**Jobs**:
- PR title format check
- Breaking changes detection
- Credential scanning
- Modified files compilation
- Code coverage check
- Bug fixes verification
- PR size analysis
- Merge conflict detection

### 4. Release (`release.yml`)
**Triggers**: Git tags matching `v*`

**Jobs**:
- Full test suite execution
- Changelog generation
- Release archive creation
- GitHub release publication

## Status Badges

Add these to your README.md:

```markdown
[![CI Pipeline](https://github.com/gneitzke/UnifiOptimizer/workflows/CI%20Pipeline/badge.svg)](https://github.com/gneitzke/UnifiOptimizer/actions)
[![Extended Tests](https://github.com/gneitzke/UnifiOptimizer/workflows/Extended%20Tests/badge.svg)](https://github.com/gneitzke/UnifiOptimizer/actions)
```

## Pre-commit Hooks

For local development, install pre-commit hooks:

```bash
pip install pre-commit
pre-commit install
```

This will run checks before each commit:
- Trailing whitespace removal
- End of file fixing
- YAML/JSON validation
- Large file detection
- Black code formatting
- Flake8 linting
- Bandit security scan
- Python syntax check
- Mesh protection verification
- Mock data check

## Running Checks Locally

### Syntax Check
```bash
python -m py_compile optimizer.py core/*.py api/*.py utils/*.py web/*.py
```

### Code Formatting
```bash
black --check .
```

### Linting
```bash
flake8 . --max-line-length=127
```

### Security Scan
```bash
bandit -r . -ll
```

### Run All Tests
```bash
python run_all_tests.py
```

### Pre-commit on All Files
```bash
pre-commit run --all-files
```

## Critical Checks

The CI pipeline includes specific checks to ensure bug fixes remain intact:

### 1. Mock Data Removal
Ensures demo data generation was not reintroduced:
```bash
! grep -q "Demo AP Uplink" core/switch_analyzer.py
```

### 2. Cache Implementation
Verifies caching system is present:
```bash
grep -q "hourly_data_cache" core/switch_analyzer.py
```

### 3. Execution Order
Validates analyze_switch_port_history is called before analyze_switches:
```python
history_pos < switches_pos
```

### 4. Mesh RSSI Protection
Confirms mesh AP exclusion logic is intact:
```bash
grep -q "Skip.*mesh.*min.*rssi\|EXCLUDED.*mesh" core/advanced_analyzer.py
```

## Workflow Configuration

### Required Secrets
None required for basic CI. For advanced features, you may add:
- `UNIFI_TEST_HOST`: Test controller host
- `UNIFI_TEST_USER`: Test controller username  
- `UNIFI_TEST_PASS`: Test controller password

### Branch Protection Rules

Recommended settings for `main` branch:
- ✅ Require pull request reviews (1 approver)
- ✅ Require status checks to pass before merging
  - CI Pipeline / lint-and-test
  - CI Pipeline / security-scan
  - CI Pipeline / validate-fixes
  - CI Pipeline / build-validation
- ✅ Require branches to be up to date before merging
- ✅ Require conversation resolution before merging

## Creating a Release

1. Ensure `main` branch is stable and all tests pass
2. Update version in relevant files
3. Create and push a tag:
   ```bash
   git tag -a v1.0.0 -m "Release v1.0.0"
   git push origin v1.0.0
   ```
4. The release workflow will automatically:
   - Run full test suite
   - Generate changelog
   - Create release archive
   - Publish GitHub release

## Troubleshooting

### Tests Failing Locally but Passing in CI
- Check Python version matches CI matrix
- Ensure all dependencies installed: `pip install -r requirements.txt -r requirements-dev.txt`
- Clear Python cache: `find . -type d -name __pycache__ -exec rm -r {} +`

### Pre-commit Hooks Failing
- Update hooks: `pre-commit autoupdate`
- Run manually: `pre-commit run --all-files`
- Skip temporarily: `git commit --no-verify` (not recommended)

### Security Scan False Positives
- Review Bandit report: `bandit -r . -ll -f json -o bandit-report.json`
- Add exclusions to `.bandit` file if needed

## Continuous Improvement

The CI pipeline is designed to catch issues early. If you encounter repeated failures:

1. Check the GitHub Actions logs for details
2. Run the failing check locally to debug
3. Update the workflow if check is too strict/loose
4. Add new checks as the project evolves

## Contact

For CI/CD issues or suggestions:
- Open an issue on GitHub
- Tag workflows with appropriate labels: `ci`, `testing`, `automation`
