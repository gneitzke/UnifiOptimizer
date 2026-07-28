"""Planner: correct payloads for the safe templates, advisories for the rest.

Every assertion is on the *rendered payload* the planner produced -- the exact body
a dry-run would show and a confirmed apply would send -- never on a network effect.
"""

from __future__ import annotations

from netadmin.domain.types import EntityType, Severity
from netadmin.fixes.applier import DEFAULT_MAX_DEVICES, DEFAULT_MAX_STEPS
from netadmin.fixes.models import ActionType, plan_confirm_token
from netadmin.fixes.planner import MAX_JOINT_CHANNEL_MOVES, plan_fix

from .conftest import (
    AP_ID,
    AP_MAC,
    SW_MAC,
    make_ap_device,
    make_finding,
    make_switch_device,
    port_entity,
    radio_entity,
    rf_entity,
)


def _radio(payload, band):
    return next(r for r in payload["radio_table"] if r["radio"] == band)


# --------------------------------------------------------------------------- #
# min-RSSI removal -- removal only, ever
# --------------------------------------------------------------------------- #
def test_min_rssi_plan_only_disables_and_preserves_other_radios(ap_device):
    finding = make_finding(
        "wifi.min_rssi_misconfig",
        radio_entity("ng"),
        severity=Severity.P2,
        evidence={"min_rssi_dbm": -75, "reason": "mesh_uplink_ap", "on_mesh_ap": True},
    )
    plan = plan_fix(finding, device=ap_device)

    assert not plan.is_advisory
    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert step.action is ActionType.MIN_RSSI_REMOVE
    assert step.method == "PUT"
    assert step.endpoint == f"rest/device/{AP_ID}"
    # The target radio is disabled; the min_rssi floor value is preserved (removal
    # is a disable, not a rewrite), and the *other* radio is untouched.
    ng = _radio(step.payload, "ng")
    assert ng["min_rssi_enabled"] is False
    assert ng["min_rssi"] == -75
    assert _radio(step.payload, "na")["min_rssi_enabled"] is False
    # Precondition: it must still be enabled to be worth removing.
    assert step.precondition.expected == {"min_rssi_enabled": True}
    assert step.precondition.target_native_id == f"{AP_MAC}:ng"
    # Revert restores the original (enabled) table.
    assert step.revertible is True
    assert _radio(step.before["body"], "ng")["min_rssi_enabled"] is True


def test_min_rssi_plan_is_advisory_when_already_disabled():
    device = make_ap_device(radios=[{"radio": "ng", "min_rssi_enabled": False, "min_rssi": 0}])
    finding = make_finding("wifi.min_rssi_misconfig", radio_entity("ng"))
    plan = plan_fix(finding, device=device)
    assert plan.is_advisory
    assert plan.manual_action_required


def test_min_rssi_plan_never_emits_a_set_even_for_strict_floor(ap_device):
    # The stricter-than-floor sub-case is still handled by *removal*, never by
    # writing a looser floor -- the planner has no code path that enables min-RSSI.
    finding = make_finding(
        "wifi.min_rssi_misconfig",
        radio_entity("ng"),
        evidence={"reason": "stricter_than_floor", "min_rssi_dbm": -60, "on_mesh_ap": False},
    )
    plan = plan_fix(finding, device=ap_device)
    assert plan.steps[0].payload["radio_table"][0]["min_rssi_enabled"] is False


# --------------------------------------------------------------------------- #
# channel change -- 2.4 GHz grid only
# --------------------------------------------------------------------------- #
def test_channel_off_grid_snaps_to_nearest_valid_channel(ap_device):
    finding = make_finding(
        "wifi.channel_plan",
        radio_entity("ng"),
        dims={"subtype": "channel_off_grid", "band": "2.4"},
        evidence={"subtype": "channel_off_grid", "band": "2.4", "channel": 3},
    )
    plan = plan_fix(finding, device=ap_device)
    step = plan.steps[0]
    assert step.action is ActionType.CHANNEL_CHANGE
    assert _radio(step.payload, "ng")["channel"] == 1  # nearest of 1/6/11 to 3
    assert step.precondition.expected == {"channel": 3}


