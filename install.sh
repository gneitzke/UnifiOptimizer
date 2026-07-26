#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
#  UnifiOptimizer — one-command source installer (macOS / Linux)
#
#  Installs the package from this checkout into a local virtualenv,
#  builds the web UI and bundles it into the package (so the dashboard
#  works with no further Node steps), and tells you to run `netadmin`.
#
#  Idempotent: safe to re-run. Detects python3 and node.
#
#  Usage:  bash install.sh
#
#  Note: end users do not need this script — `pip install unifioptimizer`
#  ships a prebuilt dashboard and needs NO Node at all. This script is the
#  convenience path for a source checkout.
# ──────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}▸${NC} $*"; }
ok()    { echo -e "${GREEN}✔${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
fail()  { echo -e "${RED}✖ $*${NC}"; exit 1; }
has()   { command -v "$1" >/dev/null 2>&1; }

echo ""
echo -e "${CYAN}════════════════════════════════════════${NC}"
echo -e "${CYAN}  UnifiOptimizer installer${NC}"
echo -e "${CYAN}════════════════════════════════════════${NC}"
echo ""

# ── Detect OS ────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
    Darwin) PLATFORM="mac"   ;;
    Linux)  PLATFORM="linux" ;;
    *)      fail "Unsupported OS: $OS (macOS and Linux only)" ;;
esac
ok "Platform: $PLATFORM ($OS)"

# ── 1. Python 3.11+ (matches requires-python) ────────────────
info "Locating Python 3.11+ ..."
PYTHON=""
for cmd in python3.12 python3.11 python3 python; do
    has "$cmd" || continue
    if "$cmd" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)' 2>/dev/null; then
        PYTHON="$cmd"; break
    fi
done

if [ -z "$PYTHON" ]; then
    warn "Python 3.11+ not found — installing ..."
    if [ "$PLATFORM" = "mac" ]; then
        has brew || /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        brew install python@3.12
        PYTHON="python3.12"
    else
        if has apt-get; then
            sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-venv python3-pip
        elif has dnf; then
            sudo dnf install -y python3 python3-pip
        elif has yum; then
            sudo yum install -y python3 python3-pip
        else
            fail "No supported package manager (apt/dnf/yum). Install Python 3.11+ manually."
        fi
        PYTHON="python3"
    fi
fi
"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)' \
    || fail "Python 3.11+ required (found $($PYTHON --version 2>&1))."
ok "Python: $($PYTHON --version 2>&1)"

# ── 2. Virtualenv + install the package ──────────────────────
# A local .venv keeps this clean and sidesteps 'externally-managed' pip errors,
# while `netadmin` becomes a normal command once the venv is on PATH.
if [ -n "${VIRTUAL_ENV:-}" ]; then
    info "Using the already-active virtualenv: $VIRTUAL_ENV"
    VENV_PY="$PYTHON"
    ACTIVATE_HINT=""
else
    VENV="$SCRIPT_DIR/.venv"
    if [ ! -x "$VENV/bin/python" ]; then
        info "Creating virtualenv at .venv ..."
        "$PYTHON" -m venv "$VENV"
    else
        info "Reusing existing virtualenv at .venv"
    fi
    VENV_PY="$VENV/bin/python"
    ACTIVATE_HINT="source .venv/bin/activate"
fi

info "Installing the netadmin package (editable) ..."
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -e .
ok "Package installed (console command: netadmin)"

# ── 3. Node.js 18+ → build & bundle the web UI ───────────────
info "Checking Node.js (needed only to build the UI from source) ..."
NODE_OK=false
if has node; then
    NODE_MAJOR="$(node -v | sed 's/^v//; s/\..*//')"
    [ "${NODE_MAJOR:-0}" -ge 18 ] 2>/dev/null && NODE_OK=true
fi

if [ "$NODE_OK" = false ]; then
    warn "Node.js 18+ not found — attempting to install ..."
    if [ "$PLATFORM" = "mac" ]; then
        has brew && brew install node@20 && NODE_OK=true || true
    else
        if has apt-get; then
            curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && \
                sudo apt-get install -y -qq nodejs && NODE_OK=true || true
        elif has dnf; then
            sudo dnf install -y nodejs && NODE_OK=true || true
        fi
    fi
    has node && NODE_MAJOR="$(node -v | sed 's/^v//; s/\..*//')" && \
        [ "${NODE_MAJOR:-0}" -ge 18 ] 2>/dev/null && NODE_OK=true || true
fi

if [ "$NODE_OK" = true ]; then
    ok "Node.js: $(node -v)  npm: $(npm -v)"
    info "Building and bundling the web UI ..."
    "$VENV_PY" tools/build_web.py
    ok "Dashboard bundled into netadmin/_webui/"
else
    warn "Node.js 18+ unavailable — skipped building the dashboard."
    warn "The daemon will run, but the web UI won't load until you build it:"
    warn "    install Node 18+, then:  $VENV_PY tools/build_web.py"
fi

# ── 4. Done ──────────────────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✔ Installation complete${NC}"
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo ""
echo "  Run it:"
echo ""
if [ -n "$ACTIVATE_HINT" ]; then
    echo -e "      ${CYAN}${ACTIVATE_HINT}${NC}"
fi
echo -e "      ${CYAN}netadmin${NC}"
echo ""
echo "  That starts the daemon and opens the dashboard. First run walks you"
echo "  through connecting your UniFi controller — no files to edit."
echo ""
