"""The fix planner (``docs/ARCHITECTURE.md`` section 9).

Pure, I/O-free mapping from a detector finding to a :class:`FixPlan`. Given a
:class:`~netadmin.domain.entities.Finding` and (for config changes) a read-only
snapshot of the target device -- the raw controller device object collected on the
tech-visit/GET path -- it renders concrete, revertible steps whose payloads
preserve every existing field of the object being changed (the UniFi
``rest/device`` PUT replaces the whole ``radio_table``, so a partial body would
wipe untouched radios; the old ``core/change_applier.py`` learned this the hard
way and we keep the discipline).

It plans only the safe, high-value templates:

* ``wifi.channel_plan`` -> a 2.4 GHz channel move onto the 1/6/11 grid. Per radio
  for a per-radio defect (off-grid); **jointly, one step per radio moved**, for
  the site-scoped per-band co-channel issue, where planning each radio on its own
  is what used to move both ends of a conflict onto the same new channel.
* ``wifi.tx_power_loud``  -> one power step down (high->medium->low, auto->medium).
* ``wifi.min_rssi_misconfig`` -> **removal only** of min-RSSI (never a set, ever).
* ``wired.port_flapping`` (PoE reboot-loop / fault) -> a single PoE port power-cycle.

Everything whose real fix is physical -- ``wired.bad_cable``, ``wifi.mesh_uplink``
RSSI, ``net.coverage_hole`` -- and any detector without a safe template returns an
*advisory* plan: ``steps == []``, ``manual_action_required``, and a note saying
what a human must do on site. The planner never invents a network mutation for a
problem a cable or an antenna has to solve.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping, Optional

from netadmin.domain.entities import Finding
from netadmin.domain.types import EntityType
from netadmin.fixes.models import ActionType, FixPlan, FixStep, Precondition, RiskLevel

__all__ = [
    "plan_fix",
    "PHYSICAL_REFUSAL_KEYS",
    "TX_POWER_ORDER",
    "MAX_JOINT_CHANNEL_MOVES",
]

# Detectors whose remediation is physical: an advisory plan, never a mutation.
PHYSICAL_REFUSAL_KEYS = frozenset(
    {
        "wired.bad_cable",
        "wifi.mesh_uplink",
        "net.coverage_hole",
    }
)

# Non-overlapping 2.4 GHz channels; the only band we auto-plan a channel move for
# (5/6 GHz channel choice needs DFS/RF planning we do not attempt automatically).
_VALID_24_CHANNELS = (1, 6, 11)

# Ceiling on the radios one joint 2.4 GHz re-plan may move. Held at or below the
# applier's DEFAULT_MAX_DEVICES (3) so a plan this module renders can always
# actually apply -- rendering a plan the max-N guard would later refuse is a trap,
# not a fix. A band needing more moves than this gets the highest-value ones now;
# the issue stays open, and the next pass plans the remainder against fresh state.
MAX_JOINT_CHANNEL_MOVES = 3

# tx-power modes ordered quietest -> loudest; a step-down moves one left.
TX_POWER_ORDER = ("low", "medium", "high")


# ---------------------------------------------------------------------------- #
# Public entry point
# ---------------------------------------------------------------------------- #
def plan_fix(
    finding: Finding,
    *,
    device: Optional[Mapping[str, Any]] = None,
    devices: Optional[Mapping[str, Mapping[str, Any]]] = None,
    issue_id: Optional[int] = None,
) -> FixPlan:
    """Map a finding to a :class:`FixPlan`.

    ``device`` is the raw controller device object (with ``_id``, ``mac``,
    ``radio_table``) captured read-only; required to render the radio/PoE payloads.
    ``devices`` is the same thing keyed by lower-cased MAC, for a site-scoped
    finding whose fix touches several devices at once (the per-band channel
    re-plan); every other template reads ``device`` alone.
    When a template needs a snapshot it does not have, or the evidence describes a
    sub-case with no safe automatic fix, the planner returns an advisory plan rather
    than guessing.
    """
    key = finding.detector_key
    native = finding.entity.native_id

    if key in PHYSICAL_REFUSAL_KEYS:
        return _advisory(
            finding,
            issue_id,
            _physical_note(key, native),
        )

    if key == "wifi.min_rssi_misconfig":
        return _plan_min_rssi_remove(finding, device, issue_id)
    if key == "wifi.channel_plan":
        return _plan_channel_change(finding, device, devices, issue_id)
    if key == "wifi.tx_power_loud":
        return _plan_tx_power_step_down(finding, device, issue_id)
    if key == "wired.port_flapping":
        return _plan_poe_power_cycle(finding, device, issue_id)

    return _advisory(
        finding,
        issue_id,
        f"No safe automatic fix for '{key}'. Review the issue evidence and remediate manually.",
    )


# ---------------------------------------------------------------------------- #
# Templates
# ---------------------------------------------------------------------------- #
def _plan_min_rssi_remove(
    finding: Finding, device: Optional[Mapping[str, Any]], issue_id: Optional[int]
) -> FixPlan:
    """Remove (disable) min-RSSI on the offending radio. Removal only -- never a set.

    Safe on every case the detector fires for (mesh-uplink AP, single-AP site,
    over-strict floor): disabling only ever *stops* clients being kicked, so it can
    never worsen coverage. Setting min-RSSI as a remediation is categorically
    refused elsewhere; this template exists solely to turn it off.
    """
    band_code = _band_code(finding)
    dev_id, radio_table, radio = _locate_radio(device, band_code)
    if dev_id is None or radio is None:
        return _advisory(
            finding, issue_id, "Device radio snapshot unavailable; disable min-RSSI manually."
        )

    if not _truthy(radio.get("min_rssi_enabled")):
        return _advisory(
            finding, issue_id, "min-RSSI already disabled on this radio; no change needed."
        )

    new_table = copy.deepcopy(list(radio_table))
    for entry in new_table:
        if entry.get("radio") == band_code:
            entry["min_rssi_enabled"] = False

    endpoint = f"rest/device/{dev_id}"
    payload = {"radio_table": new_table}
    label = finding.entity.name or finding.entity.native_id
    step = FixStep(
        action=ActionType.MIN_RSSI_REMOVE,
        target_entity_type=EntityType.RADIO,
        target_native_id=finding.entity.native_id,
        description=f"Disable min-RSSI on {label} (removal only).",
        risk=RiskLevel.LOW,
        method="PUT",
        endpoint=endpoint,
        payload=payload,
        precondition=Precondition(
            target_native_id=finding.entity.native_id,
            expected={"min_rssi_enabled": True},
            description="min-RSSI must still be enabled on this radio.",
        ),
        before={"method": "PUT", "endpoint": endpoint, "body": {"radio_table": list(radio_table)}},
        after={"method": "PUT", "endpoint": endpoint, "body": payload},
        revertible=True,
    )
    return FixPlan(
        detector_key=finding.detector_key,
        entity_native_id=finding.entity.native_id,
        title=f"Remove min-RSSI on {label}",
        steps=[step],
        issue_id=issue_id,
    )


def _plan_channel_change(
    finding: Finding,
    device: Optional[Mapping[str, Any]],
    devices: Optional[Mapping[str, Mapping[str, Any]]],
    issue_id: Optional[int],
) -> FixPlan:
    """Route a channel-plan finding by scope: one radio, or a whole band.

    A RADIO-scoped finding is a per-radio defect and keeps the single-step path.
    Anything else is the site-scoped per-band issue on the ``rf:<band>``
    pseudo-entity, whose fix is a *joint* re-plan across several radios.
    """
    if finding.entity.entity_type == EntityType.RADIO:
        return _plan_single_radio_channel(finding, device, issue_id)
    return _plan_band_channel_spread(finding, devices, issue_id)


def _plan_single_radio_channel(
    finding: Finding, device: Optional[Mapping[str, Any]], issue_id: Optional[int]
) -> FixPlan:
    """Move one 2.4 GHz radio onto the 1/6/11 grid.

    Only the deterministic 2.4 GHz cases are auto-planned. Width changes
    (``wide_channel_*``) and any 5/6 GHz case are advisory: choosing a 5 GHz channel
    safely means reasoning about DFS and neighbor occupancy the store does not hold.
    ``co_channel_reuse`` is accepted here only in its legacy per-radio shape (issues
    written before the band-scoped split; migration 0006 retired those rows).
    """
    band = str(finding.evidence.get("band") or "")
    subtype = str(finding.evidence.get("subtype") or finding.dims.get("subtype") or "")
    if band != "2.4" or subtype not in ("channel_off_grid", "co_channel_reuse"):
        return _advisory(
            finding,
            issue_id,
            f"Channel-plan sub-case '{subtype or '?'}' on {band or '?'} GHz needs manual "
            "RF planning (width/DFS/neighbor occupancy); not auto-planned.",
        )

    band_code = _band_code(finding)
    dev_id, radio_table, radio = _locate_radio(device, band_code)
    if dev_id is None or radio is None:
        return _advisory(
            finding, issue_id, "Device radio snapshot unavailable; set the channel manually."
        )

    current = _as_int(radio.get("channel"))
    if current is None:
        current = _as_int(finding.evidence.get("channel"))
    target = _recommend_24_channel(current, subtype)
    if target is None or target == current:
        return _advisory(
            finding, issue_id, "No better 2.4 GHz channel available to recommend automatically."
        )

    label = finding.entity.name or finding.entity.native_id
    step = _channel_step(
        native_id=finding.entity.native_id,
        device=device,
        current=current,
        target=target,
        description=f"Change {label} 2.4 GHz channel {current} -> {target} ({subtype}).",
    )
    if step is None:  # pragma: no cover - _locate_radio already proved it renders
        return _advisory(
            finding, issue_id, "Device radio snapshot unavailable; set the channel manually."
        )
    return FixPlan(
        detector_key=finding.detector_key,
        entity_native_id=finding.entity.native_id,
        title=f"Retune {label} to 2.4 GHz channel {target}",
        steps=[step],
        issue_id=issue_id,
    )


def _plan_band_channel_spread(
    finding: Finding,
    devices: Optional[Mapping[str, Mapping[str, Any]]],
    issue_id: Optional[int],
) -> FixPlan:
    """Re-plan a 2.4 GHz band jointly: one CHANNEL_CHANGE step per radio moved.

    The channels are assigned *together*, from the evidence's per-candidate load
    vector, so no two radios are told to make the same move -- the failure mode of
    planning each end of a conflict on its own. Only 2.4 GHz is planned; a 5 GHz
    band conflict and both width sub-cases stay advisory, for the same DFS/RF
    planning reason the single-radio path refuses them.

    Every step is an ordinary, individually revertible ``rest/device`` PUT that
    preserves the rest of its device's ``radio_table``; the plan carries no new
    gate and no new capability. A radio whose live channel no longer matches the
    evidence is dropped from the plan rather than moved on a stale assumption --
    a pinned radio by its configured channel, an auto radio by the operating
    channel its device's stats report.
    """
    band = str(finding.evidence.get("band") or "")
    subtype = str(finding.evidence.get("subtype") or finding.dims.get("subtype") or "")
    if subtype != "co_channel_reuse" or band != "2.4":
        return _advisory(
            finding,
            issue_id,
            f"Band-level channel-plan sub-case '{subtype or '?'}' on {band or '?'} GHz is a "
            "width/DFS judgement across the whole band; re-plan it manually.",
        )

    candidates = _candidate_channels(finding.evidence)
    loads = _channel_loads(finding.evidence, candidates)
    conflicted = _conflicted_radios(finding.evidence)

    # The load vector comes from the detector's evidence, which covers the WHOLE
    # band; the live device reads we are handed only cover the conflicted radios,
    # so the vector cannot simply be recomputed here. That makes drift dangerous
    # rather than merely stale: if the conflicted radios have moved since
    # detection, solving against the old vector can pick a destination that is now
    # occupied and make the spread *worse* than leaving it alone. Refuse instead.
    # The next daily detection pass re-fires with a fresh vector and the plan is
    # then correct, which is the same "re-plan rather than guess" posture the
    # per-step precondition already takes.
    drifted = _drifted_radios(conflicted, devices or {}, band)
    if drifted:
        return _advisory(
            finding,
            issue_id,
            "The 2.4 GHz band has changed since this was detected ("
            + ", ".join(drifted)
            + " moved), so a joint plan built on the old layout could make the "
            "spread worse. It will re-plan on the next detection pass.",
        )

    moves = _solve_channel_spread(loads, conflicted, candidates)
    if not moves:
        return _advisory(
            finding,
            issue_id,
            "No 2.4 GHz move improves the current spread; re-plan the band manually.",
        )

    devices = devices or {}
    steps: list[FixStep] = []
    for native_id, current, target in moves:
        device = devices.get(_device_mac(native_id))
        step = _channel_step(
            native_id=native_id,
            device=device,
            current=current,
            target=target,
            description=f"Move {native_id} from 2.4 GHz channel {current} to {target}.",
            expect_channel=current,
        )
        if step is not None:
            steps.append(step)
    if not steps:
        return _advisory(
            finding,
            issue_id,
            "Device radio snapshots unavailable or already changed; re-plan the 2.4 GHz "
            "band manually.",
        )

    steps.sort(key=lambda s: s.target_native_id)
    return FixPlan(
        detector_key=finding.detector_key,
        entity_native_id=finding.entity.native_id,
        title=f"Spread {_count(len(steps), 'radio')} across the 2.4 GHz 1/6/11 grid",
        steps=steps,
        issue_id=issue_id,
    )


def _channel_step(
    *,
    native_id: str,
    device: Optional[Mapping[str, Any]],
    current: Optional[int],
    target: int,
    description: str,
    expect_channel: Optional[int] = None,
) -> Optional[FixStep]:
    """One radio's channel-change step, or None when it cannot be rendered safely.

    Copies the device's whole ``radio_table`` and rewrites only this radio's
    channel, so the ``rest/device`` PUT cannot wipe the radios it does not touch.
    ``expect_channel``, when given, is the channel the plan assumed: a live radio
    that has since moved elsewhere returns None rather than a step built on a
    stale assumption.
    """
    band_code = native_id.rsplit(":", 1)[-1] if ":" in native_id else None
    dev_id, radio_table, radio = _locate_radio(device, band_code)
    if dev_id is None or radio is None:
        return None
    if radio.get("channel") is None:
        # No configured channel to assert means no precondition worth the name:
        # {"channel": None} is the one expected value a VANISHED radio's empty
        # live extract would satisfy, quietly defeating the missing-target drift
        # guard on a device-mutating PUT. Refuse to plan instead.
        return None
    live = _as_int(radio.get("channel"))
    if expect_channel is not None and live is not None and live != expect_channel:
        return None
    if live is not None:
        current = live
    if current == target:
        return None

    new_table = copy.deepcopy(list(radio_table))
    for entry in new_table:
        if entry.get("radio") == band_code:
            entry["channel"] = target

    endpoint = f"rest/device/{dev_id}"
    payload = {"radio_table": new_table}
    return FixStep(
        action=ActionType.CHANNEL_CHANGE,
        target_entity_type=EntityType.RADIO,
        target_native_id=native_id,
        description=description,
        risk=RiskLevel.MEDIUM,
        method="PUT",
        endpoint=endpoint,
        payload=payload,
        # The precondition asserts the CONFIGURED channel -- the same
        # ``radio_table`` field the applier's live read extracts -- not the
        # operating channel the detector observed. On an auto-channel radio
        # (UniFi's factory default) the two speak different languages: config
        # says "auto" while the evidence says the int the radio happens to be
        # on, and asserting the int against "auto" is a precondition no live
        # read can satisfy. The plan then renders forever and applies never.
        precondition=Precondition(
            target_native_id=native_id,
            expected={"channel": radio.get("channel")},
            description=(f"Radio channel must still be set to {radio.get('channel')}."),
        ),
        before={"method": "PUT", "endpoint": endpoint, "body": {"radio_table": list(radio_table)}},
        after={"method": "PUT", "endpoint": endpoint, "body": payload},
        revertible=True,
    )


def _plan_tx_power_step_down(
    finding: Finding, device: Optional[Mapping[str, Any]], issue_id: Optional[int]
) -> FixPlan:
    """Step the loud radio's tx-power down one level (never below ``low``)."""
    subtype = str(finding.evidence.get("subtype") or finding.dims.get("subtype") or "loud_power")
    if subtype != "loud_power":
        return _advisory(
            finding,
            issue_id,
            f"tx-power sub-case '{subtype}' is a coverage-balance judgement; adjust manually.",
        )

    band_code = _band_code(finding)
    dev_id, radio_table, radio = _locate_radio(device, band_code)
    if dev_id is None or radio is None:
        return _advisory(
            finding, issue_id, "Device radio snapshot unavailable; lower tx-power manually."
        )

    current_mode = str(
        radio.get("tx_power_mode") or finding.evidence.get("tx_power_mode") or ""
    ).lower()
    target_mode = _step_down_power(current_mode)
    if target_mode is None:
        return _advisory(
            finding,
            issue_id,
            f"tx-power already at its lowest ('{current_mode or '?'}'); no safe step-down.",
        )

    new_table = copy.deepcopy(list(radio_table))
    for entry in new_table:
        if entry.get("radio") == band_code:
            entry["tx_power_mode"] = target_mode

    endpoint = f"rest/device/{dev_id}"
    payload = {"radio_table": new_table}
    label = finding.entity.name or finding.entity.native_id
    step = FixStep(
        action=ActionType.TX_POWER_STEP_DOWN,
        target_entity_type=EntityType.RADIO,
        target_native_id=finding.entity.native_id,
        description=f"Step {label} tx-power {current_mode or '?'} -> {target_mode}.",
        risk=RiskLevel.LOW,
        method="PUT",
        endpoint=endpoint,
        payload=payload,
        precondition=Precondition(
            target_native_id=finding.entity.native_id,
            expected={"tx_power_mode": current_mode},
            description=f"Radio tx-power must still be '{current_mode}'.",
        ),
        before={"method": "PUT", "endpoint": endpoint, "body": {"radio_table": list(radio_table)}},
        after={"method": "PUT", "endpoint": endpoint, "body": payload},
        revertible=True,
    )
    return FixPlan(
        detector_key=finding.detector_key,
        entity_native_id=finding.entity.native_id,
        title=f"Lower tx-power on {label} to {target_mode}",
        steps=[step],
        issue_id=issue_id,
    )


