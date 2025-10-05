# UnifiOptimizer 🚀

**Professional-grade network analysis and optimization for UniFi controllers**

A comprehensive toolkit for analyzing and optimizing Ubiquiti UniFi networks. Provides expert-level insights, recommendations, and automated optimization for wireless networks with CloudKey Gen2/UDM support.

**GitHub:** https://github.com/gneitzke/UnifiOptimizer

---

## 📋 Table of Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [CloudKey User Setup](#cloudkey-user-setup)
- [Quick Start](#quick-start)
- [Usage Examples](#usage-examples)
- [What Gets Analyzed](#what-gets-analyzed)
- [Reports](#reports)
- [Troubleshooting](#troubleshooting)
- [Security](#security)

---

## ✨ Features

### Network Analysis
- **📡 RF Optimization**: Channel overlap detection, transmit power analysis, interference identification
- **🔌 Client Health**: RSSI histograms, signal quality tracking, disconnect analysis
- **📊 Historical Analysis**: 3-7 day lookback for trend detection and data-driven recommendations
- **🌐 Mesh AP Monitoring**: Uplink signal strength, reliability checks, DFS avoidance
- **📱 Device Intelligence**: Manufacturer identification, IoT device categorization, VLAN recommendations

### Interactive Optimization
- **👀 Dry-Run Mode**: Preview all changes before applying
- **✅ Interactive Approval**: Review and approve each change individually
- **📈 Smart Recommendations**: Historical data prevents repeated suggestions
- **🔄 Roaming Analysis**: Minimum RSSI threshold optimization for fast client roaming

### Reporting & Safety
- **📱 Mobile-Friendly Reports**: Responsive HTML reports viewable on any device
- **🔒 Read-Only Analysis Mode**: Safe network assessment without changes
- **💾 Profile Management**: Secure credential storage in macOS Keychain
- **📝 Change Logging**: Complete audit trail of all modifications

### WiFi 7 & 6E Support
- **📡 WiFi 7 Detection**: Identifies 802.11be capable devices (iPhone 16, Galaxy S25, etc.)
- **🔮 WiFi 6E Support**: Detects 6GHz capable devices for optimal band placement
- **📊 Executive Dashboard**: Quick health overview with visual severity indicators
- **🗄️ External Device Database**: Easy-to-update JSON file for device capabilities
  - **No code changes needed** to add new devices
  - See [`docs/DEVICE_DATABASE.md`](docs/DEVICE_DATABASE.md) for details

---

## 📦 Prerequisites

### Required Software
- **Python 3.7+** (Python 3.8+ recommended)
- **UniFi Controller** (CloudKey Gen2/Gen2+, UDM/UDM-Pro, or self-hosted)
- **Network Controller version 6.0+**
- **Administrator account** on your UniFi Controller

### Python Installation

**macOS:**
```bash
# Install Homebrew (if not already installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Python 3
brew install python3
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt update
sudo apt install python3 python3-pip
```

**Linux (CentOS/RHEL):**
```bash
sudo yum install python3 python3-pip
```

---

## 🚀 Installation

1. **Clone or download this repository:**
   ```bash
   git clone https://github.com/gneitzke/UnifiOptimizer.git
   cd UnifiOptimizer
   ```

2. **Install required Python packages:**
   ```bash
   pip3 install -r requirements.txt
   ```

3. **(Optional) Install development tools for code quality:**
   ```bash
   pip3 install -r requirements-dev.txt
   ```
   Includes: Black (formatter), Flake8 (linter), isort (import organizer)

4. **Verify installation:**
   ```bash
   python3 optimizer.py --help
   ```

---

## 👤 CloudKey User Setup

### ⚠️ IMPORTANT: Use a Local-Only Account

For security reasons, **always use a local-only administrator account** (not a Ubiquiti cloud account) with this tool.

**Why Local-Only?**
- ✅ No cloud access = reduced attack surface
- ✅ Credentials stay on your network only
- ✅ No risk of cloud account compromise
- ✅ Better security for automation

**The tool will warn you if you're using a cloud account.**

### Creating a Local-Only Account

Follow these steps to create a secure local-only administrator account:

1. **Log into your UniFi Controller** (e.g., `https://192.168.1.1`)
2. **Navigate to Settings → Admins**
3. **Click "Add New Admin"**
4. **Create the user with these settings:**
   - **Name**: `audit` (or your preferred username)
   - **Email**: Your email address (for local use only)
   - **Role**: **Admin** (required for API access)
   - **Password**: Create a strong password
   - **Important**: Do NOT check "Enable Cloud Access"
   - **Enable Two-Factor Authentication**: Optional but recommended
5. **Click "Add" to save**

### Option 2: Via UniFi OS Console (CloudKey Gen2+/UDM)

1. **SSH into your CloudKey** or access the console
2. **Navigate to UniFi OS Settings → Admins**
3. **Add a new admin** with:
   - **Account Type**: Local Account
   - **Role**: Admin
   - **Username/Password**: Your chosen credentials

### Why Admin Access?

The UniFi API requires **Admin** or **Full Management** role for:
- Reading device configurations (APs, switches, clients)
- Accessing historical statistics and events
- Applying optimization changes (if you choose to use optimization mode)

**⚠️ Read-only accounts will not work** - The tool automatically checks for API access after login and will show a clear error message if you don't have sufficient permissions.

**Note**: The tool defaults to **read-only analysis mode**, so your network is safe even with admin credentials.

---

## 🎯 Quick Start

### Interactive Mode (Easiest)

```bash
python3 optimizer.py
```

Follow the prompts to:
1. Enter your controller IP address
2. Enter your username (e.g., `audit`)
3. Enter your password (stored securely in macOS Keychain)
4. Select analysis type from the menu

### Main Menu Options

When you run `python3 optimizer.py`, you'll see:

```
╭────────────────────────────────────────╮
│     UniFi Network Optimizer            │
╰────────────────────────────────────────╯

Available Modes:

1. Client Health Diagnostics
   Colored histograms, health scores, problem detection

2. Expert Network Analysis
   3-day lookback, mesh checks, channel/power optimization

3. Dry-Run Mode
   Safe simulation showing impact without changes

4. Interactive Mode
   Apply changes with approval for each one

5. Exit
```

---

## 💡 Usage Examples

### Analyze Network (Safe, Read-Only)
```bash
python3 optimizer.py analyze --host https://YOUR_CONTROLLER_IP --username admin
```

### Verbose Mode (Detailed API Logging)
```bash
python3 optimizer.py analyze --host https://YOUR_CONTROLLER_IP --username admin --verbose
```
**Shows real-time API debugging information:**
- 📤 All GET/POST/PUT requests with full URLs and payloads
- 📥 Complete response data formatted as readable JSON
- ⚠️ Detailed error messages with stack traces
- 🔍 Perfect for troubleshooting and identifying bugs
- 🎨 Color-coded output (cyan=GET, yellow=PUT, magenta=POST, red=errors)

**See:** [`docs/VERBOSE_MODE.md`](docs/VERBOSE_MODE.md) for complete guide

**Also creates:** `verbose_YYYYMMDD_HHMMSS.log` with timestamped records

### Preview Changes (Dry-Run)
```bash
python3 optimizer.py apply --host https://YOUR_CONTROLLER_IP --username admin --dry-run
```

### Apply Changes Interactively
```bash
python3 optimizer.py apply --host https://YOUR_CONTROLLER_IP --username admin
```

### Using Saved Profiles
After first login, credentials are saved to macOS Keychain:
```bash
python3 optimizer.py --profile default
```

---

## 🔍 What Gets Analyzed

### Network Infrastructure
- **Access Points**: Channel usage, power levels, uplink status
- **Mesh APs**: Special reliability checks for wireless uplinks
- **Channels**: 2.4GHz (1, 6, 11 non-overlapping) and 5GHz (DFS avoidance)
- **Power Levels**: Optimized for roaming and coverage

### Client Health (3-Day History)
- **Signal Strength**: RSSI categorized (Excellent/Good/Fair/Poor/Critical)
- **Disconnections**: Pattern tracking and frequency analysis
- **Roaming**: Behavior analysis and sticky client detection
- **Health Scores**: A-F grades for each client

### Expert Recommendations

Prioritized by severity:
- 🔴 **CRITICAL**: Immediate action needed (mesh uplink < -80 dBm)
- 🟠 **HIGH**: Multiple clients affected or frequent issues
- 🟡 **MEDIUM**: Optimization opportunities
- 🔵 **LOW**: General best practices

---

## 📊 Reports

### HTML Reports

Generated reports include:
- **Network Health Overview**: Overall score and key metrics
- **Access Point Analysis**: Per-AP performance and recommendations
- **Client Health Dashboard**: Signal strength histograms, disconnect patterns
- **Optimization Recommendations**: Prioritized action items with explanations

Reports are saved to the `reports/` directory and can be viewed in any web browser.

### Viewing Reports
```bash
open reports/network_analysis_YYYYMMDD_HHMMSS.html
```

---

## 🛠️ Troubleshooting

### Connection Issues

**Problem**: Can't connect to controller
```
Error: Connection refused
```

**Solutions**:
- Verify controller IP address is correct
- Ensure you're using `https://` (most CloudKeys use HTTPS)
- Check firewall allows access to port 443/8443
- Try accessing the controller web interface first

### Authentication Issues

**Problem**: Login fails with correct credentials
```
Error: Authentication failed
```

**Solutions**:
- Verify user has **Admin** role (not Read-Only)
- Check for typos in username/password
- If 2FA is enabled, ensure it's configured correctly
- Try creating a new dedicated user for the tool

### Rate Limit Errors

**Problem**: Too many login attempts
```
Error: 429 - Rate limit exceeded
```

**Solutions**:
- Wait 15-30 minutes for automatic reset
- Restart your CloudKey to clear immediately
- Use saved profiles to avoid repeated authentication

### SSL Certificate Warnings

The tool disables SSL verification by default for self-signed certificates. If you see warnings, this is normal for CloudKey devices with self-signed certificates.

---

## 🔒 Security

### Credential Storage

- Passwords are stored securely in **macOS Keychain** (encrypted by the OS)
- Credentials are only accessed when needed
- You can delete stored credentials anytime using Keychain Access app

### Network Safety

- **Default mode is read-only** - no changes are made without explicit permission
- All optimization changes require confirmation
- Dry-run mode lets you preview changes safely
- Complete audit trail of all modifications

### Best Practices

1. Create a dedicated user (e.g., `audit`) with admin access
2. Use strong passwords
3. Enable 2FA if your controller supports it
4. Review recommendations before applying changes
5. Start with dry-run mode to understand impact

---

## 📁 Project Structure

```
unifi-network-optimizer/
├── api/                    # API modules for controller communication
│   ├── cloudkey_gen2_client.py
│   ├── csrf_token_manager.py
│   └── ...
├── core/                   # Core analysis modules
│   ├── optimize_network.py
│   ├── diagnose_clients.py
│   ├── advanced_analyzer.py
│   └── ...
├── utils/                  # Utility modules
│   ├── keychain.py        # Secure credential storage
│   └── ...
├── tests/                  # Test modules
│   ├── cloudkey/
│   └── ...
├── reports/                # Generated HTML reports (gitignored)
├── optimizer.py            # Main interactive script
├── requirements.txt        # Python dependencies
└── README.md              # This file
```

---

## 🛠️ Developer Setup

**Contributing to UnifiOptimizer?** Follow these steps to set up your development environment:

### 1. Clone & Install

```bash
git clone https://github.com/gneitzke/UnifiOptimizer.git
cd UnifiOptimizer

# Install runtime dependencies
pip3 install -r requirements.txt

# Install development tools (REQUIRED)
pip3 install -r requirements-dev.txt
```

### 2. VS Code Setup (Recommended)

Open project in VS Code:
```bash
code .
```

Install recommended extensions when prompted (or manually):
- **Python** (ms-python.python)
- **Black Formatter** (ms-python.black-formatter)
- **isort** (ms-python.isort)
- **Flake8** (ms-python.flake8)

**Auto-format on save is already configured!** ✨

### 3. Development Workflow

**Before every commit, run these commands:**

```bash
./format_code.sh   # Auto-format all code
./check_code.sh    # Verify code quality
```

Expected output:
```
✅ All checks passed!
```

### 4. What Gets Auto-Formatted

When you save a file in VS Code:
- ✅ Code formatted with **Black**
- ✅ Imports organized with **isort**
- ✅ Trailing whitespace removed
- ✅ Final newline added

**Example:**
```python
# Before save (messy)
from rich.console import Console
import sys
def test(  x,y,   z  ):
    return x+y+z

# After save (clean)
import sys

from rich.console import Console


def test(x, y, z):
    return x + y + z
```

### 5. Code Quality Tools

| Tool | Purpose | When It Runs |
|------|---------|--------------|
| **Black** | Code formatter | On save in VS Code |
| **isort** | Import organizer | On save in VS Code |
| **Flake8** | Linter (finds bugs) | Live in VS Code |

**Manual commands:**
```bash
black api/                    # Format specific directory
isort core/                   # Sort imports
flake8 utils/                 # Check for issues
```

### 6. Pre-Commit Checklist

Before committing:

- [ ] Run `./format_code.sh`
- [ ] Run `./check_code.sh` → Should see `✅ All checks passed!`
- [ ] Test your changes manually
- [ ] Update documentation if needed

### 📚 Developer Documentation

- **[Developer Setup Guide](docs/DEVELOPER_SETUP.md)** - Complete setup instructions
- **[Code Quality Guide](docs/CODE_QUALITY.md)** - Detailed formatting/linting info
- **[Quick Reference](CODE_QUALITY_QUICKREF.md)** - Common commands

---

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Follow the developer setup guide above
4. Make your changes
5. Run formatting: `./format_code.sh`
6. Run checks: `./check_code.sh`
7. Commit: `git commit -m "feat: Add amazing feature"`
8. Push: `git push origin feature/amazing-feature`
9. Open a Pull Request

**All contributions must pass code quality checks.**

---

## 📄 License

This project is open source, MIT License, and available for personal and commercial use.

---

## 🙏 Acknowledgments

Built for the UniFi community to help optimize wireless networks and improve client experience.

