#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
#  UnifiOptimizer — One-command installer for macOS and Linux
#  Usage:  bash install.sh
# ──────────────────────────────────────────────────────────────
set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

info()  { echo -e "${CYAN}▸${NC} $*"; }
ok()    { echo -e "${GREEN}✔${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
fail()  { echo -e "${RED}✖ $*${NC}"; exit 1; }

echo ""
echo -e "${CYAN}════════════════════════════════════════${NC}"
echo -e "${CYAN}  UnifiOptimizer Installer${NC}"
echo -e "${CYAN}════════════════════════════════════════${NC}"
echo ""

# ── Detect OS ────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
    Darwin) PLATFORM="mac"  ;;
    Linux)  PLATFORM="linux" ;;
    *)      fail "Unsupported OS: $OS (macOS and Linux only)" ;;
esac
ok "Detected platform: $PLATFORM ($OS)"

# ── Helper: check if a command exists ────────────────────────
has() { command -v "$1" &>/dev/null; }

# ── 1. Python 3.9+ ──────────────────────────────────────────
info "Checking Python..."
PYTHON=""
for cmd in python3 python; do
    if has "$cmd"; then
        ver=$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)
        major=${ver%%.*}; minor=${ver#*.}
        if [ "$major" -ge 3 ] && [ "$minor" -ge 9 ] 2>/dev/null; then
            PYTHON="$cmd"; break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    warn "Python 3.9+ not found — installing..."
    if [ "$PLATFORM" = "mac" ]; then
        if ! has brew; then
            info "Installing Homebrew..."
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        fi
        brew install python@3.11
    else
        if has apt-get; then
            sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-pip python3-venv
        elif has dnf; then
            sudo dnf install -y python3 python3-pip
        elif has yum; then
            sudo yum install -y python3 python3-pip
        else
            fail "No supported package manager found (apt/dnf/yum). Install Python 3.9+ manually."
        fi
    fi
    PYTHON="python3"
fi
ok "Python: $($PYTHON --version)"

# ── 2. pip ───────────────────────────────────────────────────
info "Checking pip..."
if ! $PYTHON -m pip --version &>/dev/null; then
    warn "pip not found — installing..."
    if [ "$PLATFORM" = "mac" ]; then
        $PYTHON -m ensurepip --upgrade 2>/dev/null || brew install python@3.11
    else
        if has apt-get; then
            sudo apt-get install -y -qq python3-pip
        else
            curl -sS https://bootstrap.pypa.io/get-pip.py | $PYTHON
        fi
    fi
fi
ok "pip: $($PYTHON -m pip --version 2>&1 | head -1)"

# ── 3. Node.js 18+ ──────────────────────────────────────────
info "Checking Node.js..."
NODE_OK=false
if has node; then
    NODE_VER=$(node -v | sed 's/v//')
    NODE_MAJOR=${NODE_VER%%.*}
    [ "$NODE_MAJOR" -ge 18 ] 2>/dev/null && NODE_OK=true
fi

if [ "$NODE_OK" = false ]; then
    warn "Node.js 18+ not found — installing..."
    if [ "$PLATFORM" = "mac" ]; then
        has brew || /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        brew install node@20
    else
        if has apt-get; then
            curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
            sudo apt-get install -y -qq nodejs
        elif has dnf; then
            sudo dnf install -y nodejs
        else
            fail "Install Node.js 18+ manually: https://nodejs.org"
        fi
    fi
fi
ok "Node.js: $(node -v)"
ok "npm: $(npm -v)"

# ── 4. Python dependencies ───────────────────────────────────
info "Installing Python dependencies..."
$PYTHON -m pip install --quiet -r requirements.txt
ok "Python packages installed"

# ── 5. Frontend dependencies ─────────────────────────────────
info "Installing frontend dependencies..."
(cd web && npm install --silent)
ok "Frontend packages installed"

# ── 6. Build frontend ────────────────────────────────────────
info "Building frontend for production..."
(cd web && npx vite build --mode production 2>&1 | tail -3)
ok "Frontend built → web/dist/"

# ── 7. Generate JWT_SECRET if not set ────────────────────────
if [ -z "$JWT_SECRET" ]; then
    SECRET=$(openssl rand -hex 32 2>/dev/null || $PYTHON -c "import secrets; print(secrets.token_hex(32))")
    warn "JWT_SECRET not set — generated one for this session."
    warn "For production, add to your shell profile:"
    echo ""
    echo "  export JWT_SECRET=\"$SECRET\""
    echo ""
    export JWT_SECRET="$SECRET"
fi

# ── 8. Verify ────────────────────────────────────────────────
info "Verifying installation..."
$PYTHON -c "import fastapi, uvicorn, rich, requests, yaml; print('All Python imports OK')"
(cd web && node -e "require('fs').existsSync('dist/index.html') || process.exit(1)") && ok "Frontend dist verified"

echo ""
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ Installation complete!${NC}"
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo ""
echo "  Start the app:"
echo ""
echo "    ./start.sh"
echo ""
echo "  Then open http://localhost:5173"
echo ""
echo "  Or run CLI analysis:"
echo ""
echo "    $PYTHON optimizer.py analyze --host https://YOUR_CONTROLLER_IP --username admin"
echo ""