def _plan_poe_power_cycle(
    finding: Finding, device: Optional[Mapping[str, Any]], issue_id: Optional[int]
) -> FixPlan:
    """Power-cycle a PoE port, but only for the reboot-loop / PoE-fault sub-case.

    A flapping port whose PoE draw drops to zero between flaps is a device stuck in
    a reboot loop -- a power-cycle is the right, reversible-by-nature nudge. A
    flapping port with no PoE-reboot signal is a physical fault (cable/connector):
    that is advisory, because cycling power fixes nothing a cable caused.
    """
    reboot_loop = _truthy(finding.evidence.get("poe_reboot_loop"))
    poe_fault = _truthy(finding.evidence.get("poe_fault"))
    if not (reboot_loop or poe_fault):
        return _advisory(
            finding,
            issue_id,
            "Port flapping without a PoE reboot-loop signal points at a physical cable/"
            "connector fault; inspect the run on site rather than power-cycling.",
        )

    sw_mac, port_idx = _split_port_native(finding.entity.native_id)
    if sw_mac is None or port_idx is None:
        return _advisory(
            finding,
            issue_id,
            "Could not resolve switch/port from the entity; power-cycle manually.",
        )

    endpoint = "cmd/devmgr"
    payload = {"cmd": "power-cycle", "mac": sw_mac, "port_idx": port_idx}
    label = finding.entity.name or finding.entity.native_id
    # A power-cycle is a transient command, not a persisted config change: there is
    # no stored config to restore, so the step is not revertible (before=None).
    expected: dict[str, Any] = {}
    if device is not None:
        port = _find_port(device, port_idx)
        if port is not None and port.get("poe_mode") is not None:
            expected = {"poe_mode": port.get("poe_mode")}
    step = FixStep(
        action=ActionType.POE_POWER_CYCLE,
        target_entity_type=EntityType.PORT,
        target_native_id=finding.entity.native_id,
        description=f"Power-cycle PoE on {label} (port {port_idx} of {sw_mac}).",
        risk=RiskLevel.MEDIUM,
        method="POST",
        endpoint=endpoint,
        payload=payload,
        precondition=Precondition(
            target_native_id=finding.entity.native_id,
            expected=expected,
            description="Port must still be the flapping PoE port.",
        ),
        before=None,
        after={"method": "POST", "endpoint": endpoint, "body": payload},
        revertible=False,
    )
    return FixPlan(
        detector_key=finding.detector_key,
        entity_native_id=finding.entity.native_id,
        title=f"Power-cycle PoE port {port_idx} on {sw_mac}",
        steps=[step],
        issue_id=issue_id,
    )


