#!/bin/bash
# Code Quality Check Script
# Runs linting and formatting checks on the codebase

set -e  # Exit on error

echo "🔍 Running code quality checks..."
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if tools are installed
echo "📦 Checking for required tools..."
if ! command -v black &> /dev/null; then
    echo -e "${YELLOW}⚠️  Black not found. Install with: pip3 install black${NC}"
    exit 1
fi

if ! command -v flake8 &> /dev/null; then
    echo -e "${YELLOW}⚠️  Flake8 not found. Install with: pip3 install flake8${NC}"
    exit 1
fi

if ! command -v isort &> /dev/null; then
    echo -e "${YELLOW}⚠️  isort not found. Install with: pip3 install isort${NC}"
    exit 1
fi

echo -e "${GREEN}✓ All tools installed${NC}"
echo ""

# Run isort (import sorting) - check only
echo "📋 Checking import sorting with isort..."
if isort --check-only --diff api/ core/ utils/ *.py 2>/dev/null; then
    echo -e "${GREEN}✓ Import sorting looks good${NC}"
else
    echo -e "${YELLOW}⚠️  Imports need sorting. Run: ./format_code.sh${NC}"
fi
echo ""

# Run Black (formatting) - check only
echo "🎨 Checking code formatting with Black..."
if black --check --quiet api/ core/ utils/ *.py 2>/dev/null; then
    echo -e "${GREEN}✓ Code formatting looks good${NC}"
else
    echo -e "${YELLOW}⚠️  Code needs formatting. Run: ./format_code.sh${NC}"
fi
echo ""

# Run Flake8 (linting)
echo "🔍 Running Flake8 linter..."
if flake8 api/ core/ utils/ *.py; then
    echo -e "${GREEN}✓ No linting issues found${NC}"
else
    echo -e "${RED}✗ Linting issues found${NC}"
    exit 1
fi
echo ""

echo -e "${GREEN}✅ All checks passed!${NC}"
