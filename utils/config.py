"""
Configuration loader for UniFi Network Optimizer

Loads settings from data/config.yaml with fallback defaults.
All thresholds and options can be customized without code changes.
"""

import copy
from pathlib import Path

# Default configuration (used when config.yaml is missing or incomplete)
DEFAULTS = {
    "thresholds": {
        "rssi": {
            "excellent": -50,
            "good": -60,
            "fair": -70,
            "poor": -80,
        },
        "min_rssi": {
            "optimal": {"2.4ghz": -75, "5ghz": -72, "6ghz": -70},
            "max_connectivity": {"2.4ghz": -80, "5ghz": -77, "6ghz": -75},
        },
        "mesh": {
            "uplink_critical_dbm": -80,
            "uplink_acceptable_dbm": -75,
            "disconnect_threshold": 5,
        },
        "channel_width": {
            "prefer_40mhz_when_aps_gt": 4,
            "narrow_when_clients_lt": 5,
        },
        "power": {
            "skip_reduction_for_mesh": True,
            "skip_reduction_for_mesh_parents": True,
        },
    },
    "options": {
        "lookback_days": 3,
        "min_rssi_strategy": "optimal",
        "generate_html_report": True,
        "generate_share_report": True,
    },
}


def _deep_merge(base, override):
    """Recursively merge override into base, returning a new dict"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_path=None):
    """
    Load configuration from YAML file with fallback to defaults.

    Args:
        config_path: Path to config.yaml. If None, searches standard locations.

    Returns:
        dict: Merged configuration
    """
    if config_path is None:
        # Search standard locations
        candidates = [
            Path(__file__).parent.parent / "data" / "config.yaml",
            Path("data/config.yaml"),
            Path("config.yaml"),
        ]
        for candidate in candidates:
            if candidate.exists():
                config_path = candidate
                break

    if config_path and Path(config_path).exists():
        try:
            import yaml

            with open(config_path, "r") as f:
                user_config = yaml.safe_load(f) or {}
            return _deep_merge(DEFAULTS, user_config)
        except ImportError:
            # PyYAML not installed — try simple parsing or use defaults
            pass
        except Exception:
            pass

    return copy.deepcopy(DEFAULTS)


# Singleton instance loaded once
_config = None


def get_config():
    """Get the loaded configuration (cached singleton)"""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def get_threshold(path, default=None):
    """
    Get a threshold value by dot-separated path.

    Examples:
        get_threshold("rssi.excellent")  -> -50
        get_threshold("min_rssi.optimal.5ghz")  -> -72
        get_threshold("mesh.uplink_critical_dbm")  -> -80
    """
    config = get_config()
    parts = path.split(".")

    # Search in thresholds section
    current = config.get("thresholds", {})
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def get_option(key, default=None):
    """Get an option value by key name"""
    config = get_config()
    return config.get("options", {}).get(key, default)