# ---------------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------------- #
def _advisory(finding: Finding, issue_id: Optional[int], note: str) -> FixPlan:
    """A step-less plan: manual action required, with a human-readable note."""
    return FixPlan(
        detector_key=finding.detector_key,
        entity_native_id=finding.entity.native_id,
        title=f"Manual action required: {finding.title}",
        steps=[],
        advisory=note,
        manual_action_required=True,
        issue_id=issue_id,
    )


def _physical_note(key: str, native: str) -> str:
    notes = {
        "wired.bad_cable": (
            f"{native}: replace or reseat the cable/SFP and re-test the run. "
            "No controller setting fixes a physical link fault."
        ),
        "wifi.mesh_uplink": (
            f"{native}: the wireless uplink RSSI is too weak. Reposition the AP, add a "
            "wired backhaul, or add a relay node. Not a controller-side change."
        ),
        "net.coverage_hole": (
            f"{native}: clients see no acceptable AP here. Add or reposition an AP to "
            "cover the dead zone. Not a controller-side change."
        ),
    }
    return notes.get(key, f"{native}: manual, on-site remediation required.")


def _band_code(finding: Finding) -> Optional[str]:
    """The controller radio code ('ng'/'na'/'6e') from the RADIO entity native id."""
    native = finding.entity.native_id
    if native and ":" in native:
        return native.rsplit(":", 1)[-1]
    raw = finding.entity.meta.get("band") if finding.entity.meta else None
    return str(raw) if raw is not None else None