def test_co_channel_reuse_rotates_onto_next_grid_slot(ap_device):
    device = make_ap_device(radios=[{"radio": "ng", "channel": 6, "ht": 20}])
    finding = make_finding(
        "wifi.channel_plan",
        radio_entity("ng"),
        dims={"subtype": "co_channel_reuse", "band": "2.4"},
        evidence={"subtype": "co_channel_reuse", "band": "2.4", "channel": 6},
    )
    plan = plan_fix(finding, device=device)
    assert _radio(plan.steps[0].payload, "ng")["channel"] == 11


def test_five_ghz_channel_case_is_advisory(ap_device):
    finding = make_finding(
        "wifi.channel_plan",
        radio_entity("na"),
        dims={"subtype": "co_channel_reuse", "band": "5"},
        evidence={"subtype": "co_channel_reuse", "band": "5", "channel": 36},
    )
    plan = plan_fix(finding, device=ap_device)
    assert plan.is_advisory
    assert plan.manual_action_required


def test_wide_channel_subtype_is_advisory(ap_device):
    finding = make_finding(
        "wifi.channel_plan",
        radio_entity("ng"),
        dims={"subtype": "wide_channel_24ghz", "band": "2.4"},
        evidence={"subtype": "wide_channel_24ghz", "band": "2.4", "channel": 6, "ht_mhz": 40},
    )
    plan = plan_fix(finding, device=ap_device)
    assert plan.is_advisory


# --------------------------------------------------------------------------- #
# joint 2.4 GHz re-plan -- the site-scoped, per-band co-channel issue
# --------------------------------------------------------------------------- #
def _band_devices(assignment: dict[str, int]) -> dict[str, dict]:
    """One AP snapshot per radio native id, each on its assigned 2.4 GHz channel."""
    devices = {}
    for i, (native, channel) in enumerate(sorted(assignment.items())):
        mac = native.rsplit(":", 1)[0]
        devices[mac] = make_ap_device(
            device_id=f"dev{i}",
            mac=mac,
            radios=[
                {"radio": "ng", "channel": channel, "ht": 20},
                {"radio": "na", "channel": 36, "ht": 80},
            ],
        )
    return devices


def _band_finding(groups, *, per_channel, band="2.4", subtype="co_channel_reuse"):
    return make_finding(
        "wifi.channel_plan",
        rf_entity(band),
        dims={"subtype": subtype, "band": band},
        evidence={
            "subtype": subtype,
            "band": band,
            "conflict_groups": [
                {"channel": ch, "radios": [{"native_id": n} for n in natives]}
                for ch, natives in groups
            ],
            "candidate_channels": [1, 6, 11],
            "per_channel": per_channel,
            "unused_candidates": [ch for ch, n in per_channel.items() if n == 0],
        },
    )


def test_band_replan_moves_only_one_end_of_a_pair():
    """The regression that motivated the split: a per-radio rotation applied to both
    members of a conflict moved BOTH onto the same new channel. Planned jointly,
    exactly one radio moves and the pair ends up on different channels."""
    a, b = "aa:bb:cc:00:00:01:ng", "aa:bb:cc:00:00:02:ng"
    finding = _band_finding([(6, [a, b])], per_channel={"1": 0, "6": 2, "11": 0})
    devices = _band_devices({a: 6, b: 6})

    plan = plan_fix(finding, devices=devices)

    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert step.action is ActionType.CHANNEL_CHANGE
    assert step.target_native_id == a  # lowest native id, deterministically
    assert _radio(step.payload, "ng")["channel"] == 1  # the quietest candidate
    assert step.precondition.expected == {"channel": 6}
    assert step.revertible is True


