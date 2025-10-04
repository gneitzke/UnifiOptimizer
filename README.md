# UniFi Network Optimizer 🚀# UniFi Network Optimizer 🚀```



**Professional-grade network analysis and optimization for UniFi controllers**██╗   ██╗███╗   ██╗██╗███████╗██╗     █████╗ ██╗   ██╗██████╗ ██╗████████╗



A comprehensive toolkit for analyzing and optimizing Ubiquiti UniFi networks. Provides expert-level insights, recommendations, and automated optimization for wireless networks with CloudKey Gen2/UDM support.**Professional-grade network analysis and optimization for UniFi controllers**██║   ██║████╗  ██║██║██╔════╝██║    ██╔══██╗██║   ██║██╔══██╗██║╚══██╔══╝



---██║   ██║██╔██╗ ██║██║█████╗  ██║    ███████║██║   ██║██║  ██║██║   ██║   



## 📋 Table of Contents## Quick Start██║   ██║██║╚██╗██║██║██╔══╝  ██║    ██╔══██║██║   ██║██║  ██║██║   ██║   



- [Features](#features)╚██████╔╝██║ ╚████║██║██║     ██║    ██║  ██║╚██████╔╝██████╔╝██║   ██║   

- [Prerequisites](#prerequisites)

- [Installation](#installation)### 1. Run the Optimizer ╚═════╝ ╚═╝  ╚═══╝╚═╝╚═╝     ╚═╝    ╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚═╝   ╚═╝   

- [CloudKey User Setup](#cloudkey-user-setup)

- [Quick Start](#quick-start)```bash```

- [Usage Examples](#usage-examples)

- [What Gets Analyzed](#what-gets-analyzed)python3 optimizer.py

- [Reports](#reports)

- [Best Practices](#best-practices)```# UniFi Audit Tool

- [Troubleshooting](#troubleshooting)



---

### 2. Run TestsA comprehensive network analysis and optimization tool for Ubiquiti UniFi environments. UniFi Audit analyzes your wireless network, identifies issues, and provides actionable recommendations.

## ✨ Features

```bash

### Network Analysis

- **📡 RF Optimization**: Channel overlap detection, transmit power analysis, interference identificationpython3 run_all_tests.py## Features

- **🔌 Client Health**: RSSI histograms, signal quality tracking, disconnect analysis

- **📊 Historical Analysis**: 3-7 day lookback for trend detection and data-driven recommendations```

- **🌐 Mesh AP Monitoring**: Uplink signal strength, reliability checks, DFS avoidance

- **📱 Device Intelligence**: Manufacturer identification, IoT device categorization, VLAN recommendations- **Network Analysis**: Detects configuration issues, suboptimal settings, and client connection problems



### Interactive OptimizationThat's it! 🎉- **RF Optimization**: Recommends channel and transmit power settings to minimize interference

- **👀 Dry-Run Mode**: Preview all changes before applying

- **✅ Interactive Approval**: Review and approve each change individually- **Client Distribution Analysis**: Identifies overloaded APs and suggests client balancing strategies

- **📈 Smart Recommendations**: Historical data prevents repeated suggestions

- **🔄 Roaming Analysis**: Minimum RSSI threshold optimization for fast client roaming---- **Interactive Reports**: Generates detailed HTML reports with actionable insights

- **📱 Mobile-Friendly Reports**: Responsive HTML reports viewable on any device

- **Direct Optimization**: Can apply recommended changes directly to your controller

### Safety Features

- **🔒 Read-Only Analysis Mode**: Safe network assessment without changes## What This Does- **CloudKey Compatibility**: Works with both CloudKey Gen1 and Gen2/UDM devices

- **💾 Profile Management**: Secure credential storage in macOS Keychain

- **📝 Change Logging**: Complete audit trail of all modifications

- **⏮️ Rollback Support**: Undo changes if needed

The **optimizer.py** provides expert-level network analysis with:## Quick Start

---



## 📦 Prerequisites

✅ **Client Health Diagnostics** - RSSI histograms, signal quality, disconnection tracking  1. **Installation**:

### 1. Python Installation

✅ **Expert Network Analysis** - 3-day historical lookback, mesh AP reliability checks     ```

**Check if Python is installed:**

```bash✅ **Dry-Run Mode** - See what would change without making changes     pip install -r requirements.txt

python3 --version

```✅ **Interactive Mode** - Apply optimizations with your approval     ```



If you see `Python 3.7` or higher, you're good! Otherwise:



**macOS:**### Special Feature: Mesh AP Reliability2. **Run the tool**:

```bash

# Using Homebrew (recommended)If you have wireless mesh APs (remote locations, acreage, etc.), the analyzer gives them **special reliability-focused treatment**:   ```

brew install python3

- Monitors uplink signal strength (alerts if < -80 dBm)   python unifi_audit.py

# Or download from python.org

# Visit: https://www.python.org/downloads/- Avoids DFS channels (prevents radar-induced outages)   ```

```

- Recommends optimal power settings for stability

**Linux (Ubuntu/Debian):**

```bash- Suggests intermediate hops instead of long-distance links3. **Follow the interactive prompts** to connect to your UniFi controller and analyze your network.

sudo apt update

sudo apt install python3 python3-pip

```

---## Requirements

**Linux (CentOS/RHEL):**

```bash

sudo yum install python3 python3-pip

```## Main Menu- Python 3.7+



### 2. UniFi Controller Requirements- UniFi Controller (On-premises or CloudKey)



- UniFi CloudKey Gen2/Gen2+, UDM/UDM-Pro, or self-hosted controllerWhen you run `python3 optimizer.py`, you'll see:- Administrator credentials for your UniFi Controller

- Network Controller version 6.0+

- Administrator account access (see [CloudKey User Setup](#cloudkey-user-setup))



---```## Project Structure



## 🚀 Installation╭────────────────────────────────────────╮



1. **Clone or download this repository:**│     UniFi Network Optimizer      │```

   ```bash

   git clone https://github.com/gneitzke/unifi-network-optimizer.git╰────────────────────────────────────────╯unifi_audit/

   cd unifi-network-optimizer

   ```├── api/                    # API modules for controller communication



2. **Install required Python packages:**Available Modes:│   ├── client.py           # API client for UniFi controllers

   ```bash

   pip3 install -r requirements.txt│   ├── cloudkey_compat.py  # CloudKey compatibility layer

   ```

1. Client Health Diagnostics│   └── ...

3. **Verify installation:**

   ```bash   Colored histograms, health scores, problem detection├── core/                   # Core analysis modules

   python3 optimizer.py --help

   ```│   ├── analyzer.py         # Main analysis engine



---2. Expert Network Analysis│   ├── modeler.py          # Network modeling capabilities



## 👤 CloudKey User Setup   3-day lookback, mesh checks, channel/power optimization│   └── ...



For security, create a dedicated read-only admin user for this tool:├── tests/                  # Test modules



### Option 1: Via UniFi Web Interface (Recommended)3. Dry-Run Mode│   ├── cloudkey/           # CloudKey-specific tests



1. **Log into your UniFi Controller** (e.g., `https://192.168.1.1`)   Safe simulation showing impact without changes│   └── ...



2. **Navigate to Settings → Admins**├── ui/                     # User interface components



3. **Click "Add New Admin"**4. Interactive Mode│   └── elements.py         # Terminal UI components



4. **Create the user with these settings:**   Apply changes with approval for each one├── utils/                  # Utility modules

   - **Name**: `audit` (or your preferred username)

   - **Email**: Your email address│   ├── constants.py        # System constants and defaults

   - **Role**: **Admin** (required for API access)

   - **Password**: Create a strong password5. Exit│   └── helpers.py          # Helper functions

   - **Enable Two-Factor Authentication**: Optional but recommended

```└── unifi_audit.py          # Main application

5. **Click "Add" to save**

```

### Option 2: Via UniFi OS Console (CloudKey Gen2+/UDM)

---

1. **SSH into your CloudKey** or access the console

## Detailed Documentation

2. **Navigate to UniFi OS Settings → Admins**

## What Gets Analyzed

3. **Add a new admin** with:

   - **Account Type**: Local AccountSee the [docs](/docs) directory for detailed documentation on:

   - **Role**: Admin

   - **Username/Password**: Your chosen credentials### Network Infrastructure



### Why Admin Access?- **Access Points**: Channel usage, power levels, uplink status- [Complete Feature List](/docs/README.md)



The UniFi API requires **Admin** role for:- **Mesh APs**: Special reliability checks for wireless uplinks- [CloudKey Compatibility](/docs/CLOUDKEY_COMPATIBILITY.md)

- Reading device configurations (APs, switches, clients)

- Accessing historical statistics and events- **Channels**: 2.4GHz (1, 6, 11 non-overlapping) and 5GHz (DFS avoidance)- API and Extension Documentation

- Applying optimization changes (if you choose to use `apply` mode)

- **Power Levels**: Optimized for roaming and coverage- Configuration Options

**Note**: The tool defaults to **read-only analysis mode**, so your network is safe even with admin credentials.



---

### Client Health (3-Day History)## Security

## 🎯 Quick Start

- **Signal Strength**: RSSI categorized (Excellent/Good/Fair/Poor/Critical)

### Interactive Mode (Easiest)

- **Disconnections**: Pattern tracking and frequency analysisThis tool requires administrative access to your UniFi controller. Credentials are used only for the current session and are never stored permanently unless you explicitly enable credential caching.

```bash

python3 optimizer.py- **Roaming**: Behavior analysis and sticky client detection

```

- **Health Scores**: A-F grades for each client## Quick Start

Follow the prompts to:

1. Enter your controller IP address```bash

2. Enter your username (e.g., `audit`)

3. Enter your password (stored securely in macOS Keychain)### Expert Recommendationsunzip unifi_easytoolkit_plus.zip

4. Select analysis type

Prioritized by severity:cd unifi_easytoolkit_plus

### Command-Line Mode

- 🔴 **CRITICAL**: Immediate action needed (mesh uplink < -80 dBm)

**Analyze Network (Safe, Read-Only):**

```bash- 🟠 **HIGH**: Multiple clients affected or frequent issuespython3 Setup.py         # one-time: installs 'requests' and 'rich'

python3 core/optimize_network.py analyze --host https://YOUR_CONTROLLER_IP --username admin

```- 🟡 **MEDIUM**: Optimization opportunitiespython3 DeepAnalyse.py   # follow the prompts



**Preview Changes (Dry-Run):**- 🔵 **LOW**: General best practices```

```bash

python3 core/optimize_network.py apply --host https://YOUR_CONTROLLER_IP --username admin --dry-run

```

---## Network Topology Overview

**Apply Changes Interactively:**

```bash```

python3 core/optimize_network.py apply --host https://YOUR_CONTROLLER_IP --username admin --interactive

```## Installation    ┌─────────────────────────────────────────────────────────────┐



### Using Saved Profiles    │                Your UniFi Network                           │



**Save credentials for future use:**### 1. Install Requirements    │                                                             │

```bash

python3 core/optimize_network.py analyze --host https://YOUR_CONTROLLER_IP --username admin --save-profile default```bash    │  📡 Cloud Key/UDM ← 🔌 → 📶 AP1 ← 📱💻📱 Clients         │

```

pip install -r requirements.txt    │       ↑                    ↓                                │

**Use saved profile:**

```bash```    │       │                   📶 AP2 ← 📱💻 Clients            │

python3 core/optimize_network.py analyze --profile default

```    │       │                    ↓                                │



**List all profiles:**### 2. Configure Controller    │       └─ 🔍 Network Doctor 📶 AP3 ← 📱💻📱 Clients         │

```bash

python3 core/optimize_network.py --list-profilesYou'll be prompted for:    │          (This Toolkit)                                     │

```

- **Controller URL**: e.g., `https://192.168.1.119` or `https://unifi.yourdomain.com`    │                                                             │

---

- **Username**: Your UniFi admin username    │  🩺 Diagnoses: Channels, Power, RSSI, Client Distribution  │

## 📖 Usage Examples

- **Password**: Your UniFi admin password    │  ⚡ Optimizes: Band Steering, Load Balance, Min RSSI       │

### 1. First-Time Network Health Check

- **Site**: Usually `default`    │  🎯 Applies:   Radio Settings, WLAN Config                 │

```bash

# Run complete analysis with 7-day historical lookback    └─────────────────────────────────────────────────────────────┘

python3 optimizer.py

---```

# Select: Option 1 - Client Health Diagnostics

# This provides a comprehensive overview without making changes

```

## Usage Examples## What it does

### 2. Optimize Channels and Power

1. **Audit** your UniFi Network (Cloud Key/UDM) via API.

```bash

# See what would change### Quick Health Check2. Generate a comprehensive **static HTML report** with:

python3 core/optimize_network.py apply --profile default --dry-run

```bash   - **Summary**: Network overview with device counts and RSSI analysis

# Apply changes with approval for each

python3 core/optimize_network.py apply --profile default --interactivepython3 optimizer.py   - **AP Overview**: Enhanced table with channels, widths, clients, and historical metrics

```

# Select option 1: Client Health Diagnostics   - **📈 Historical Analysis**: Event patterns, authentication failures, interference alarms

### 3. Generate Reports Only

```   - **🔄 Roaming & RSSI Analysis**: Client behavior patterns and optimization recommendations

```bash

# Run analysis and generate HTML report   - **Recommendations**: Data-driven suggestions based on both current state and historical patterns

python3 regenerate_report.py

```### Full Network Analysis   - **Steps Log**: Complete audit trail of what was analyzed



Reports are saved in `reports/` directory with timestamps.```bash3. **Model fixes** (plan-*.json) with intelligent wireless optimizations based on historical data.



### 4. Monitor Specific Issuespython3 optimizer.py4. **Apply fixes interactively** — one change at a time, each with a y/N prompt and explanation.



```bash# Select option 2: Expert Network Analysis

# Check client health

python3 core/diagnose_clients.py --profile default```## Features



# Focus on weak signal clients

python3 core/diagnose_clients.py --profile default --detailed

```### See What Would Change (Safe)### 📈 Advanced Historical Analysis



---```bashThe toolkit now provides **enterprise-grade network intelligence** by analyzing historical logs:



## 🔍 What Gets Analyzedpython3 optimizer.py



### RF Environment# Select option 3: Dry-Run Mode#### **🔍 Forward Roaming Analysis (Predictive)**

- ✅ **Channel Overlap**: Detects APs on overlapping channels causing interference

- ✅ **Non-Standard Channels**: Identifies use of channels outside 1, 6, 11 (2.4 GHz)```- **Sticky Client Detection**: Identifies devices that should roam but don't

- ✅ **Same-Channel Density**: Flags 3+ APs on same channel

- ✅ **DFS Channel Issues**: Monitors radar interference on 5 GHz DFS channels- **Coverage Gap Analysis**: Finds areas needing AP improvement  

- ✅ **Transmit Power**: Recommends optimal power levels (typically Medium)

### Apply Optimizations- **Band Steering Optimization**: Detects suboptimal client distribution

### Client Health

- ✅ **RSSI Distribution**: Signal strength histograms per AP```bash

- ✅ **Weak Signal Clients**: Identifies clients with poor connectivity (< -70 dBm)

- ✅ **Disconnection Patterns**: Tracks frequent disconnects and roaming issuespython3 optimizer.py#### **📡 Backward Roaming Analysis (Diagnostic)**

- ✅ **Band Utilization**: 2.4 GHz vs 5 GHz client distribution

- ✅ **Airtime Utilization**: Per-AP average airtime to detect congestion# Select option 4: Interactive Mode- **Worst RSSI Scenario Detection**: Finds clients disconnecting at very poor signal levels



### Roaming Optimization# You'll approve each change individually- **Percentile Analysis**: 10th/25th/50th/90th RSSI breakdowns for threshold optimization

- ✅ **Minimum RSSI Thresholds**: Data-driven recommendations for fast roaming

- ✅ **802.11r/k/v Support**: Fast roaming feature detection```- **Root Cause Analysis**: Understands WHY disconnects happen

- ✅ **Sticky Client Detection**: Identifies clients that won't roam

- ✅ **Percentile Analysis**: 10th/25th/50th/90th RSSI breakdowns- **Intelligent Recommendations**: Data-driven minimum RSSI suggestions



### Network Intelligence---

- ✅ **Device Categorization**: IoT, cameras, gaming, printers, streaming devices

- ✅ **Manufacturer Analysis**: 62+ device patterns recognized#### **⚡ Enhanced Connection Log Analysis**

- ✅ **VLAN Recommendations**: Suggests IoT isolation for security

- ✅ **QoS Suggestions**: Camera/VoIP traffic prioritization## Example Output- **Client Connection Pattern Detection**: Identifies high disconnect clients, sticky clients, and excessive roamers



### Mesh Networks- **Access Point Performance Metrics**: Finds problematic APs with weak signal disconnects

- ✅ **Uplink Signal Strength**: Monitors wireless backhaul quality

- ✅ **Hop Recommendations**: Suggests intermediate APs for long distances```- **Hourly Pattern Analysis**: Detects time periods with high disconnect rates

- ✅ **DFS Avoidance**: Prevents radar-induced mesh outages

- ✅ **Reliability Scoring**: Overall mesh health assessmentExpert Network Analysis- **Targeted Recommendations**: Generates specific suggestions to improve network stability



---• Historical lookback: 3 days



## 📊 Reports• RSSI and signal quality analysis#### **🎯 Pattern Recognition**



The tool generates two report formats:• Mesh AP reliability checks- **Excessive Roamers**: Devices with unstable roaming patterns (>30% roam ratio)



### 1. Interactive Report (`report.html`)• Channel and power optimization- **Authentication Failures**: Security configuration issues over time

- **Chart.js Visualizations**: Interactive graphs and charts

- **Responsive Design**: Desktop, tablet, and mobile optimized• Client health diagnostics- **Interference Correlation**: Links alarms to performance degradation

- **Detailed Analysis**: Expandable sections with recommendations

- **Best for**: Viewing on computers and tablets- **Long-term Trends**: Track network health evolution



### 2. Shareable Report (`report_share.html`)✓ Found 5 access points

- **Static PNG Charts**: Embedded matplotlib images

- **iMessage/Email Compatible**: Works without JavaScript  • 2 wireless mesh APs (reliability-focused)### Smart URL Handling

- **Lightweight**: Can be easily shared via messaging apps

- **Best for**: Mobile sharing, email attachments  • 3 wired uplink APs- **Automatic HTTPS prefix**: Just enter `192.168.1.119` and it becomes `https://192.168.1.119`



Both reports include:- **HTTP to HTTPS conversion**: Automatically converts `http://` URLs to `https://` for security

- Executive summary with health score

- Per-AP analysis with signal heatmapsMesh AP Uplink Status:

- Client distribution and RSSI histograms

- Channel recommendations with smart tracking  Remote Barn AP: -72 dBm (Good)### 🔄 Multi-Version UniFi API Compatibility

- Manufacturer analysis and device categorization

- Actionable recommendations prioritized by severity  South Field AP: -78 dBm (Fair)- **Smart API Path Detection**: Automatically tries modern (`/proxy/network/api`) and legacy (`/api`) endpoints



---- **Graceful Fallback**: Continues operation when optional endpoints are not available



## 🎓 Best Practices✓ Analyzed 15 clients- **Version Awareness**: Works with UDM Pro/SE, Cloud Key Gen2, and older controllers



### Channel Selection  ⚠ 3 clients with weak signal- **Detailed Documentation**: See `API_COMPATIBILITY.md` for controller version details

- ✅ **2.4 GHz**: Use only channels 1, 6, or 11 (non-overlapping)

- ✅ **5 GHz**: Prefer non-DFS channels (36-48, 149-165) for stability  ⚠ 2 clients with frequent disconnects- **Compatibility Checker Tool**: Run `python check_api_compatibility.py` to test which endpoints are available on your controller

- ✅ **DFS Channels**: Only use if you understand radar detection implications



### Transmit Power

- ✅ **Medium Power**: Best for multi-AP deployments (recommended)Found 6 Optimization Opportunities### Authentication Support

- ✅ **Avoid High Power**: Can cause coverage overlap and "sticky clients"

- ✅ **Low Power**: Only for dense AP deployments (conference rooms, etc.)● 1 Critical ● 2 High Priority ● 2 Medium ● 1 LowYou can authenticate with:



### Roaming Optimization- **Username/Password (local admin)** — recommended for Cloud Keys without API Keys

- ✅ **Enable Minimum RSSI**: Set to -70 to -75 dBm for fast roaming

- ✅ **Enable 802.11r**: Fast roaming for VoIP/video applications1. South Field AP CRITICAL- **Username/Password with 2FA** — full support for two-factor authentication

- ✅ **Consistent Settings**: Same config across all APs

   Mesh uplink RSSI -82 dBm is too weak- **API Key** — if your UniFi Network exposes API keys

### Client Distribution

- ✅ **Good RSSI**: Aim for > -60 dBm for best performance   Recommendation: Reposition AP or add intermediate hop

- ✅ **Fair RSSI**: -60 to -70 dBm acceptable for most clients

- ✅ **Poor RSSI**: < -70 dBm indicates need for more APs or relocation   Type: Mesh ReliabilityThe script will prompt you for whichever you choose. Passwords and 2FA tokens are never stored.



### Network Segmentation

- ✅ **IoT VLAN**: Isolate smart home devices, cameras, sensors

- ✅ **Guest Network**: Separate SSID for visitors2. Kitchen AP HIGH### Comprehensive Testing

- ✅ **Management VLAN**: Controller and AP management traffic

   Change 2.4GHz channel: 1 → 6- **Unit tests** for URL normalization and authentication flows

---

   Reason: 2 clients with 7 disconnects. Reduce interference.- **Integration tests** with mock servers for end-to-end validation

## 🔧 Troubleshooting

   Affects 2 client(s)- **Manual verification** script for testing with your actual hardware

### "Authentication Failed"

```

**Problem**: Wrong username/password

## Typical Flow

**Solution**:

```bash---- First run: you'll be prompted for host (e.g. `192.168.1.119` - the `https://` prefix is automatically added), site (`default`), auth method (`user` or `key`), and mode (`deep` is a good start).

# Remove saved profile and re-enter credentials

python3 core/optimize_network.py --list-profiles- If your account requires 2FA, you will be prompted for your verification code.

# Note the profile name, then delete it from keychain

# Run again and enter correct credentials## Testing- A report is saved like `report-YYYYMMDD-HHMMSS.html` and opened automatically.

```

- A `state.json` is saved to remember your **last audit file**, **host**, **site**, and **timestamp** (no passwords or tokens).

### "Connection Refused" or "Timeout"

Run the test suite to verify everything works:

**Problem**: Can't reach controller

## Menu on Subsequent Runs

**Solution**:

1. Verify controller IP: `ping YOUR_CONTROLLER_IP````bash```

2. Ensure you're on same network as controller

3. Check firewall isn't blocking port 443/8443python3 run_all_tests.py[1] Run audit again

4. Verify controller is running: access web UI in browser

```[2] Model fixes from last audit (make plan-*.json)

### "SSL Certificate Error"

[3] Apply fixes from last plan (prompts)

**Problem**: Self-signed certificate

Tests cover:[4] Full pipeline: audit → model → apply (prompts)

**Solution**: This is normal for CloudKey devices. The tool accepts self-signed certificates.

- CloudKey Gen2+ authentication[5] Clear saved state

### "No Module Named 'requests'"

- API connectivity```

**Problem**: Dependencies not installed

- CSRF token management

**Solution**:

```bash- Change application (dry-run)## Best Practices Applied

pip3 install -r requirements.txt

```- Client health analysisThe toolkit uses conservative wireless best practices:



### Permission Denied (macOS Keychain)- Mesh AP detection- **2.4 GHz width** → 20 MHz (reduces interference)



**Problem**: Keychain access denied- **2.4 GHz channels** → 1/6/11 only (non-overlapping)



**Solution**: Grant Terminal/Python access in System Preferences → Security & Privacy → Privacy → Full Disk Access---- **5 GHz width** → 80 MHz (40 MHz in very dense environments)



### Reports Show Old Data- **DFS avoidance** → suggests non-DFS channels if radar hits are suspected



**Problem**: Cached data from previous analysis## Best Practices Applied- **Minimum RSSI** → enables at -75 dBm to disconnect weak clients



**Solution**:- **Load balancing** → enables to distribute clients across APs

```bash

# Force fresh data collection### Channels- **Band steering** → prefers 5 GHz for better performance

rm -rf reports/*.html

python3 regenerate_report.py✅ **2.4GHz**: Use only 1, 6, 11 (non-overlapping)  

```

✅ **5GHz**: Prefer non-DFS (36-48, 149-165) for reliability  You can safely **decline** any change at apply time.

---

❌ **Avoid**: Overlapping 2.4GHz channels (2-5, 7-10)  

## 📁 Project Structure

❌ **Avoid**: DFS channels on mesh APs (radar = outages)  ## RSSI Configuration Guidelines

```

unifi-network-optimizer/

├── api/                        # Controller API clients

│   ├── cloudkey_gen2_client.py # CloudKey Gen2/UDM API### Power Levels### Understanding Minimum RSSI Thresholds

│   ├── csrf_token_manager.py   # CSRF token handling

│   └── cloudkey_jwt_helper.py  # JWT authentication✅ **MEDIUM**: Best for multi-AP deployments (recommended)  The toolkit's historical analysis helps you choose the optimal minimum RSSI threshold based on your actual network patterns:

├── core/                       # Core analysis modules

│   ├── network_analyzer.py     # RF and channel analysis✅ **AUTO**: Let UniFi optimize dynamically  

│   ├── advanced_analyzer.py    # Deep network insights

│   ├── channel_optimizer.py    # Smart channel recommendations❌ **HIGH**: Causes sticky clients and interference in multi-AP setups  - **-80 dBm** → Very weak, usually not worth keeping clients connected. Only set this high if you absolutely need roaming to work aggressively.

│   ├── manufacturer_analyzer.py # Device categorization

│   ├── html_report_generator.py # Interactive reports- **-75 dBm** → A good balance for most environments, forces devices to roam when signal is getting poor. ✅ **Recommended default**

│   ├── html_report_generator_share.py # Shareable reports

│   └── optimize_network.py     # Main CLI interface### Mesh APs- **-70 dBm** → More aggressive. Works well in dense deployments where another AP is nearby to pick up the client.

├── utils/                      # Utility functions

│   ├── keychain.py            # Credential management✅ **Uplink RSSI**: Keep > -75 dBm for reliability  - **-65 dBm or higher** → Very aggressive; best only if you have excellent AP coverage and don't want clients hanging onto marginal links at all.

│   └── ...

├── reports/                    # Generated HTML reports (git-ignored)✅ **Non-DFS Channels**: Avoid radar-induced disconnections  

├── data/                       # Configuration files

│   └── config.json            # Default settings✅ **Moderate Power**: MEDIUM or AUTO (not HIGH)  ### Historical RSSI Analysis

├── optimizer.py               # Interactive menu interface

├── regenerate_report.py       # Report generation script✅ **Multiple Hops**: Better than one long-distance link  The toolkit now analyzes your actual disconnect patterns to recommend optimal thresholds:

├── requirements.txt           # Python dependencies

└── README.md                  # This file

```

### Client Health```

---

✅ **Good RSSI**: Aim for > -60 dBm for best performance  📊 RSSI Threshold Analysis:

## 🔐 Security Notes

✅ **Low Disconnects**: < 3 per client over 3 days is healthy  ├── Current worst disconnect: -89 dBm

- **Credentials**: Stored securely in macOS Keychain (encrypted)

- **Reports**: May contain network topology - keep confidential✅ **Smooth Roaming**: 15-20% AP coverage overlap ideal  ├── Recommended minimum RSSI: -71 dBm (high confidence)

- **Git**: Sensitive files excluded via `.gitignore`

- **API Access**: Uses HTTPS with certificate verification disabled for CloudKey self-signed certs├── 90th percentile disconnect RSSI: -78 dBm

- **Logs**: No passwords are logged or stored in plain text

---└── Very poor disconnects: 12% of clients need attention

---

```

## 📝 License

## Project Structure

This project is provided as-is for personal and commercial use. See LICENSE file for details.

**Benefits of Data-Driven RSSI Settings:**

---

```- **Reduce Poor Disconnects**: Prevent clients from hanging onto weak signals

## 🤝 Contributing

UniFi Network Optimizer/- **Improve Roaming**: Force devices to find better APs proactively

Contributions welcome! Please:

1. Fork the repository├── optimizer.py       ← Main interface (run this!)- **Optimize Performance**: Maintain consistent connection quality

2. Create a feature branch

3. Test your changes thoroughly├── run_all_tests.py        ← Test suite- **Prevent Sticky Clients**: Encourage proper AP selection

4. Submit a pull request with clear description

├── README.md              ← You are here

---

├── requirements.txt       ← Python dependencies### Example Historical Analysis Output

## 📞 Support

│```

For issues or questions:

1. Check [Troubleshooting](#troubleshooting) section├── api/                   ← UniFi API clients📊 ANALYSIS RESULTS:

2. Review closed issues on GitHub

3. Open a new issue with detailed description and logs│   ├── cloudkey_gen2_client.py    (CloudKey Gen2+ support)├── Total clients tracked: 47



---│   └── csrf_token_manager.py      (Authentication)├── Roaming clients: 23



## 🙏 Acknowledgments│├── Sticky clients: 3 (should roam but don't)  



Built for the UniFi community to optimize wireless networks with data-driven insights.├── core/                  ← Analysis engines├── Excessive roamers: 2 (roam too frequently)



**Tested with:**│   ├── network_analyzer.py        (Expert analysis)└── Poor RSSI disconnects: 8 (need coverage improvement)

- CloudKey Gen2/Gen2+

- UniFi Dream Machine (UDM/UDM-Pro)│   ├── client_health.py           (RSSI, health scoring)

- Network Controller 6.x, 7.x, 8.x

│   ├── change_applier.py          (Safe change application)🏠 STICKY CLIENTS (Forward Analysis):

---

│   ├── optimize_network.py        (Optimization logic)  • Device laptop-conf... sees 1 AP, 0% roam rate → improve band steering

**Made with ❤️ for better WiFi**

│   └── diagnose_clients.py        (Client diagnostics)  • Device tablet-break... avg disconnect -88 dBm → coverage issue

│  

├── tests/                 ← Test suites📡 POOR RSSI DISCONNECTS (Backward Analysis):  

├── docs/                  ← Documentation  • Device phone-mary... worst: -91 dBm, 12 disconnects → AP relocation needed

├── ui/                    ← User interface components  • Device iot-sensor... avg: -85 dBm → increase minimum RSSI threshold

├── utils/                 ← Helper utilities

└── deleteme/              ← Old/unused files (safe to delete)📈 INTELLIGENT RECOMMENDATIONS:

```  • Set minimum RSSI to -72 dBm (high confidence, based on 156 events)

  • AP-Kitchen: 8 interference alarms → consider channel change  

---  • 15 authentication failures at 8 AM daily → check RADIUS timeout

```

## CloudKey Gen2+ Support

## Testing

✅ Full support for UniFi CloudKey Gen2 Plus  Run the comprehensive test suite from the root directory:

✅ Automatic CSRF token management  ```bash

✅ Proper API endpoint handling (`/proxy/network/`)  python3 RunUnitTests.py

✅ JWT-based authentication  ```



Compatible with:Or run the test suite from the tests directory:

- CloudKey Gen2/Gen2 Plus```bash

- UniFi Dream Machine (UDM)python3 tests/run_tests.py

- UniFi Dream Machine Pro (UDM-Pro)```

- Self-hosted UniFi Network Application

Or test individual components:

---```bash

python3 tests/test_utils.py      # URL normalization tests

## Safety Featurespython3 tests/test_auth.py       # Authentication tests (including 2FA)

python3 tests/test_integration.py # Integration tests with mock servers

### Read-Only Analysis```

Options 1 & 2 never modify your network - they only analyze and recommend.

For manual verification with your actual UniFi controller:

### Dry-Run Mode```bash

Option 3 simulates changes and shows detailed impact **without actually applying them**.python3 verify.py

```

### Interactive Approval

Option 4 prompts you to approve **each individual change** before applying it. You can skip any change you don't want.## Troubleshooting



### Impact Analysis### Authentication Issues

Before any change, you see:- **Login fails**: The toolkit automatically adds `https://` prefix, so just enter the IP address

- What will change- **2FA required**: You'll be prompted for your verification code when needed

- Why it's recommended- **API key auth**: Choose 'key' as auth method and paste your API key

- How many clients are affected

- Expected disconnection time (typically 5-10 seconds)### Connection Issues

- **No devices**: Ensure your **Site** name matches (often `default`)

---- **SSL warnings**: Self-signed certificate warnings are automatically suppressed

- **Network timeouts**: Check firewall rules allow HTTPS access to your UniFi controller

## Troubleshooting

### Data Issues

### "Failed to login"- **Empty reports**: Verify you have access to the specified site

- Check controller URL (include `https://`)- **Missing APs**: Ensure your user account has appropriate permissions

- Verify username/password

- Ensure firewall allows connection## File Structure

- Try port 8443 if default doesn't work```

unifi_easytoolkit_plus/

### "No devices found"├── DeepAnalyse.py          # Main entry point

- Verify you have access points in the controller├── Setup.py                # Dependency installer

- Check you're querying the correct site├── utils.py                # URL normalization and utilities

- Ensure user has admin/read access├── config.py               # Configuration management

├── network.py              # UniFi API client

### "Module not found"├── analyzer.py             # Network analysis logic

```bash├── reporter.py             # HTML report generation

pip install -r requirements.txt├── verify.py               # Manual verification script

```├── requirements.txt        # Python dependencies

├── data/                   # Configuration and state storage

### "Connection refused"├── reports/                # Generated HTML reports

- Controller must be reachable on network└── tests/                  # Test suite

- Check if CloudKey/UDM is online    ├── test_utils.py       # URL normalization tests

- Verify SSL certificate is valid (or use `--no-verify`)    ├── test_auth.py        # Authentication tests

    ├── test_integration.py # Integration tests

---    └── run_tests.py        # Test runner

```

## Advanced Usage

## Uninstall

### Command Line (Bypass Demo)Just delete the folder. No global changes are made beyond installing two Python libraries (`requests`, `rich`).

```bash

# Direct analysis with 7-day lookback## Changelog

python3 core/optimize_network.py analyze \

  --host https://192.168.1.119 \### Latest Version - v2.0 "Network Intelligence"

  --username admin \- 🚀 **Advanced Historical Analysis**: Complete forward/backward roaming pattern analysis

  --lookback 7- 📊 **RSSI Optimization**: Data-driven threshold recommendations with percentile analysis  

- 🔍 **Pattern Recognition**: Sticky clients, excessive roamers, and coverage gap detection

# Apply changes with dry-run- 📈 **Enterprise Intelligence**: Authentication failure tracking and interference correlation

python3 core/optimize_network.py apply --dry-run \- ✅ **2FA Support**: Full two-factor authentication support for secure UniFi accounts

  --host https://192.168.1.119 \- ✅ **Smart URL Handling**: Automatic `https://` prefix addition and `http://` to `https://` conversion

  --username admin- ✅ **Comprehensive Testing**: 15 tests covering core functionality and advanced analysis

```- ✅ **Enhanced Reports**: Historical analysis sections with actionable insights

- ✅ **Production Ready**: Verified with real-world scenarios and edge cases

### Custom Site

```bash### Previous Versions

python3 optimizer.py- ✅ **Enhanced Error Handling**: Better error messages and recovery

# When prompted, enter your site name (default is "default")- ✅ **State Management**: Improved configuration and state persistence

```

— Enjoy! 🛠️

### Extended Historical Analysis
Modify `optimizer.py` line with `--lookback 3` to `--lookback 7` for 7-day analysis.

---

## Performance

- **Analysis Mode**: 20-30 seconds (depends on network size)
- **Client Diagnostics**: 10-15 seconds
- **Dry-Run**: 15-20 seconds
- **Apply Changes**: 5-10 seconds per change (brief disconnection)

---

## Support

### Documentation
See `docs/` folder for detailed guides:
- `EXPERT_NETWORK_ANALYSIS.md` - Comprehensive analysis guide
- `CLIENT_HEALTH_INTEGRATION.md` - Client diagnostics details
- `QUICK_START_EXPERT.md` - Quick reference card

### Issues
1. Check `docs/` for relevant guides
2. Run `python3 run_all_tests.py` to verify setup
3. Review error messages carefully

---

## Credits

Built with ❤️ for UniFi network administrators who want:
- Professional-grade analysis
- Data-driven decision making
- Safe, controlled network changes
- Reliable mesh AP deployments

**Optimized for real-world deployments** including acreage, multi-building, and mesh networks where **reliability matters more than speed**.

---

## License

MIT License - See LICENSE file for details

---

## Version

**v2.0** - Expert Network Analysis Release

**What's New:**
- 🆕 Historical lookback analysis (3-day default)
- 🆕 Mesh AP special handling (reliability-focused)
- 🆕 Expert RSSI thresholds and best practices
- 🆕 Priority-based recommendations (CRITICAL/HIGH/MEDIUM/LOW)
- 🆕 Client health scoring with A-F grades
- 🆕 DFS channel detection and avoidance
- 🆕 Power optimization for roaming
- ✨ Enhanced demo interface
- ✅ Full CloudKey Gen2+ support
- ✅ Comprehensive test coverage

---

**Ready to optimize your network? Run `python3 optimizer.py` now!** 🚀