def _locate_radio(
    device: Optional[Mapping[str, Any]], band_code: Optional[str]
) -> tuple[Optional[str], list[dict[str, Any]], Optional[dict[str, Any]]]:
    """Resolve (device_id, radio_table, target radio dict) from a raw device object."""
    if device is None or band_code is None:
        return None, [], None
    dev_id = device.get("_id") or device.get("id")
    radio_table = list(device.get("radio_table") or [])
    if not dev_id or not radio_table:
        return None, radio_table, None
    target = next((r for r in radio_table if r.get("radio") == band_code), None)
    return str(dev_id), radio_table, target


def _find_port(device: Mapping[str, Any], port_idx: int) -> Optional[dict[str, Any]]:
    for port in device.get("port_table") or []:
        if _as_int(port.get("port_idx")) == port_idx:
            return port
    return None


def _split_port_native(native: str) -> tuple[Optional[str], Optional[int]]:
    """Split a port native id ``"<sw_mac>:<port_idx>"`` into its parts."""
    if not native or ":" not in native:
        return None, None
    mac, _, idx = native.rpartition(":")
    return (mac or None), _as_int(idx)


def _candidate_channels(evidence: Mapping[str, Any]) -> tuple[int, ...]:
    """The channels the band may be re-planned across (evidence, else 1/6/11)."""
    raw = evidence.get("candidate_channels")
    channels: list[int] = []
    if isinstance(raw, (list, tuple)):
        for value in raw:
            parsed = _as_int(value)
            if parsed is not None and parsed not in channels:
                channels.append(parsed)
    return tuple(sorted(channels)) if channels else _VALID_24_CHANNELS