def test_band_replan_spreads_a_pile_across_distinct_channels():
    natives = [f"aa:bb:cc:00:00:0{i}:ng" for i in (1, 2, 3)]
    finding = _band_finding([(1, natives)], per_channel={"1": 3, "6": 0, "11": 0})
    devices = _band_devices({n: 1 for n in natives})

    plan = plan_fix(finding, devices=devices)

    assert len(plan.steps) == 2  # one radio stays; two move
    targets = [_radio(s.payload, "ng")["channel"] for s in plan.steps]
    assert sorted(targets) == [6, 11]  # never two radios onto one channel
    # One PUT per device, each preserving that device's other radio.
    assert {s.endpoint for s in plan.steps} == {"rest/device/dev0", "rest/device/dev1"}
    assert plan.device_count == 2
    for step in plan.steps:
        assert _radio(step.payload, "na")["channel"] == 36


def test_band_replan_is_deterministic_so_the_confirm_token_is_stable():
    natives = [f"aa:bb:cc:00:00:0{i}:ng" for i in (1, 2, 3)]
    finding = _band_finding([(1, natives)], per_channel={"1": 3, "6": 0, "11": 0})
    devices = _band_devices({n: 1 for n in natives})

    first = plan_confirm_token(plan_fix(finding, devices=devices))
    second = plan_confirm_token(plan_fix(finding, devices=devices))
    assert first == second


def test_band_replan_respects_channels_already_carrying_a_radio():
    """A candidate holding one radio is not empty: the busiest channel is drained
    onto the genuinely quietest one, not onto whatever looks free in the groups."""
    natives = [f"aa:bb:cc:00:00:0{i}:ng" for i in (1, 2, 3)]
    finding = _band_finding([(1, natives)], per_channel={"1": 3, "6": 2, "11": 0})
    devices = _band_devices({n: 1 for n in natives})

    plan = plan_fix(finding, devices=devices)
    assert [_radio(s.payload, "ng")["channel"] for s in plan.steps] == [11]


def test_band_replan_never_exceeds_the_appliers_max_device_guard():
    natives = [f"aa:bb:cc:00:00:0{i}:ng" for i in range(1, 8)]
    finding = _band_finding([(1, natives)], per_channel={"1": 7, "6": 0, "11": 0})
    devices = _band_devices({n: 1 for n in natives})

    plan = plan_fix(finding, devices=devices)
    assert len(plan.steps) == MAX_JOINT_CHANNEL_MOVES
    assert MAX_JOINT_CHANNEL_MOVES <= DEFAULT_MAX_DEVICES
    assert MAX_JOINT_CHANNEL_MOVES <= DEFAULT_MAX_STEPS
    assert plan.device_count <= DEFAULT_MAX_DEVICES


def test_band_replan_is_advisory_when_the_spread_is_already_optimal():
    natives = [f"aa:bb:cc:00:00:0{i}:ng" for i in (1, 2)]
    finding = _band_finding([(1, natives)], per_channel={"1": 2, "6": 1, "11": 1})
    plan = plan_fix(finding, devices=_band_devices({n: 1 for n in natives}))
    assert plan.is_advisory
    assert plan.manual_action_required


def test_band_replan_drops_a_radio_whose_live_channel_moved():
    """Evidence says channel 1; the live radio is already on 11. Planning it would
    act on a stale assumption, so that move is dropped rather than guessed."""
    a, b = "aa:bb:cc:00:00:01:ng", "aa:bb:cc:00:00:02:ng"
    finding = _band_finding([(1, [a, b])], per_channel={"1": 2, "6": 0, "11": 0})
    devices = _band_devices({a: 11, b: 1})

    plan = plan_fix(finding, devices=devices)
    assert plan.is_advisory


