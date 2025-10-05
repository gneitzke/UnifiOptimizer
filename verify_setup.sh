#!/bin/bash
# Development Environment Verification Script
# Checks that all required tools are installed and configured

set -e

echo "🔍 Verifying development environment..."
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Track if everything is OK
ALL_OK=true

# Check Python
echo "🐍 Checking Python..."
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version)
    echo -e "${GREEN}✓ ${PYTHON_VERSION}${NC}"
else
    echo -e "${RED}✗ Python 3 not found${NC}"
    ALL_OK=false
fi
echo ""

# Check pip
echo "📦 Checking pip..."
if command -v pip3 &> /dev/null; then
    PIP_VERSION=$(pip3 --version | cut -d' ' -f2)
    echo -e "${GREEN}✓ pip ${PIP_VERSION}${NC}"
else
    echo -e "${RED}✗ pip3 not found${NC}"
    ALL_OK=false
fi
echo ""

# Check runtime dependencies
echo "📚 Checking runtime dependencies..."
MISSING_RUNTIME=()

if ! python3 -c "import requests" 2>/dev/null; then
    MISSING_RUNTIME+=("requests")
fi

if ! python3 -c "import rich" 2>/dev/null; then
    MISSING_RUNTIME+=("rich")
fi

if ! python3 -c "import matplotlib" 2>/dev/null; then
    MISSING_RUNTIME+=("matplotlib")
fi

if [ ${#MISSING_RUNTIME[@]} -eq 0 ]; then
    echo -e "${GREEN}✓ All runtime dependencies installed${NC}"
else
    echo -e "${YELLOW}⚠ Missing: ${MISSING_RUNTIME[*]}${NC}"
    echo -e "${BLUE}  Install with: pip3 install -r requirements.txt${NC}"
    ALL_OK=false
fi
echo ""

# Check development tools
echo "🛠️  Checking development tools..."
MISSING_DEV=()

if ! command -v black &> /dev/null; then
    MISSING_DEV+=("black")
fi

if ! command -v flake8 &> /dev/null; then
    MISSING_DEV+=("flake8")
fi

if ! command -v isort &> /dev/null; then
    MISSING_DEV+=("isort")
fi

if [ ${#MISSING_DEV[@]} -eq 0 ]; then
    echo -e "${GREEN}✓ All development tools installed${NC}"

    # Show versions
    BLACK_VERSION=$(black --version 2>&1 | head -n1 | cut -d' ' -f2)
    FLAKE8_VERSION=$(flake8 --version 2>&1 | head -n1 | cut -d' ' -f1)
    ISORT_VERSION=$(isort --version 2>&1 | grep -oP '\d+\.\d+\.\d+' | head -n1)

    echo -e "${GREEN}  • Black ${BLACK_VERSION}${NC}"
    echo -e "${GREEN}  • Flake8 ${FLAKE8_VERSION}${NC}"
    echo -e "${GREEN}  • isort ${ISORT_VERSION}${NC}"
else
    echo -e "${YELLOW}⚠ Missing: ${MISSING_DEV[*]}${NC}"
    echo -e "${BLUE}  Install with: pip3 install -r requirements-dev.txt${NC}"
    ALL_OK=false
fi
echo ""

# Check configuration files
echo "⚙️  Checking configuration files..."
CONFIG_OK=true

if [ ! -f ".flake8" ]; then
    echo -e "${RED}✗ .flake8 not found${NC}"
    CONFIG_OK=false
fi

if [ ! -f "pyproject.toml" ]; then
    echo -e "${RED}✗ pyproject.toml not found${NC}"
    CONFIG_OK=false
fi

if [ ! -d ".vscode" ]; then
    echo -e "${YELLOW}⚠ .vscode/ directory not found (OK if not using VS Code)${NC}"
elif [ ! -f ".vscode/settings.json" ]; then
    echo -e "${YELLOW}⚠ .vscode/settings.json not found${NC}"
fi

if [ "$CONFIG_OK" = true ]; then
    echo -e "${GREEN}✓ Configuration files present${NC}"
fi
echo ""

# Check scripts
echo "📜 Checking scripts..."
SCRIPTS_OK=true

if [ ! -f "check_code.sh" ]; then
    echo -e "${RED}✗ check_code.sh not found${NC}"
    SCRIPTS_OK=false
elif [ ! -x "check_code.sh" ]; then
    echo -e "${YELLOW}⚠ check_code.sh not executable${NC}"
    echo -e "${BLUE}  Run: chmod +x check_code.sh${NC}"
fi

if [ ! -f "format_code.sh" ]; then
    echo -e "${RED}✗ format_code.sh not found${NC}"
    SCRIPTS_OK=false
elif [ ! -x "format_code.sh" ]; then
    echo -e "${YELLOW}⚠ format_code.sh not executable${NC}"
    echo -e "${BLUE}  Run: chmod +x format_code.sh${NC}"
fi

if [ "$SCRIPTS_OK" = true ] && [ -x "check_code.sh" ] && [ -x "format_code.sh" ]; then
    echo -e "${GREEN}✓ Scripts present and executable${NC}"
fi
echo ""

# Summary
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$ALL_OK" = true ]; then
    echo -e "${GREEN}✅ Development environment is ready!${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Start coding!"
    echo "  2. Format: ./format_code.sh"
    echo "  3. Check:  ./check_code.sh"
    echo "  4. Commit your changes"
    echo ""
    echo "Documentation:"
    echo "  • docs/DEVELOPER_SETUP.md"
    echo "  • docs/CODE_QUALITY.md"
else
    echo -e "${YELLOW}⚠️  Some components are missing${NC}"
    echo ""
    echo "To fix:"
    echo -e "${BLUE}  pip3 install -r requirements.txt${NC}"
    echo -e "${BLUE}  pip3 install -r requirements-dev.txt${NC}"
    echo -e "${BLUE}  chmod +x check_code.sh format_code.sh${NC}"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