def _drifted_radios(
    conflicted: Mapping[int, list[str]],
    devices: Mapping[str, Any],
    band: str,
) -> list[str]:
    """Conflicted radios whose live channel no longer matches the evidence.

    Only radios we actually hold a live device read for can be checked; one we
    were not handed is left alone, because "unknown" is not "drifted". A pinned
    radio is compared on its configured channel; an auto radio has no configured
    int to compare, so it is compared on the OPERATING channel the device's
    ``radio_table_stats`` reports -- an auto radio that has hopped since
    detection has invalidated the load vector exactly as a re-pinned one has,
    and skipping it (the old behaviour) let a hopped auto radio sail into a
    joint solve built on a band layout it already left. Returns readable native
    ids so the advisory can name them.
    """
    out: list[str] = []
    for channel, natives in conflicted.items():
        for native_id in natives:
            device = devices.get(_device_mac(native_id))
            if device is None:
                continue
            band_code = native_id.rsplit(":", 1)[-1] if ":" in native_id else None
            _dev_id, _table, radio = _locate_radio(device, band_code)
            if radio is None:
                continue
            live = _as_int(radio.get("channel"))
            if live is None:
                live = _operating_channel(device, band_code)
            if live is not None and live != channel:
                out.append(native_id)
    return sorted(out)


