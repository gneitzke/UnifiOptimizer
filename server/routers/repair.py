"""Repair router — preview, apply, and revert network changes."""

import os
import sys

from fastapi import APIRouter, Header, HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from server.models.schemas import (
    ChangeHistoryEntry,
    ChangePreview,
    ChangeResult,
    RepairApplyRequest,
    RepairPreviewRequest,
    RevertRequest,
)
from server.services.change_tracker import change_tracker
from server.services.session_manager import session_pool

router = APIRouter()


def _get_token(authorization: str = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    return authorization[7:]


def _get_cached_results(job_id: str) -> dict:
    """Retrieve cached analysis results for a job."""
    from server.routers.analysis import _results, _lock

    with _lock:
        result = _results.get(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Analysis results not found — run analysis first")
    return result


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


@router.post("/preview")
async def preview_changes(
    req: RepairPreviewRequest,
    job_id: str,
    authorization: str = Header(None),
):
    """Preview the impact of selected recommendations before applying."""
    token = _get_token(authorization)
    entry = session_pool.get_session(token)
    if entry is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    result = _get_cached_results(job_id)
    recs = result.get("recommendations", [])

    previews = []
    for idx in req.recommendation_ids:
        if idx < 0 or idx >= len(recs):
            continue
        rec = recs[idx]
        device_name = rec.get("device", {}).get("name", "Unknown")
        action = rec.get("action", "unknown")

        current_value, new_value = _format_change(rec)

        previews.append(
            ChangePreview(
                index=idx,
                device_name=device_name,
                action=action,
                current_value=current_value,
                new_value=new_value,
                impact={
                    "affected_clients": rec.get("affected_clients", 0),
                    "estimated_downtime": _estimate_downtime(action),
                    "risk_level": "low" if action in ("band_steering",) else "medium",
                    "reason": rec.get("reason", ""),
                },
            )
        )

    return {"previews": previews}


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


@router.post("/apply")
async def apply_changes(
    req: RepairApplyRequest,
    job_id: str,
    authorization: str = Header(None),
):
    """Apply selected recommendations to the controller."""
    token = _get_token(authorization)
    entry = session_pool.get_session(token)
    if entry is None:
        raise HTTPException(status_code=401, detail="Session expired")

    result = _get_cached_results(job_id)
    all_recs = result.get("recommendations", [])

    selected = []
    for idx in req.recommendation_ids:
        if 0 <= idx < len(all_recs):
            selected.append(all_recs[idx])

    if not selected:
        raise HTTPException(status_code=400, detail="No valid recommendations selected")

    from core.services.apply_service import apply_recommendations

    apply_result = apply_recommendations(
        entry.client,
        selected,
        dry_run=req.dry_run,
        interactive=False,
        analysis_data=result.get("full_analysis"),
    )

    # Track each applied change for revert
    change_results = []
    for item in apply_result["applied"]:
        rec = item["rec"]
        device = rec.get("device", {})

        # Snapshot before config (radio_table)
        before_config = {"radio_table": device.get("radio_table", [])}
        after_config = {"action": rec.get("action"), "details": rec}

        change_id = change_tracker.record_change(
            device_name=device.get("name", "Unknown"),
            device_mac=device.get("mac", ""),
            action=rec.get("action", "unknown"),
            before_config=before_config,
            after_config=after_config,
            status="dry_run" if req.dry_run else "applied",
        )

        from datetime import datetime

        change_results.append(
            ChangeResult(
                change_id=change_id,
                device_name=device.get("name", "Unknown"),
                action=rec.get("action", "unknown"),
                status="dry_run" if req.dry_run else "applied",
                before_config=before_config,
                after_config=after_config,
                timestamp=datetime.utcnow().isoformat() + "Z",
            )
        )

    return {
        "results": change_results,
        "summary": {
            "applied": len(apply_result["applied"]),
            "failed": len(apply_result["failed"]),
            "skipped": len(apply_result["skipped"]),
            "dry_run": req.dry_run,
        },
    }


# ---------------------------------------------------------------------------
# Revert
# ---------------------------------------------------------------------------


@router.post("/revert")
async def revert_change(req: RevertRequest, authorization: str = Header(None)):
    """Revert a previously applied change using the stored before_config."""
    token = _get_token(authorization)
    entry = session_pool.get_session(token)
    if entry is None:
        raise HTTPException(status_code=401, detail="Session expired")

    change = change_tracker.get_change(req.change_id)
    if change is None:
        raise HTTPException(status_code=404, detail="Change not found")
    if change["reverted"]:
        raise HTTPException(status_code=409, detail="Change already reverted")
    if change["status"] == "dry_run":
        raise HTTPException(status_code=400, detail="Cannot revert a dry-run change")

    # Revert by PUTting the before_config back to the controller
    before = change.get("before_config", {})
    device_mac = change.get("device_mac")

    if not device_mac or not before.get("radio_table"):
        raise HTTPException(status_code=500, detail="Insufficient revert data")

    try:
        # Re-fetch current device state
        devices_response = entry.client.get(f"s/{entry.site}/stat/device")
        device = None
        devices_data = (
            devices_response.get("data", [])
            if isinstance(devices_response, dict)
            else devices_response
            if isinstance(devices_response, list)
            else []
        )
        device = next((d for d in devices_data if d.get("mac") == device_mac), None)

        if device is None:
            raise HTTPException(status_code=404, detail="Device not found on controller")

        # Restore radio_table
        device_id = device.get("_id")
        payload = {"radio_table": before["radio_table"]}
        entry.client.put(f"s/{entry.site}/rest/device/{device_id}", payload)

        change_tracker.mark_reverted(req.change_id)
        return {"reverted": True, "change_id": req.change_id}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Revert failed: {str(e)}")


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


@router.get("/history")
async def change_history(limit: int = 100, authorization: str = Header(None)):
    """List recent change history."""
    _get_token(authorization)
    history = change_tracker.get_history(limit=limit)
    return {"changes": history}


@router.get("/revertable")
async def revertable_changes(authorization: str = Header(None)):
    """List changes that can still be reverted."""
    _get_token(authorization)
    return {"changes": change_tracker.get_revertable()}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_change(rec: dict):
    action = rec.get("action", "")
    if action == "channel_change":
        return str(rec.get("current_channel", "?")), str(rec.get("new_channel", "?"))
    if action == "power_change":
        return rec.get("current_power", "?"), rec.get("new_power", "?")
    if action == "band_steering":
        return rec.get("current_mode", "off"), rec.get("new_mode", "prefer_5g")
    if action == "min_rssi":
        cur = f"{'on' if rec.get('current_enabled') else 'off'}"
        if rec.get("current_value"):
            cur += f" ({rec['current_value']} dBm)"
        new = f"{'on' if rec.get('new_enabled') else 'off'}"
        if rec.get("new_value"):
            new += f" ({rec['new_value']} dBm)"
        return cur, new
    return "?", "?"


def _estimate_downtime(action: str) -> str:
    estimates = {
        "channel_change": "5-10 seconds",
        "power_change": "2-5 seconds",
        "band_steering": "No downtime",
        "min_rssi": "5-10 seconds (weak clients disconnect)",
    }
    return estimates.get(action, "Unknown")
