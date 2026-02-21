"""Apply service — headless change application with revert tracking.

Orchestrates applying recommendations through the ChangeApplier without
any console output. Returns structured results.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.change_applier import ChangeApplier


def apply_recommendations(
    client,
    recommendations,
    dry_run=False,
    interactive=False,
    analysis_data=None,
):
    """Apply a list of recommendations and return structured results.

    Args:
        client: Authenticated CloudKeyGen2Client instance.
        recommendations: List of recommendation dicts (from recommendation_service).
        dry_run: If True, simulate changes without applying.
        interactive: If True, prompt user before each change (CLI-only).
        analysis_data: Full analysis dict (used for iOS device count, etc.).

    Returns:
        dict with keys:
            - applied: list of successfully applied change dicts.
            - failed: list of failed change dicts with error info.
            - skipped: list of skipped change dicts.
            - devices_restarted: list of device IDs that were restarted.
            - change_log: raw change log from ChangeApplier.
    """
    ios_device_count = 0
    if analysis_data:
        min_rssi_analysis = analysis_data.get("min_rssi_analysis", {})
        ios_device_count = min_rssi_analysis.get("ios_device_count", 0)

    applier = ChangeApplier(client, dry_run=dry_run, interactive=interactive)

    devices_to_restart = set()
    devices_by_id = {}
    applied = []
    failed = []
    skipped = []

    for rec in recommendations:
        action = rec["action"]
        device = rec["device"]
        device_id = device.get("_id")
        devices_by_id[device_id] = device

        success = False

        if action == "channel_change":
            success = applier.apply_channel_change(device, rec["radio"], rec["new_channel"])
        elif action == "power_change":
            success = applier.apply_power_change(device, rec["radio"], rec["new_power"])
        elif action == "band_steering":
            success = applier.apply_band_steering(device, rec["new_mode"])
        elif action == "min_rssi":
            radio_name = rec.get("radio")
            if radio_name:
                success = applier.apply_min_rssi(
                    device, radio_name, rec["new_enabled"], rec["new_value"]
                )
            else:
                values = rec.get("values")
                strategy = rec.get("strategy")
                success = applier.apply_min_rssi_all_bands(
                    device, rec["new_enabled"], values, strategy, ios_device_count
                )

        entry = {"device_name": device.get("name", "Unknown"), "action": action, "rec": rec}

        if success:
            applied.append(entry)
            if device_id:
                devices_to_restart.add(device_id)
        else:
            # ChangeApplier returns False for both failures and interactive skips
            failed.append(entry)

    # Restart APs that had changes applied
    devices_restarted = []
    if devices_to_restart and not dry_run:
        for device_id in devices_to_restart:
            device = devices_by_id[device_id]
            applier.restart_ap(device, soft_restart=True)
            devices_restarted.append(device_id)

    return {
        "applied": applied,
        "failed": failed,
        "skipped": skipped,
        "devices_restarted": devices_restarted,
        "dry_run": dry_run,
    }