def _operating_channel(device: Mapping[str, Any], band_code: Optional[str]) -> Optional[int]:
    """The channel a radio is transmitting on, from ``radio_table_stats``.

    The configured channel lives in ``radio_table`` and can be the string
    "auto"; the stats table carries the int the auto planner actually chose.
    ``None`` when the device does not report stats for the radio.
    """
    for entry in device.get("radio_table_stats") or []:
        if isinstance(entry, Mapping) and entry.get("radio") == band_code:
            return _as_int(entry.get("channel"))
    return None


def _channel_loads(evidence: Mapping[str, Any], candidates: tuple[int, ...]) -> dict[int, int]:
    """Our radios per candidate channel, as the detector counted them.

    ``per_channel`` covers the whole band, including channels carrying a single
    radio -- which is what stops the solver from moving a radio onto a channel
    that is already occupied but not itself in conflict. Older evidence without it
    falls back to the conflict groups, which is a lower bound, never a wrong one.
    """
    loads = {ch: 0 for ch in candidates}
    per_channel = evidence.get("per_channel")
    if isinstance(per_channel, Mapping):
        for key, value in per_channel.items():
            channel, count = _as_int(key), _as_int(value)
            if channel in loads and count is not None:
                loads[channel] = count
        return loads
    for channel, radios in _conflict_groups(evidence):
        if channel in loads:
            loads[channel] = len(radios)
    return loads