def test_band_replan_without_snapshots_is_advisory():
    a, b = "aa:bb:cc:00:00:01:ng", "aa:bb:cc:00:00:02:ng"
    finding = _band_finding([(1, [a, b])], per_channel={"1": 2, "6": 0, "11": 0})
    plan = plan_fix(finding, devices={})
    assert plan.is_advisory


def test_band_replan_refuses_five_ghz_and_width_subtypes():
    a, b = "aa:bb:cc:00:00:01:na", "aa:bb:cc:00:00:02:na"
    five = _band_finding([(36, [a, b])], per_channel={"36": 2}, band="5")
    assert plan_fix(five, devices=_band_devices({a: 36, b: 36})).is_advisory

    width = make_finding(
        "wifi.channel_plan",
        rf_entity("5"),
        dims={"subtype": "wide_channel_dense_5ghz", "band": "5"},
        evidence={"subtype": "wide_channel_dense_5ghz", "band": "5", "ap_count": 4},
    )
    plan = plan_fix(width, devices={})
    assert plan.is_advisory
    assert plan.manual_action_required


# --------------------------------------------------------------------------- #
# tx-power step-down
# --------------------------------------------------------------------------- #
def test_tx_power_high_steps_to_medium(ap_device):
    finding = make_finding(
        "wifi.tx_power_loud",
        radio_entity("ng"),
        dims={"subtype": "loud_power", "band": "2.4"},
        evidence={"tx_power_mode": "high"},
    )
    plan = plan_fix(finding, device=ap_device)
    step = plan.steps[0]
    assert step.action is ActionType.TX_POWER_STEP_DOWN
    assert _radio(step.payload, "ng")["tx_power_mode"] == "medium"
    assert step.precondition.expected == {"tx_power_mode": "high"}


def test_tx_power_auto_steps_to_medium():
    device = make_ap_device(radios=[{"radio": "na", "channel": 36, "tx_power_mode": "auto"}])
    finding = make_finding(
        "wifi.tx_power_loud",
        radio_entity("na"),
        evidence={"tx_power_mode": "auto"},
    )
    plan = plan_fix(finding, device=device)
    assert _radio(plan.steps[0].payload, "na")["tx_power_mode"] == "medium"


def test_tx_power_already_low_is_advisory():
    device = make_ap_device(radios=[{"radio": "ng", "channel": 6, "tx_power_mode": "low"}])
    finding = make_finding(
        "wifi.tx_power_loud", radio_entity("ng"), evidence={"tx_power_mode": "low"}
    )
    plan = plan_fix(finding, device=device)
    assert plan.is_advisory


# --------------------------------------------------------------------------- #
# PoE power-cycle -- reboot loop / fault only
# --------------------------------------------------------------------------- #
def test_poe_cycle_planned_for_reboot_loop(switch_device):
    finding = make_finding(
        "wired.port_flapping",
        port_entity(5),
        severity=Severity.P1,
        evidence={"poe_reboot_loop": True, "poe_min_w": 0.0, "poe_max_w": 6.5},
    )
    plan = plan_fix(finding, device=switch_device)
    step = plan.steps[0]
    assert step.action is ActionType.POE_POWER_CYCLE
    assert step.method == "POST"
    assert step.endpoint == "cmd/devmgr"
    assert step.payload == {"cmd": "power-cycle", "mac": SW_MAC, "port_idx": 5}
    assert step.revertible is False  # a transient command, nothing to restore
    assert step.before is None


def test_port_flapping_without_reboot_loop_is_advisory(switch_device):
    finding = make_finding(
        "wired.port_flapping",
        port_entity(5),
        evidence={"transitions_short": 7},  # no PoE reboot signal -> physical
    )
    plan = plan_fix(finding, device=switch_device)
    assert plan.is_advisory
    assert plan.manual_action_required
    assert "physical" in (plan.advisory or "").lower()


