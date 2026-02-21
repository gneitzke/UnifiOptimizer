"""Recommendation service — convert raw analysis recommendations to applier format.

Headless (no console output) conversion of expert analyzer recommendations
into the format expected by ChangeApplier.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from utils.network_helpers import is_mesh_parent


def convert_recommendations(expert_recs, all_devices=None):
    """Convert expert analyzer recommendations to change-applier format.

    This is the headless equivalent of optimize_network._convert_expert_recommendations().

    Args:
        expert_recs: Recommendations list from the analysis pipeline.
        all_devices: Full device list from the controller API.

    Returns:
        list[dict]: Recommendations formatted for ChangeApplier, with keys like
            device, action, radio, reason, priority, affected_clients, etc.
    """
    converted = []
    skipped = []

    device_by_mac = {}
    if all_devices:
        device_by_mac = {d.get("mac"): d for d in all_devices}

    for rec in expert_recs:
        ap_info = rec.get("ap")
        if not ap_info:
            continue

        ap_mac = ap_info["mac"]
        device = device_by_mac.get(ap_mac)

        if not device:
            device = {"mac": ap_info["mac"], "name": ap_info["name"], "type": "uap"}

        rec_type = rec.get("type", "")
        issue = rec.get("issue", "")
        priority = rec.get("priority", "low")

        if "power" in issue or "power" in rec_type:
            result = _convert_power_rec(rec, device, ap_info, ap_mac, all_devices, priority)
            if result is None:
                skipped.append(
                    {"device": ap_info.get("name", "Unknown"), "reason": "mesh AP"}
                )
                continue
            converted.append(result)

        elif "channel" in issue or "channel" in rec_type:
            converted.append(_convert_channel_rec(rec, device, priority))

        elif "band_steering" in issue or "band_steering" in rec_type or rec_type == "band_steering":
            converted.append(_convert_band_steering_rec(rec, device, priority))

        elif "min_rssi" in issue or "min_rssi" in rec_type or rec_type == "min_rssi_disabled":
            result = _convert_min_rssi_rec(rec, device, rec_type, priority)
            if result is not None:
                converted.append(result)

    return converted, skipped


# ---------------------------------------------------------------------------
# Internal converters
# ---------------------------------------------------------------------------


def _convert_power_rec(rec, device, ap_info, ap_mac, all_devices, priority):
    """Return a power-change dict or None if the AP is mesh-protected."""
    is_mesh_child = ap_info.get("is_mesh", False) or ap_info.get("is_mesh_child", False)
    is_parent = ap_info.get("is_mesh_parent", False)

    if device.get("uplink", {}).get("type") == "wireless":
        is_mesh_child = True

    if all_devices:
        aps = [d for d in all_devices if d.get("type") == "uap"]
        for other_ap in aps:
            other_uplink = other_ap.get("uplink", {})
            if (
                other_uplink.get("type") == "wireless"
                and other_uplink.get("uplink_remote_mac") == ap_mac
            ):
                is_parent = True
                break

    if is_mesh_child or is_parent:
        return None

    radio = rec.get("radio", {})
    current_power = radio.get("tx_power_mode", "high")
    tx_power_dbm = radio.get("tx_power")
    new_power = "medium" if current_power == "high" else "low"

    return {
        "device": device,
        "action": "power_change",
        "radio": radio.get("radio", "ng"),
        "current_power": current_power,
        "new_power": new_power,
        "tx_power_dbm": tx_power_dbm,
        "reason": rec.get("message", "") + ". " + rec.get("recommendation", ""),
        "priority": priority,
        "affected_clients": rec.get("affected_clients", 0),
    }


def _convert_channel_rec(rec, device, priority):
    radio = rec.get("radio", {})
    band = rec.get("band", "")
    current_channel = radio.get("channel", 1)
    try:
        current_channel = int(current_channel)
    except (ValueError, TypeError):
        current_channel = 1

    new_channel = rec.get("new_channel")
    if new_channel is None:
        new_channel = _suggest_channel(band, current_channel)

    return {
        "device": device,
        "action": "channel_change",
        "radio": radio.get("radio", "ng"),
        "current_channel": current_channel,
        "new_channel": new_channel,
        "reason": rec.get("message", "") + ". " + rec.get("recommendation", ""),
        "priority": priority,
        "affected_clients": rec.get("affected_clients", 0),
    }


def _suggest_channel(band, current_channel):
    """Suggest a new channel when the analyzer didn't provide one."""
    if band == "2.4GHz":
        return {1: 6, 6: 11}.get(current_channel, 1)
    if band == "6GHz":
        if current_channel < 37 or current_channel > 213:
            return 37
        mapping = {37: 69, 53: 101, 69: 101, 85: 133, 101: 133}
        return mapping.get(current_channel, 37)
    # 5GHz
    if 52 <= current_channel <= 144:
        return 36
    return 149


def _convert_band_steering_rec(rec, device, priority):
    current_mode = device.get("bandsteering_mode") or device.get("band_steering_mode", "off")
    return {
        "device": device,
        "action": "band_steering",
        "current_mode": current_mode,
        "new_mode": "prefer_5g",
        "reason": rec.get("message", "") + ". " + rec.get("recommendation", ""),
        "priority": priority,
        "affected_clients": rec.get("affected_clients", 0),
    }


def _convert_min_rssi_rec(rec, device, rec_type, priority):
    # Skip mesh AP min-RSSI warnings (those are informational, not actionable)
    if rec.get("is_mesh") or rec_type == "mesh_min_rssi_danger":
        return None

    radio_name = rec.get("radio", "ng")
    band = rec.get("band", "2.4GHz")
    current_enabled = False
    current_value = None

    for radio in device.get("radio_table", []):
        if radio.get("radio") == radio_name:
            current_enabled = radio.get("min_rssi_enabled", False)
            current_value = radio.get("min_rssi", None)
            break

    recommended_value = rec.get("recommended_value", -75 if radio_name == "ng" else -72)

    return {
        "device": device,
        "action": "min_rssi",
        "radio": radio_name,
        "band": band,
        "current_enabled": current_enabled,
        "current_value": current_value,
        "new_enabled": True,
        "new_value": recommended_value,
        "reason": rec.get("message", "") + ". " + rec.get("recommendation", ""),
        "priority": priority,
    }