def _conflict_groups(evidence: Mapping[str, Any]) -> list[tuple[int, list[str]]]:
    """``[(channel, [radio native_id, ...])]`` from the finding's conflict groups."""
    groups: list[tuple[int, list[str]]] = []
    for group in evidence.get("conflict_groups") or ():
        if not isinstance(group, Mapping):
            continue
        channel = _as_int(group.get("channel"))
        if channel is None:
            continue
        natives = [
            str(radio.get("native_id"))
            for radio in (group.get("radios") or ())
            if isinstance(radio, Mapping) and radio.get("native_id")
        ]
        if natives:
            groups.append((channel, sorted(natives)))
    return sorted(groups)


def _conflicted_radios(evidence: Mapping[str, Any]) -> dict[int, list[str]]:
    """Movable radios per channel, sorted by native id so a plan is reproducible."""
    return {channel: natives for channel, natives in _conflict_groups(evidence)}


def _solve_channel_spread(
    loads: dict[int, int],
    conflicted: Mapping[int, list[str]],
    candidates: tuple[int, ...],
) -> list[tuple[str, int, int]]:
    """Assign channels jointly: ``[(radio native_id, from_channel, to_channel)]``.

    Greedy and deterministic: while the busiest candidate channel carries at least
    two more radios than the quietest one, move the lowest-native-id radio off the
    busiest onto the quietest and re-count. Ties break on the lower channel
    number, so the same evidence always yields the same plan -- which is what
    makes the confirm token stable between the dry-run a human reads and the apply
    they authorise. Each move strictly reduces the imbalance, so the loop
    terminates; it also stops at :data:`MAX_JOINT_CHANNEL_MOVES`.

    Because the target is re-chosen from the *updated* loads, two radios are never
    told to move onto a channel that a previous move already filled past the
    quietest one -- the exact way independent per-radio rotations used to recreate
    the conflict they were fixing.
    """
    if not candidates:
        return []
    working = {ch: loads.get(ch, 0) for ch in candidates}
    pools = {ch: list(natives) for ch, natives in conflicted.items()}
    moves: list[tuple[str, int, int]] = []
    while len(moves) < MAX_JOINT_CHANNEL_MOVES:
        source = max(candidates, key=lambda ch: (working[ch], -ch))
        target = min(candidates, key=lambda ch: (working[ch], ch))
        if working[source] - working[target] < 2:
            return moves  # balanced as far as this band can be
        pool = pools.get(source)
        if not pool:
            return moves  # nothing movable is left on the busiest channel
        native = pool.pop(0)
        working[source] -= 1
        working[target] += 1
        moves.append((native, source, target))
    return moves


