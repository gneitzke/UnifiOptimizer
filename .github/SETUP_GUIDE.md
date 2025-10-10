# CI/CD Setup Guide

## ✅ What Was Created

### GitHub Actions Workflows (.github/workflows/)
1. **ci.yml** - Main CI pipeline
   - Runs on every push and PR
   - Tests Python 3.9-3.12
   - Linting, security, quality checks
   - Validates bug fixes remain intact

2. **extended-tests.yml** - Comprehensive testing
   - Runs daily at 2 AM UTC
   - Cross-platform compatibility tests
   - Performance validation

3. **pr-validation.yml** - Pull request checks
   - PR title format validation
   - Breaking changes detection
   - Credential scanning
   - Merge conflict detection

4. **release.yml** - Automated releases
   - Triggers on version tags (v*)
   - Creates release archives
   - Generates changelogs

### Configuration Files
- **.pre-commit-config.yaml** - Local pre-commit hooks
- **.github/workflows/mlc_config.json** - Markdown link checker config
- **.github/CI_CD_DOCUMENTATION.md** - Detailed CI/CD docs
- **.github/check_ci_status.py** - CI verification script

## 🚀 Quick Start

### 1. Commit and Push CI Configuration
```bash
git add .github .pre-commit-config.yaml
git commit -m "ci: Add comprehensive CI/CD pipeline"
git push origin main
```

### 2. Enable GitHub Actions
- Go to your repository on GitHub
- Click "Actions" tab
- If disabled, click "I understand my workflows, go ahead and enable them"

### 3. Set Up Local Pre-commit Hooks (Optional but Recommended)
```bash
pip install pre-commit
pre-commit install
```

Now pre-commit hooks will run automatically before each commit!

### 4. Verify CI is Working
- Go to GitHub → Actions tab
- You should see workflows running
- Check that all jobs pass ✅

## 🔧 Local Development

### Run Checks Locally (Before Pushing)
```bash
# Syntax check
python -m py_compile optimizer.py core/*.py api/*.py

# Pre-commit on all files
pre-commit run --all-files

# Run tests
python run_all_tests.py
```

### Manual CI Status Check
```bash
python3 .github/check_ci_status.py
```

## 📋 What Gets Checked

### On Every Push/PR:
- ✅ Python syntax (compilation)
- ✅ Code formatting (Black)
- ✅ Linting (Flake8, Pylint)
- ✅ Security scan (Bandit)
- ✅ Module imports
- ✅ Bug fixes validation:
  - Mock data removed
  - Cache implementation present
  - Execution order correct
  - Mesh RSSI protection intact

### On Pull Requests:
- ✅ PR title format
- ✅ Breaking changes detection
- ✅ No hardcoded credentials
- ✅ Modified files compile
- ✅ Code coverage
- ✅ PR size analysis
- ✅ Merge conflicts

### Daily (Extended Tests):
- ✅ Integration tests
- ✅ Cross-platform compatibility
- ✅ Performance checks
- ✅ Documentation validation

## 🏷️ Creating Releases

When ready to release:

```bash
# Update version number in files
# Then create and push a tag
git tag -a v1.0.0 -m "Release v1.0.0: Initial stable release"
git push origin v1.0.0
```

This automatically:
1. Runs full test suite
2. Creates release archive
3. Generates changelog
4. Publishes GitHub release

## 🛡️ Branch Protection

Recommended settings for `main` branch:

1. Go to Settings → Branches → Add rule
2. Branch name pattern: `main`
3. Enable:
   - ✅ Require pull request reviews before merging
   - ✅ Require status checks to pass before merging
     - Select: `CI Pipeline / lint-and-test`
     - Select: `CI Pipeline / build-validation`
   - ✅ Require branches to be up to date before merging

## 📊 Status Badges

Add to your README.md:

```markdown
[![CI Pipeline](https://github.com/gneitzke/UnifiOptimizer/workflows/CI%20Pipeline/badge.svg)](https://github.com/gneitzke/UnifiOptimizer/actions)
[![Security Scan](https://github.com/gneitzke/UnifiOptimizer/workflows/CI%20Pipeline/badge.svg?job=security-scan)](https://github.com/gneitzke/UnifiOptimizer/actions)
```

## 🐛 Critical Bug Fix Validation

The CI pipeline includes specific checks to ensure the October 10, 2025 bug fixes remain intact:

1. **Mock Data Removal** - Ensures demo data isn't reintroduced
2. **Cache Implementation** - Verifies caching system is present
3. **Execution Order** - Validates port history analyzed before switches
4. **Mesh RSSI Protection** - Confirms mesh exclusion logic intact

These checks run on EVERY commit to prevent regression!

## 💡 Tips

### Skip CI for Documentation Changes
```bash
git commit -m "docs: Update README [skip ci]"
```

### Force Re-run Failed Workflow
- Go to Actions tab
- Click failed workflow
- Click "Re-run all jobs"

### View Detailed Logs
- Actions tab → Click workflow → Click job → Expand steps

## 🆘 Troubleshooting

### "Workflow file is invalid"
- Check YAML syntax at https://www.yamllint.com/
- Ensure proper indentation (spaces, not tabs)

### Tests Failing in CI but Pass Locally
- Check Python version matches CI matrix (3.9-3.12)
- Ensure all dependencies in requirements.txt
- Clear caches: `find . -type d -name __pycache__ -exec rm -r {} +`

### Pre-commit Hooks Too Slow
```bash
# Skip specific hooks
SKIP=black,flake8 git commit -m "message"

# Or skip all (not recommended)
git commit --no-verify
```

## 📚 Additional Resources

- Full documentation: `.github/CI_CD_DOCUMENTATION.md`
- GitHub Actions docs: https://docs.github.com/en/actions
- Pre-commit docs: https://pre-commit.com/

## ✅ Verification

Run this to verify everything is set up:
```bash
python3 .github/check_ci_status.py
```

Expected output: "🎉 CI/CD pipeline is ready!"