# --------------------------------------------------------------------------- #
# Physical-issue refusals + unknown detector
# --------------------------------------------------------------------------- #
def test_physical_issues_return_advisory_with_empty_steps():
    for key in ("wired.bad_cable", "wifi.mesh_uplink", "net.coverage_hole"):
        entity = radio_entity("ng") if key.startswith("wifi") else port_entity(1)
        plan = plan_fix(make_finding(key, entity))
        assert plan.steps == []
        assert plan.manual_action_required
        assert plan.advisory


def test_unknown_detector_returns_advisory():
    plan = plan_fix(make_finding("wifi.some_new_thing", radio_entity("ng")))
    assert plan.is_advisory
    assert plan.manual_action_required


def test_missing_device_snapshot_is_advisory_not_a_guess():
    finding = make_finding(
        "wifi.channel_plan",
        radio_entity("ng"),
        dims={"subtype": "channel_off_grid", "band": "2.4"},
        evidence={"subtype": "channel_off_grid", "band": "2.4", "channel": 3},
    )
    plan = plan_fix(finding, device=None)
    assert plan.is_advisory


def test_device_count_collapses_radios_to_one_device(ap_device):
    finding = make_finding(
        "wifi.tx_power_loud", radio_entity("ng"), evidence={"tx_power_mode": "high"}
    )
    plan = plan_fix(finding, device=ap_device)
    assert plan.device_count == 1
    assert plan.steps[0].target_entity_type is EntityType.RADIO


def test_band_replan_refuses_when_the_live_layout_drifted_from_the_evidence():
    """A joint plan solved against a stale layout can make the spread WORSE.

    Reviewer's reproduction: the detector recorded 1:{A,B} 6:{C,D} 11:{}, then the
    ch6 pair drifted to 11 overnight. Solving on the old vector moves A from 1 to
    11, which is now the busiest channel: imbalance goes from 2 to 3 and the free
    channel 6 is left empty. The planner only holds live reads for the conflicted
    radios, so it cannot recompute the whole band; refusing is the honest move.
    """
    a, b = "aa:bb:cc:00:00:01:ng", "aa:bb:cc:00:00:02:ng"
    c, d = "aa:bb:cc:00:00:03:ng", "aa:bb:cc:00:00:04:ng"
    finding = _band_finding(
        [(1, [a, b]), (6, [c, d])],
        per_channel={"1": 2, "6": 2, "11": 0},
    )
    # Live: the ch6 pair actually sits on 11 now.
    devices = _band_devices({a: 1, b: 1, c: 11, d: 11})

    plan = plan_fix(finding, devices=devices)

    assert plan.steps == []
    assert plan.manual_action_required is True
    assert "changed since this was detected" in plan.advisory.lower()


def test_band_replan_still_plans_when_the_live_layout_matches():
    """The guard must refuse only on real drift, never on a healthy plan."""
    a, b = "aa:bb:cc:00:00:01:ng", "aa:bb:cc:00:00:02:ng"
    finding = _band_finding([(6, [a, b])], per_channel={"1": 0, "6": 2, "11": 0})
    devices = _band_devices({a: 6, b: 6})

    plan = plan_fix(finding, devices=devices)

    assert len(plan.steps) == 1
    assert plan.manual_action_required is False


# --------------------------------------------------------------------------- #
# auto-channel radios: the plan must be appliable, not just renderable
# --------------------------------------------------------------------------- #
def test_auto_channel_radio_precondition_expects_auto():
    """The precondition must assert the CONFIGURED channel, not the operating one.

    Auto is UniFi's factory default. The detector's evidence carries the channel
    the radio is operating on (an int); the device object's configured channel is
    the string "auto". Asserting the operating int against the configured string
    is a precondition no live read can ever satisfy: the plan renders, the apply
    always aborts with drift, and the fix is dead on arrival for every
    default-configured radio — which is how it shipped.
    """
    device = make_ap_device(radios=[{"radio": "ng", "channel": "auto", "ht": 20}])
    finding = make_finding(
        "wifi.channel_plan",
        radio_entity("ng"),
        dims={"subtype": "channel_off_grid", "band": "2.4"},
        evidence={"subtype": "channel_off_grid", "band": "2.4", "channel": 3},
    )
    plan = plan_fix(finding, device=device)
    step = plan.steps[0]
    assert step.action is ActionType.CHANNEL_CHANGE
    assert step.precondition.expected == {"channel": "auto"}


