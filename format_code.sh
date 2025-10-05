#!/bin/bash
# Code Formatter Script
# Automatically formats code with Black and isort

set -e  # Exit on error

echo "🎨 Formatting code..."
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if tools are installed
if ! command -v black &> /dev/null; then
    echo -e "${YELLOW}⚠️  Black not found. Install with: pip3 install black${NC}"
    exit 1
fi

if ! command -v isort &> /dev/null; then
    echo -e "${YELLOW}⚠️  isort not found. Install with: pip3 install isort${NC}"
    exit 1
fi

# Run isort (import sorting)
echo "📋 Sorting imports with isort..."
isort api/ core/ utils/ *.py 2>/dev/null || true
echo -e "${GREEN}✓ Imports sorted${NC}"
echo ""

# Run Black (formatting)
echo "🎨 Formatting code with Black..."
black api/ core/ utils/ *.py 2>/dev/null || true
echo -e "${GREEN}✓ Code formatted${NC}"
echo ""

echo -e "${GREEN}✅ Formatting complete!${NC}"
echo ""
echo "Run './check_code.sh' to verify code quality"
