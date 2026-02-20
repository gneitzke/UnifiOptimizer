# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

UnifiOptimizer is a Python 3.7+ network analysis and optimization toolkit for Ubiquiti UniFi controllers. It operates in two modes:
- **analyze**: Read-only analysis that generates an HTML report
- **optimize**: Apply recommended configuration changes (with dry-run and interactive approval)

## Commands

### Running
```bash
python3 optimizer.py analyze --host https://CONTROLLER_IP --username admin
python3 optimizer.py optimize --host https://CONTROLLER_IP --username admin --dry-run
python3 optimizer.py analyze --profile default   # use saved credentials
python3 regenerate_report.py                     # rebuild report from cached analysis
```

### Linting & Formatting
```bash
./format_code.sh    # auto-format with Black + isort
./check_code.sh     # verify formatting and run Flake8
```
Line length is 100. Black and isort are configured in `pyproject.toml`. Flake8 config is in `.flake8`.

### Documentation
Always update `README.md` and `WHATS_NEW.md` when making any code changes. Include docs updates in the same commit or a follow-up docs commit immediately after.

### Testing
```bash
python3 run_all_tests.py <host> <username> <password> [site_name]
```
Tests require a live UniFi controller. 24 test files live in `tests/`.

## Architecture

### Entry Point Flow
```
optimizer.py  (CLI entry point; routes to core/optimize_network.py)
  └── core/optimize_network.py  (main orchestrator, ~2500 lines)
        ├── analyze_network()
        │     ├── network_analyzer.py      → ExpertNetworkAnalyzer
        │     ├── advanced_analyzer.py     → AdvancedNetworkAnalyzer
        │     └── report_v2.py             → generate_v2_report()
        └── optimize_network()
              └── change_applier.py        → ChangeApplier
```

### Module Responsibilities

| Directory/File | Purpose |
|---|---|
| `api/cloudkey_gen2_client.py` | HTTP client for the UniFi controller API; manages sessions, CSRF tokens |
| `api/csrf_token_manager.py` | Extracts and applies CSRF tokens on every request |
| `core/optimize_network.py` | CLI entry, overall flow control, orchestrates all analysis passes |
| `core/network_analyzer.py` | First analysis pass: AP channels/power, client RSSI, mesh detection |
| `core/advanced_analyzer.py` | Second pass: DFS events, band steering, fast roaming, airtime, WiFi 6/6E/7 |
| `core/change_applier.py` | Impact analysis and safe application of changes; writes audit JSON |
| `core/switch_analyzer.py` | Switch port analysis: error rates, utilization, optimization recommendations |
| `core/client_health.py` | Per-client composite health scoring (40% signal / 25% stability / 20% roaming / 15% throughput) |
| `core/report_v2.py` | HTML report generation with charts and dashboards |
| `utils/config.py` | YAML config loader with dot-path access and deep-merge defaults |
| `utils/keychain.py` | macOS Keychain credential storage for saved profiles |
| `utils/network_helpers.py` | RSSI normalization, mesh role detection, AP display names |
| `data/config.yaml` | User-customizable thresholds (RSSI, min RSSI, mesh, channel width) |
| `data/wifi_device_capabilities.json` | Device database for WiFi 6/6E/7 capability detection |

### Key Patterns

**Configuration access** — always use `utils/config.py`, not direct file reads:
```python
from utils.config import get_threshold, get_option
rssi_excellent = get_threshold("rssi.excellent", -50)
lookback = get_option("lookback_days", 3)
```

**CSRF token flow** — login → extract token → attach to all subsequent PUT/POST requests. Handled automatically by `CSRFTokenManager` attached to the `requests.Session` in `cloudkey_gen2_client.py`.

**Mesh detection** — `is_mesh_child()` checks `uplink.type == "wireless"`. Power reduction changes skip mesh APs to preserve uplink reliability.

**Change audit trail** — every applied change is appended to timestamped JSON files named like `changes_YYYYMMDD_HHMMSS.json` in the working directory (for example, `changes_20250101_120000.json`).

**Analysis cache** — full analysis results are saved to `analysis_cache_YYYYMMDD_HHMMSS.json` so `regenerate_report.py` can rebuild reports without re-querying the controller.

**SSL** — verification is disabled by default; the controller uses self-signed certificates.