def _device_mac(native_id: str) -> str:
    """The device MAC under a radio native id (``"<mac>:<band>"``), lower-cased."""
    if native_id.count(":") >= 6:
        return native_id.rsplit(":", 1)[0].lower()
    return native_id.lower()


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _recommend_24_channel(current: Optional[int], subtype: str) -> Optional[int]:
    """Pick a 1/6/11 channel: nearest grid slot for off-grid, a rotation for reuse."""
    if subtype == "channel_off_grid":
        if current is None:
            return _VALID_24_CHANNELS[0]
        return min(_VALID_24_CHANNELS, key=lambda c: (abs(c - current), c))
    if subtype == "co_channel_reuse":
        # Deterministic rotation onto the next non-overlapping channel.
        rotation = {1: 6, 6: 11, 11: 1}
        if current in rotation:
            return rotation[current]
        return _VALID_24_CHANNELS[0]
    return None


def _step_down_power(mode: str) -> Optional[str]:
    """One level quieter: high->medium, medium->low, auto->medium. None at floor."""
    mode = (mode or "").lower()
    if mode == "auto":
        return "medium"
    if mode in TX_POWER_ORDER:
        idx = TX_POWER_ORDER.index(mode)
        if idx > 0:
            return TX_POWER_ORDER[idx - 1]
        return None  # already at "low"
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