def test_auto_channel_radio_with_no_channel_key_is_not_planned():
    """A radio_table entry with no channel at all yields an advisory, not a step.

    ``{"channel": None}`` is the one expected value a VANISHED radio's empty live
    extract would satisfy — planning on it would quietly defeat the
    missing-target drift guard on a device-mutating PUT.
    """
    device = make_ap_device(radios=[{"radio": "ng", "ht": 20}])
    finding = make_finding(
        "wifi.channel_plan",
        radio_entity("ng"),
        dims={"subtype": "channel_off_grid", "band": "2.4"},
        evidence={"subtype": "channel_off_grid", "band": "2.4", "channel": 3},
    )
    plan = plan_fix(finding, device=device)
    assert plan.steps == []
    assert plan.manual_action_required


def test_band_replan_drops_an_auto_radio_that_hopped_since_detection():
    """An auto radio is drift-checked on its OPERATING channel from stats.

    Config "auto" never changes, so the configured-channel comparison is blind
    to a hop; skipping auto radios (the old behaviour) let a joint solve run
    against a band layout the radio had already left.
    """
    natives = [f"aa:bb:cc:00:00:0{i}:ng" for i in (1, 2)]
    finding = _band_finding([(1, natives)], per_channel={"1": 2, "6": 0, "11": 0})
    devices = {}
    for i, native in enumerate(sorted(natives)):
        mac = native.rsplit(":", 1)[0]
        devices[mac] = make_ap_device(
            device_id=f"dev{i}",
            mac=mac,
            radios=[{"radio": "ng", "channel": "auto", "ht": 20}],
        )
        # dev0 hopped to 6 since detection; dev1 still operates on 1.
        devices[mac]["radio_table_stats"] = [{"radio": "ng", "channel": 6 if i == 0 else 1}]

    plan = plan_fix(finding, devices=devices)
    assert plan.steps == []
    assert plan.manual_action_required


def test_band_replan_of_unhopped_auto_radios_renders_and_survives_the_live_read():
    """The factory-default fleet case: all-auto radios, none hopped, plan applies.

    This is the joint-path twin of the single-radio regression test — the
    multi-AP scenario is where the unsatisfiable precondition actually bit.
    """
    from netadmin.fixes.applier import Applier
    from netadmin.fixes.service import _extract_target_attrs

    natives = [f"aa:bb:cc:00:00:0{i}:ng" for i in (1, 2)]
    finding = _band_finding([(1, natives)], per_channel={"1": 2, "6": 0, "11": 0})
    devices = {}
    for i, native in enumerate(sorted(natives)):
        mac = native.rsplit(":", 1)[0]
        devices[mac] = make_ap_device(
            device_id=f"dev{i}",
            mac=mac,
            radios=[{"radio": "ng", "channel": "auto", "ht": 20}],
        )
        devices[mac]["radio_table_stats"] = [{"radio": "ng", "channel": 1}]

    plan = plan_fix(finding, devices=devices)
    assert len(plan.steps) == 1  # one radio moves off the shared channel
    step = plan.steps[0]
    assert step.precondition.expected == {"channel": "auto"}

    mac = step.precondition.target_native_id.rsplit(":", 1)[0]
    live = _extract_target_attrs(
        devices[mac], step.precondition.target_native_id, step.precondition.expected
    )
    # State-free check; instantiating a full Applier here would add nothing.
    drifted = Applier.__new__(Applier)._precondition_drift(
        plan, {step.precondition.target_native_id: live}
    )
    assert drifted == []
