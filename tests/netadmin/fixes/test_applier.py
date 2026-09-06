"""Applier: dry-run sends nothing; a real apply is gated six ways; revert works.

The FakeControllerWriter's ``calls`` list is the load-bearing assertion throughout:
an empty list proves a code path reached no network. No test here ever constructs a
RealControllerWriter, and one test proves the dry-run path cannot.
"""

from __future__ import annotations

import asyncio
import gc
import json
import threading
import weakref

import pytest

from netadmin.domain.types import EntityType
from netadmin.fixes import writer as writer_mod
from netadmin.fixes.applier import Applier
from netadmin.fixes.models import (
    ActionType,
    ApplyResult,
    ConfirmTokenError,
    DryRunResult,
    FixError,
    FixPlan,
    FixStep,
    MaxStepsExceeded,
    Precondition,
    PreconditionDrift,
    RiskLevel,
    SafetyViolation,
    WriterRequired,
    plan_confirm_token,
)
from netadmin.fixes.planner import plan_fix
from netadmin.fixes.writer import FakeControllerWriter

from .conftest import AP_ID, AP_MAC, make_ap_device, make_finding, radio_entity

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _channel_plan(ap_device, issue_id=None):
    finding = make_finding(
        "wifi.channel_plan",
        radio_entity("ng"),
        dims={"subtype": "channel_off_grid", "band": "2.4"},
        evidence={"subtype": "channel_off_grid", "band": "2.4", "channel": 3},
    )
    return plan_fix(finding, device=ap_device, issue_id=issue_id)


def _state_ok():
    # Live state that matches the channel-plan precondition (channel still 3).
    return {f"{AP_MAC}:ng": {"channel": 3}}


# --------------------------------------------------------------------------- #
# Dry run sends nothing
# --------------------------------------------------------------------------- #
async def test_dry_run_renders_payloads_and_sends_nothing(store, ap_device):
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    plan = _channel_plan(ap_device)

    result = await applier.apply(plan)  # dry_run defaults True

    assert isinstance(result, DryRunResult)
    assert writer.call_count == 0  # nothing sent
    assert store.list_changes() == []  # nothing ledgered
    assert result.rendered[0]["endpoint"] == f"rest/device/{AP_ID}"
    assert result.rendered[0]["payload"]["radio_table"][0]["channel"] == 1
    assert result.confirm_token == plan_confirm_token(plan)


async def test_render_never_touches_writer_even_when_present(store, ap_device):
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    applier.render(_channel_plan(ap_device))
    assert writer.call_count == 0


async def test_real_controller_writer_never_constructed_in_dry_run(monkeypatch, store, ap_device):
    """Structural proof: nothing on the dry-run path instantiates the real writer."""
    constructed = []
    real_init = writer_mod.RealControllerWriter.__init__

    def _spy_init(self, client):
        constructed.append(client)
        return real_init(self, client)

    monkeypatch.setattr(writer_mod.RealControllerWriter, "__init__", _spy_init)

    applier = Applier(store, FakeControllerWriter())
    await applier.apply(_channel_plan(ap_device))  # dry run
    applier.render(_channel_plan(ap_device))

    assert constructed == []


# --------------------------------------------------------------------------- #
# Confirm token
# --------------------------------------------------------------------------- #
async def test_apply_without_confirm_token_is_refused(store, ap_device):
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    plan = _channel_plan(ap_device)
    with pytest.raises(ConfirmTokenError):
        await applier.apply(plan, dry_run=False, confirm_token=None, current_state=_state_ok())
    assert writer.call_count == 0


async def test_apply_with_mismatched_token_is_refused(store, ap_device):
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    plan = _channel_plan(ap_device)
    with pytest.raises(ConfirmTokenError):
        await applier.apply(
            plan, dry_run=False, confirm_token="deadbeef", current_state=_state_ok()
        )
    assert writer.call_count == 0


# --------------------------------------------------------------------------- #
# Writer required for a real apply
# --------------------------------------------------------------------------- #
async def test_real_apply_without_writer_is_refused(store, ap_device):
    applier = Applier(store, writer=None)  # render-only instance
    plan = _channel_plan(ap_device)
    with pytest.raises(WriterRequired):
        await applier.apply(
            plan,
            dry_run=False,
            confirm_token=plan_confirm_token(plan),
            current_state=_state_ok(),
        )


# --------------------------------------------------------------------------- #
# Precondition drift aborts the whole plan
# --------------------------------------------------------------------------- #
async def test_precondition_drift_aborts_before_any_call(store, ap_device):
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    plan = _channel_plan(ap_device)
    drifted_state = {f"{AP_MAC}:ng": {"channel": 6}}  # someone already moved it
    with pytest.raises(PreconditionDrift):
        await applier.apply(
            plan,
            dry_run=False,
            confirm_token=plan_confirm_token(plan),
            current_state=drifted_state,
        )
    assert writer.call_count == 0
    assert store.list_changes() == []  # aborted before ledgering


async def test_missing_live_state_counts_as_drift(store, ap_device):
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    plan = _channel_plan(ap_device)
    with pytest.raises(PreconditionDrift):
        await applier.apply(
            plan, dry_run=False, confirm_token=plan_confirm_token(plan), current_state={}
        )
    assert writer.call_count == 0


# --------------------------------------------------------------------------- #
# Happy-path apply: before/after recorded, one call, status applied
# --------------------------------------------------------------------------- #
async def test_apply_sends_one_call_and_records_before_after(store, ap_device):
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    plan = _channel_plan(ap_device, issue_id=None)

    result = await applier.apply(
        plan, dry_run=False, confirm_token=plan_confirm_token(plan), current_state=_state_ok()
    )

    assert isinstance(result, ApplyResult)
    assert result.applied is True
    assert writer.call_count == 1
    sent = writer.calls[0]
    assert sent.method == "PUT"
    assert sent.endpoint == f"rest/device/{AP_ID}"
    assert sent.body["radio_table"][0]["channel"] == 1

    rows = store.list_changes()
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "applied"
    assert row["action"] == ActionType.CHANNEL_CHANGE.value
    before = json.loads(row["before_json"])
    after = json.loads(row["after_json"])
    assert before["body"]["radio_table"][0]["channel"] == 3  # original
    assert after["body"]["radio_table"][0]["channel"] == 1  # applied


async def test_failed_send_records_before_state_and_marks_failed(store, ap_device):
    # Prove the before-state is written BEFORE the send: even when the send fails,
    # the ledger row exists with the captured before-state and a failed status.
    writer = FakeControllerWriter(fail_on={f"PUT rest/device/{AP_ID}"})
    applier = Applier(store, writer)
    plan = _channel_plan(ap_device)

    result = await applier.apply(
        plan, dry_run=False, confirm_token=plan_confirm_token(plan), current_state=_state_ok()
    )

    assert result.applied is False
    assert result.aborted_reason == "step_failed"
    rows = store.list_changes()
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert json.loads(rows[0]["before_json"])["body"]["radio_table"][0]["channel"] == 3


async def test_transport_exception_marks_failed_and_stops(store, ap_device):
    writer = FakeControllerWriter(raise_on={f"PUT rest/device/{AP_ID}"})
    applier = Applier(store, writer)
    plan = _channel_plan(ap_device)
    result = await applier.apply(
        plan, dry_run=False, confirm_token=plan_confirm_token(plan), current_state=_state_ok()
    )
    assert result.applied is False
    assert store.list_changes()[0]["status"] == "failed"


# --------------------------------------------------------------------------- #
# Revert restores the before-state
# --------------------------------------------------------------------------- #
async def test_revert_restores_before_state(store, ap_device):
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    plan = _channel_plan(ap_device)
    result = await applier.apply(
        plan, dry_run=False, confirm_token=plan_confirm_token(plan), current_state=_state_ok()
    )
    change_id = result.change_ids[0]

    # Fresh live radio state (post-apply): full radio entries, min-RSSI unchanged
    # by a channel fix. The revert restores only the touched field (channel) on top
    # of current live values.
    live_radios = {
        "ng": {
            "radio": "ng",
            "channel": 3,
            "min_rssi_enabled": True,
            "min_rssi": -75,
            "tx_power_mode": "high",
        },
        "na": {"radio": "na", "channel": 36, "min_rssi_enabled": False, "min_rssi": 0},
    }
    revert = await applier.revert(change_id, current_radios=live_radios)

    assert revert.ok
    # The revert PUT carries the ORIGINAL channel (back to 3).
    last = writer.calls[-1]
    assert last.method == "PUT"
    assert last.endpoint == f"rest/device/{AP_ID}"
    assert next(r for r in last.body["radio_table"] if r["radio"] == "ng")["channel"] == 3
    row = store.get_change(change_id)
    assert row["status"] == "reverted"
    assert row["reverted_ts"] is not None


async def test_revert_of_nonrevertible_change_is_refused(store):
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    # A transient command (e.g. a PoE power-cycle) stores no before-body, so it
    # cannot be reverted. Such a change never reaches the ledger through a real
    # apply now -- it is advisory -- but a legacy / hand-inserted row must still be
    # refused cleanly rather than crash.
    change_id = store.insert_change(
        action="wired.poe_power_cycle",
        before={"method": "POST", "endpoint": "cmd/devmgr", "body": {}},
        after={"method": "POST", "endpoint": "cmd/devmgr", "body": {"cmd": "power-cycle"}},
        status="applied",
        ts=1,
    )
    with pytest.raises(FixError):
        await applier.revert(change_id)
    assert writer.call_count == 0


async def test_revert_unknown_change_raises(store):
    applier = Applier(store, FakeControllerWriter())
    with pytest.raises(FixError):
        await applier.revert(999)


# --------------------------------------------------------------------------- #
# Revert is re-gated by the absolute min-RSSI rail (never a back door around it)
# --------------------------------------------------------------------------- #
def _insert_min_rssi_removal_change(store):
    """Insert a ledgered min-RSSI *removal* change directly.

    min-RSSI removal is advisory now (it has no genuine revert), so it never
    reaches the ledger through a real apply. To exercise the revert rail against a
    legacy row, we insert one by hand: ``before`` has min-RSSI enabled, ``after``
    has it disabled -- the shape an old, applied removal left behind.
    """
    endpoint = f"rest/device/{AP_ID}"
    before_body = {
        "radio_table": [
            {"radio": "ng", "channel": 3, "min_rssi_enabled": True, "min_rssi": -75},
            {"radio": "na", "channel": 36, "min_rssi_enabled": False, "min_rssi": 0},
        ]
    }
    after_body = {
        "radio_table": [
            {"radio": "ng", "channel": 3, "min_rssi_enabled": False, "min_rssi": -75},
            {"radio": "na", "channel": 36, "min_rssi_enabled": False, "min_rssi": 0},
        ]
    }
    return store.insert_change(
        action="wifi.min_rssi_remove",
        before={"method": "PUT", "endpoint": endpoint, "body": before_body},
        after={"method": "PUT", "endpoint": endpoint, "body": after_body},
        status="applied",
        ts=1,
    )


async def test_revert_that_would_reenable_min_rssi_is_refused(store):
    # Reverting a min-RSSI *removal* would set min-RSSI back on: the invariant
    # ("only ever removed, never set") forbids it. Live state now has it off.
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    change_id = _insert_min_rssi_removal_change(store)

    with pytest.raises(SafetyViolation):
        await applier.revert(
            change_id,
            current_radios={
                "ng": {"radio": "ng", "channel": 3, "min_rssi_enabled": False, "min_rssi": 0},
                "na": {"radio": "na", "channel": 36, "min_rssi_enabled": False, "min_rssi": 0},
            },
        )
    assert writer.call_count == 0  # revert sent nothing
    assert store.get_change(change_id)["status"] != "reverted"


async def test_revert_enabling_min_rssi_on_mesh_uplink_is_refused(store):
    # Even if min-RSSI happened to be on live, restoring it on an AP that is now a
    # mesh uplink is refused outright (mesh min-RSSI is removal-only).
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    change_id = _insert_min_rssi_removal_change(store)

    with pytest.raises(SafetyViolation):
        await applier.revert(
            change_id,
            current_radios={
                "ng": {"radio": "ng", "channel": 3, "min_rssi_enabled": True, "min_rssi": -75},
                "na": {"radio": "na", "channel": 36, "min_rssi_enabled": False, "min_rssi": 0},
            },
            is_mesh_uplink=True,
        )
    assert writer.call_count == 0


async def test_revert_of_radio_config_without_live_state_is_refused(store):
    # A radio-config restore with no fresh live state read is refused rather than
    # restored blind -- never mutate on unverified state.
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    change_id = _insert_min_rssi_removal_change(store)

    with pytest.raises(SafetyViolation):
        await applier.revert(change_id, current_radios=None)
    assert writer.call_count == 0


# --------------------------------------------------------------------------- #
# Max-N guard
# --------------------------------------------------------------------------- #
def _dummy_step(device_mac: str, idx: int) -> FixStep:
    endpoint = f"rest/device/dev{idx}"
    return FixStep(
        action=ActionType.CHANNEL_CHANGE,
        target_entity_type=EntityType.RADIO,
        target_native_id=f"{device_mac}:ng",
        description="dummy",
        risk=RiskLevel.LOW,
        method="PUT",
        endpoint=endpoint,
        payload={"radio_table": [{"radio": "ng", "channel": 1}]},
        precondition=Precondition(target_native_id=f"{device_mac}:ng", expected={}),
        before={"method": "PUT", "endpoint": endpoint, "body": {"radio_table": []}},
        after={"method": "PUT", "endpoint": endpoint, "body": {}},
    )


def _multi_plan(n_devices: int) -> FixPlan:
    steps = [_dummy_step(f"aa:bb:cc:00:00:{i:02d}", i) for i in range(n_devices)]
    return FixPlan(
        detector_key="wifi.channel_plan",
        entity_native_id="multi",
        title="multi",
        steps=steps,
    )


async def test_max_steps_guard_aborts_without_sending(store):
    writer = FakeControllerWriter()
    applier = Applier(store, writer, max_steps=4, max_devices=99)
    plan = _multi_plan(5)  # 5 steps > 4
    with pytest.raises(MaxStepsExceeded):
        await applier.apply(plan, dry_run=False, confirm_token=plan_confirm_token(plan))
    assert writer.call_count == 0


async def test_max_devices_guard_aborts_without_sending(store):
    writer = FakeControllerWriter()
    applier = Applier(store, writer, max_steps=99, max_devices=2)
    plan = _multi_plan(3)  # 3 distinct devices > 2
    with pytest.raises(MaxStepsExceeded):
        await applier.apply(plan, dry_run=False, confirm_token=plan_confirm_token(plan))
    assert writer.call_count == 0


# --------------------------------------------------------------------------- #
# Absolute min-RSSI rail
# --------------------------------------------------------------------------- #
def _min_rssi_set_plan() -> FixPlan:
    endpoint = f"rest/device/{AP_ID}"
    # A hand-forged plan that would ENABLE min-RSSI (the forbidden direction).
    step = FixStep(
        action=ActionType.MIN_RSSI_REMOVE,  # mislabelled on purpose
        target_entity_type=EntityType.RADIO,
        target_native_id=f"{AP_MAC}:ng",
        description="malicious set",
        risk=RiskLevel.LOW,
        method="PUT",
        endpoint=endpoint,
        payload={"radio_table": [{"radio": "ng", "min_rssi_enabled": True, "min_rssi": -70}]},
        precondition=Precondition(target_native_id=f"{AP_MAC}:ng", expected={}),
        before={
            "method": "PUT",
            "endpoint": endpoint,
            "body": {"radio_table": [{"radio": "ng", "min_rssi_enabled": False}]},
        },
        after={"method": "PUT", "endpoint": endpoint, "body": {}},
    )
    return FixPlan(
        detector_key="wifi.min_rssi_misconfig",
        entity_native_id=f"{AP_MAC}:ng",
        title="forged",
        steps=[step],
    )


async def test_min_rssi_set_is_refused_even_when_forged(store):
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    plan = _min_rssi_set_plan()
    with pytest.raises(SafetyViolation):
        await applier.apply(plan, dry_run=False, confirm_token=plan_confirm_token(plan))
    assert writer.call_count == 0


async def test_min_rssi_tightening_is_refused(store):
    endpoint = f"rest/device/{AP_ID}"
    step = FixStep(
        action=ActionType.MIN_RSSI_REMOVE,
        target_entity_type=EntityType.RADIO,
        target_native_id=f"{AP_MAC}:ng",
        description="tighten",
        risk=RiskLevel.LOW,
        method="PUT",
        endpoint=endpoint,
        payload={"radio_table": [{"radio": "ng", "min_rssi_enabled": True, "min_rssi": -60}]},
        precondition=Precondition(target_native_id=f"{AP_MAC}:ng", expected={}),
        before={
            "method": "PUT",
            "endpoint": endpoint,
            "body": {"radio_table": [{"radio": "ng", "min_rssi_enabled": True, "min_rssi": -75}]},
        },
        after={},
    )
    plan = FixPlan("wifi.min_rssi_misconfig", f"{AP_MAC}:ng", "tighten", steps=[step])
    applier = Applier(store, FakeControllerWriter())
    with pytest.raises(SafetyViolation):
        await applier.apply(plan, dry_run=False, confirm_token=plan_confirm_token(plan))


async def test_apply_refuses_change_touching_non_revertible_field(store):
    """Verifier round 4: the revert restores only radio_table. A payload that ALSO
    changes another top-level field (here ``disabled`` False->True) has no complete
    inverse -- the revert would channel-restore but silently leave ``disabled`` set
    and still mark the change reverted. Refuse it up front, with zero dispatch."""
    endpoint = f"rest/device/{AP_ID}"
    step = FixStep(
        action=ActionType.CHANNEL_CHANGE,
        target_entity_type=EntityType.RADIO,
        target_native_id=f"{AP_MAC}:ng",
        description="channel + disabled",
        risk=RiskLevel.LOW,
        method="PUT",
        endpoint=endpoint,
        # channel 3 -> 1 (revertible) BUT also disabled False -> True (not restorable)
        payload={"radio_table": [{"radio": "ng", "channel": 1}], "disabled": True},
        precondition=Precondition(target_native_id=f"{AP_MAC}:ng", expected={}),
        before={
            "method": "PUT",
            "endpoint": endpoint,
            "body": {"radio_table": [{"radio": "ng", "channel": 3}], "disabled": False},
        },
        after={"method": "PUT", "endpoint": endpoint, "body": {}},
    )
    plan = FixPlan("wifi.channel_plan", f"{AP_MAC}:ng", "mixed", steps=[step])
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    with pytest.raises(SafetyViolation):
        await applier.apply(plan, dry_run=False, confirm_token=plan_confirm_token(plan))
    assert writer.call_count == 0


async def test_min_rssi_removal_is_advisory_not_executed(store, ap_device):
    # The genuine removal is surfaced as an advisory recommendation, never applied
    # as an irreversible write (re-enabling min-RSSI is barred, so it has no revert).
    finding = make_finding(
        "wifi.min_rssi_misconfig",
        radio_entity("ng"),
        evidence={"reason": "mesh_uplink_ap", "on_mesh_ap": True},
    )
    plan = plan_fix(finding, device=ap_device)
    assert plan.is_advisory
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    result = await applier.apply(plan, dry_run=False, confirm_token=plan_confirm_token(plan))
    assert result.applied is False
    assert result.aborted_reason == "manual_action_required"
    assert writer.call_count == 0


# --------------------------------------------------------------------------- #
# Advisory plans are a no-op apply
# --------------------------------------------------------------------------- #
async def test_apply_of_advisory_plan_is_noop(store):
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    plan = plan_fix(make_finding("wired.bad_cable", radio_entity("ng")))
    result = await applier.apply(plan, dry_run=False, confirm_token=plan_confirm_token(plan))
    assert result.applied is False
    assert result.aborted_reason == "manual_action_required"
    assert writer.call_count == 0
    assert store.list_changes() == []


# --------------------------------------------------------------------------- #
# auto-channel radios go through the REAL gate
# --------------------------------------------------------------------------- #
def _auto_channel_plan(issue_id=None):
    device = make_ap_device(radios=[{"radio": "ng", "channel": "auto", "ht": 20}])
    finding = make_finding(
        "wifi.channel_plan",
        radio_entity("ng"),
        dims={"subtype": "channel_off_grid", "band": "2.4"},
        evidence={"subtype": "channel_off_grid", "band": "2.4", "channel": 3},
    )
    return plan_fix(finding, device=device, issue_id=issue_id), device


async def test_auto_channel_plan_applies_through_the_real_gate(store):
    """The shipped regression, end to end: an unchanged auto radio must APPLY.

    Before the fix this exact call aborted with PreconditionDrift on every auto
    radio (UniFi's factory default): the precondition asserted the operating
    int from evidence against a live config that says "auto", forever.
    """
    from netadmin.fixes.service import _extract_target_attrs

    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    plan, device = _auto_channel_plan()
    step = plan.steps[0]
    live = _extract_target_attrs(
        device, step.precondition.target_native_id, step.precondition.expected
    )
    result = await applier.apply(
        plan,
        dry_run=False,
        confirm_token=plan_confirm_token(plan),
        current_state={step.precondition.target_native_id: live},
    )
    assert result.applied is True
    assert writer.call_count == 1


async def test_auto_channel_plan_pinned_meanwhile_still_aborts(store):
    """The guard keeps guarding: pin the radio between render and apply."""
    from netadmin.fixes.service import _extract_target_attrs

    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    plan, _device = _auto_channel_plan()
    step = plan.steps[0]
    pinned = make_ap_device(radios=[{"radio": "ng", "channel": 6, "ht": 20}])
    live = _extract_target_attrs(
        pinned, step.precondition.target_native_id, step.precondition.expected
    )
    with pytest.raises(PreconditionDrift):
        await applier.apply(
            plan,
            dry_run=False,
            confirm_token=plan_confirm_token(plan),
            current_state={step.precondition.target_native_id: live},
        )
    assert writer.call_count == 0


async def test_vanished_radio_is_drift_even_when_expected_would_be_none(store):
    """The empty extract of a gone radio must never satisfy any precondition.

    Belt-and-braces with the planner's refusal to build a {"channel": None}
    precondition: even a hand-forged plan carrying one aborts, because a key
    the live read could not produce is drift, not a None == None match.
    """
    from netadmin.fixes.service import _extract_target_attrs

    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    plan, _device = _auto_channel_plan()
    step = plan.steps[0]
    step.precondition.expected = {"channel": None}  # forge the hole
    vanished = make_ap_device(radios=[{"radio": "na", "channel": 36, "ht": 40}])
    live = _extract_target_attrs(
        vanished, step.precondition.target_native_id, step.precondition.expected
    )
    with pytest.raises(PreconditionDrift):
        await applier.apply(
            plan,
            dry_run=False,
            confirm_token=plan_confirm_token(plan),
            current_state={step.precondition.target_native_id: live},
        )
    assert writer.call_count == 0


async def test_auto_channel_revert_body_still_says_auto(store):
    """What makes the fix revertible: the before-state carries "auto" verbatim."""
    plan, _device = _auto_channel_plan()
    step = plan.steps[0]
    radios = {r["radio"]: r for r in step.before["body"]["radio_table"]}
    assert radios["ng"]["channel"] == "auto"


# --------------------------------------------------------------------------- #
# S2: the applier enforces revertibility itself; it never trusts the flag
# --------------------------------------------------------------------------- #
def _forged_step(*, before, payload, revertible, endpoint=f"rest/device/{AP_ID}"):
    return FixStep(
        action=ActionType.CHANNEL_CHANGE,
        target_entity_type=EntityType.RADIO,
        target_native_id=f"{AP_MAC}:ng",
        description="forged step",
        risk=RiskLevel.LOW,
        method="PUT",
        endpoint=endpoint,
        payload=payload,
        precondition=Precondition(target_native_id=f"{AP_MAC}:ng", expected={}),
        before=before,
        after={"method": "PUT", "endpoint": endpoint, "body": payload},
        revertible=revertible,
    )


async def test_applier_refuses_a_transient_step_even_when_flag_lies(store):
    # A step with no restorable before-state (a transient command) that LIES with
    # revertible=True must still be refused by the applier -- it does not trust the
    # planner's flag. Nothing is sent.
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    step = _forged_step(
        before=None, payload={"radio_table": [{"radio": "ng", "channel": 1}]}, revertible=True
    )
    plan = FixPlan("wired.port_flapping", f"{AP_MAC}:ng", "forged", steps=[step])
    with pytest.raises(SafetyViolation):
        await applier.apply(
            plan,
            dry_run=False,
            confirm_token=plan_confirm_token(plan),
            current_state={f"{AP_MAC}:ng": {"channel": 1}},
        )
    assert writer.call_count == 0
    assert store.list_changes() == []


async def test_applier_refuses_a_min_rssi_removal_step_it_cannot_revert(store):
    # A min-RSSI *removal* step whose before re-enables min-RSSI has no genuine
    # revert (the rail would refuse it), so the applier refuses to apply it -- even
    # though it passes the forward min-RSSI rail and claims revertible=True.
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    endpoint = f"rest/device/{AP_ID}"
    step = _forged_step(
        before={
            "method": "PUT",
            "endpoint": endpoint,
            "body": {"radio_table": [{"radio": "ng", "min_rssi_enabled": True, "min_rssi": -75}]},
        },
        payload={"radio_table": [{"radio": "ng", "min_rssi_enabled": False, "min_rssi": -75}]},
        revertible=True,
    )
    plan = FixPlan("wifi.min_rssi_misconfig", f"{AP_MAC}:ng", "forged", steps=[step])
    with pytest.raises(SafetyViolation):
        await applier.apply(
            plan,
            dry_run=False,
            confirm_token=plan_confirm_token(plan),
            current_state={f"{AP_MAC}:ng": {"min_rssi_enabled": False}},
        )
    assert writer.call_count == 0


async def test_empty_precondition_with_missing_snapshot_is_drift(store):
    # An empty precondition is "satisfied" only when the target was actually read.
    # A step whose target is absent from the fresh snapshot must NOT sail through on
    # that emptiness -- it is drift, and nothing is sent.
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    endpoint = f"rest/device/{AP_ID}"
    step = _forged_step(
        before={
            "method": "PUT",
            "endpoint": endpoint,
            "body": {"radio_table": [{"radio": "ng", "channel": 3}]},
        },
        payload={"radio_table": [{"radio": "ng", "channel": 1}]},
        revertible=True,
    )
    plan = FixPlan("wifi.channel_plan", f"{AP_MAC}:ng", "forged", steps=[step])
    with pytest.raises(PreconditionDrift):
        await applier.apply(
            plan,
            dry_run=False,
            confirm_token=plan_confirm_token(plan),
            current_state={},  # snapshot missing for the target
        )
    assert writer.call_count == 0
    assert store.list_changes() == []


# --------------------------------------------------------------------------- #
# C1: a fresh revert restores only touched fields and refuses on conflicting drift
# --------------------------------------------------------------------------- #
async def test_revert_preserves_unrelated_later_changes(store, ap_device):
    # The original change touched only the channel. Since then, someone lowered the
    # tx-power. The revert must restore the channel WITHOUT clobbering that later,
    # unrelated power change.
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    plan = _channel_plan(ap_device)  # ng channel 3 -> 1
    result = await applier.apply(
        plan, dry_run=False, confirm_token=plan_confirm_token(plan), current_state=_state_ok()
    )
    change_id = result.change_ids[0]

    # Live now: channel is still the applied value (1), but power was lowered later.
    live = {
        "ng": {
            "radio": "ng",
            "channel": 1,
            "tx_power_mode": "low",  # unrelated later change
            "min_rssi_enabled": True,
            "min_rssi": -75,
        },
        "na": {"radio": "na", "channel": 36, "min_rssi_enabled": False, "min_rssi": 0},
    }
    revert = await applier.revert(change_id, current_radios=live)
    assert revert.ok

    sent_ng = next(r for r in writer.calls[-1].body["radio_table"] if r["radio"] == "ng")
    assert sent_ng["channel"] == 3  # restored
    assert sent_ng["tx_power_mode"] == "low"  # NOT clobbered back to the old "high"


async def test_revert_refuses_when_a_touched_field_conflicts(store, ap_device):
    # The very field the change set (channel) has since drifted to a third value:
    # reverting would silently overwrite that newer work, so refuse and require
    # re-approval. Nothing is sent.
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    plan = _channel_plan(ap_device)  # ng channel 3 -> 1
    result = await applier.apply(
        plan, dry_run=False, confirm_token=plan_confirm_token(plan), current_state=_state_ok()
    )
    change_id = result.change_ids[0]
    calls_after_apply = writer.call_count

    live = {  # someone moved the channel to 11 since the fix
        "ng": {"radio": "ng", "channel": 11, "tx_power_mode": "high"},
        "na": {"radio": "na", "channel": 36},
    }
    with pytest.raises(PreconditionDrift):
        await applier.revert(change_id, current_radios=live)
    assert writer.call_count == calls_after_apply  # revert sent nothing
    assert store.get_change(change_id)["status"] != "reverted"


async def test_apply_and_revert_are_serialized_per_device(store):
    # Two reverts on the same device must not interleave their writes: the per-
    # device lock serializes them into [start, end, start, end], never
    # [start, start, end, end].
    import asyncio as _asyncio

    from netadmin.fixes.models import WriteResult

    class _OrderedWriter:
        def __init__(self) -> None:
            self.events: list[str] = []

        async def put(self, endpoint, body):
            self.events.append("start")
            await _asyncio.sleep(0)
            await _asyncio.sleep(0)
            self.events.append("end")
            return WriteResult(ok=True, status_code=200, data={})

        async def post(self, endpoint, body):  # pragma: no cover - unused
            return await self.put(endpoint, body)

    endpoint = f"rest/device/{AP_ID}"

    def _channel_change_row():
        before = {
            "method": "PUT",
            "endpoint": endpoint,
            "body": {"radio_table": [{"radio": "ng", "channel": 3}]},
        }
        after = {
            "method": "PUT",
            "endpoint": endpoint,
            "body": {"radio_table": [{"radio": "ng", "channel": 1}]},
        }
        return store.insert_change(
            action="wifi.channel_change", before=before, after=after, status="applied", ts=1
        )

    id_a, id_b = _channel_change_row(), _channel_change_row()
    writer = _OrderedWriter()
    applier = Applier(store, writer)
    live = {"ng": {"radio": "ng", "channel": 1}}

    await _asyncio.gather(
        applier.revert(id_a, current_radios=live),
        applier.revert(id_b, current_radios=live),
    )

    assert writer.events == ["start", "end", "start", "end"]


async def test_concurrent_reverts_read_committed_state_no_stale_overwrite(store):
    # C1: two reverts on the SAME device, each rolling back a DIFFERENT field. The
    # read-modify-write is atomic under the shared per-device lock -- the fresh live
    # state is read INSIDE the lock (via state_reader), so the second revert reads
    # the first's committed write instead of a snapshot taken before either. With a
    # stale snapshot the second silently undoes the first; here BOTH survive.
    import asyncio as _asyncio

    from netadmin.fixes.models import WriteResult

    endpoint = f"rest/device/{AP_ID}"
    # Shared mutable "live" device state that both reverts read and write through.
    live = {"ng": {"radio": "ng", "channel": 1, "tx_power_mode": "low"}}

    class _LiveWriter:
        async def put(self, ep, body):
            await _asyncio.sleep(0)  # a chance to interleave, if the lock let it
            for r in body["radio_table"]:
                live[r["radio"]] = dict(r)
            return WriteResult(ok=True, status_code=200, data={"meta": {"rc": "ok"}})

        async def post(self, ep, body):  # pragma: no cover - unused
            return await self.put(ep, body)

    # Change A restored the CHANNEL (before ch3 -> after ch1); B restored TX-POWER
    # (before high -> after low). Their touched fields are disjoint.
    id_a = store.insert_change(
        action="wifi.channel_change",
        before={
            "method": "PUT",
            "endpoint": endpoint,
            "body": {"radio_table": [{"radio": "ng", "channel": 3, "tx_power_mode": "low"}]},
        },
        after={
            "method": "PUT",
            "endpoint": endpoint,
            "body": {"radio_table": [{"radio": "ng", "channel": 1, "tx_power_mode": "low"}]},
        },
        status="applied",
        ts=1,
    )
    id_b = store.insert_change(
        action="wifi.tx_power_step_down",
        before={
            "method": "PUT",
            "endpoint": endpoint,
            "body": {"radio_table": [{"radio": "ng", "channel": 1, "tx_power_mode": "high"}]},
        },
        after={
            "method": "PUT",
            "endpoint": endpoint,
            "body": {"radio_table": [{"radio": "ng", "channel": 1, "tx_power_mode": "low"}]},
        },
        status="applied",
        ts=1,
    )

    applier = Applier(store, _LiveWriter())

    async def _reader():
        # Fresh read of live state; the applier calls this INSIDE the device lock.
        return {k: dict(v) for k, v in live.items()}, False

    await _asyncio.gather(
        applier.revert(id_a, state_reader=_reader),
        applier.revert(id_b, state_reader=_reader),
    )

    # A restored channel -> 3, B restored tx-power -> high. A stale second read would
    # have written the OTHER field back to its pre-revert value, undoing the first.
    assert live["ng"]["channel"] == 3
    assert live["ng"]["tx_power_mode"] == "high"
    assert store.get_change(id_a)["status"] == "reverted"
    assert store.get_change(id_b)["status"] == "reverted"


async def test_serialize_releases_acquired_locks_on_cancellation_mid_acquire(store):
    # NEW-BUG: cancelling a task while it acquires a SECOND device lock must release
    # the FIRST lock it already holds. Acquisition is inside try/finally, so no lock
    # leaks -- otherwise every later operation on that device would hang forever.
    import asyncio as _asyncio

    applier = Applier(store, FakeControllerWriter())
    lock_b = applier._lock_for("b")  # pre-hold "b" so _serialize parks acquiring it
    await lock_b.acquire()

    async def _op():
        async with applier._serialize(["a", "b"]):  # takes "a", then blocks on "b"
            pass  # pragma: no cover - never reached (cancelled while parked on "b")

    task = _asyncio.create_task(_op())
    await _asyncio.sleep(0)
    await _asyncio.sleep(0)  # let it grab "a" and park on "b"
    task.cancel()
    with pytest.raises(_asyncio.CancelledError):
        await task

    # "a" was released despite the cancellation mid-acquire (the process-wide lock is
    # free for the next operation, not leaked).
    assert not applier._lock_for("a").locked()
    lock_b.release()


# --------------------------------------------------------------------------- #
# C2: an ambiguous mutation outcome is reported as "unknown", never "failed"
# --------------------------------------------------------------------------- #
async def test_apply_ambiguous_write_reports_unknown_not_failed(store, ap_device):
    from netadmin.fixes.models import WriteResult

    ambiguous = WriteResult(
        ok=False, status_code=None, data={"ambiguous": True, "error": "outcome unknown; not retried"}
    )
    writer = FakeControllerWriter(response=ambiguous)
    applier = Applier(store, writer)
    plan = _channel_plan(ap_device)
    result = await applier.apply(
        plan, dry_run=False, confirm_token=plan_confirm_token(plan), current_state=_state_ok()
    )
    assert result.applied is False
    # The step is "unknown" (ambiguous), NOT collapsed into generic "failed".
    assert result.steps[0].status == "unknown"
    assert "unknown" in (result.steps[0].error or "")
    # The ledger row is "unknown" too -- the change may be live and must not read as
    # a clean failure the operator can ignore.
    assert store.list_changes()[0]["status"] == "unknown"


# --------------------------------------------------------------------------- #
# S2: the revertibility gate dry-runs the REAL reverse through the same rails
# --------------------------------------------------------------------------- #
async def test_channel_fix_on_mesh_min_rssi_ap_is_refused_up_front(store, ap_device):
    # ap_device's ng radio has min-RSSI enabled. On an AP that is a mesh uplink, the
    # revert's min-RSSI rail would refuse to restore (mesh min-RSSI is removal-only),
    # so a fix whose revert would later be barred is refused UP FRONT (S2) -- never
    # applied-then-unrevertable. The SAME plan applies fine when the AP is not mesh.
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    plan = _channel_plan(ap_device)  # ng channel 3 -> 1, min-RSSI untouched (stays on)
    with pytest.raises(SafetyViolation):
        await applier.apply(
            plan,
            dry_run=False,
            confirm_token=plan_confirm_token(plan),
            current_state=_state_ok(),
            mesh_uplinks={AP_ID},
        )
    assert writer.call_count == 0
    assert store.list_changes() == []


async def test_apply_refuses_step_whose_reverse_fails_min_rssi_rail(store):
    # A forged step whose BEFORE would re-enable min-RSSI (off -> on) on revert. The
    # reverse dry-run fails the min-RSSI rail, so the apply is refused at apply time.
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    endpoint = f"rest/device/{AP_ID}"
    step = _forged_step(
        before={
            "method": "PUT",
            "endpoint": endpoint,
            "body": {
                "radio_table": [{"radio": "ng", "channel": 3, "min_rssi_enabled": True, "min_rssi": -70}]
            },
        },
        payload={"radio_table": [{"radio": "ng", "channel": 1, "min_rssi_enabled": False, "min_rssi": -70}]},
        revertible=True,
    )
    plan = FixPlan("wifi.channel_plan", f"{AP_MAC}:ng", "forged", steps=[step])
    with pytest.raises(SafetyViolation):
        await applier.apply(
            plan,
            dry_run=False,
            confirm_token=plan_confirm_token(plan),
            current_state={f"{AP_MAC}:ng": {"channel": 1}},
        )
    assert writer.call_count == 0


async def test_apply_refuses_step_with_irrelevant_nonempty_before_body(store):
    # A transient command dressed up with a nonempty-but-IRRELEVANT before-body (a
    # POST to cmd/devmgr) restores no prior config, so it is not genuinely
    # revertible. The gate refuses it despite the full-looking before-state (S2).
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    step = _forged_step(
        before={
            "method": "POST",
            "endpoint": "cmd/devmgr",
            "body": {"cmd": "power-cycle", "mac": AP_MAC},
        },
        payload={"cmd": "power-cycle", "mac": AP_MAC},
        revertible=True,
        endpoint="cmd/devmgr",
    )
    plan = FixPlan("wired.port_flapping", f"{AP_MAC}:ng", "forged", steps=[step])
    with pytest.raises(SafetyViolation):
        await applier.apply(
            plan,
            dry_run=False,
            confirm_token=plan_confirm_token(plan),
            current_state={f"{AP_MAC}:ng": {}},
        )
    assert writer.call_count == 0


async def test_apply_refuses_transient_dispatch_with_unrelated_valid_before(store):
    # S2 (residual): a step that DISPATCHES a transient POST cmd/devmgr power-cycle
    # but carries an unrelated, perfectly well-formed radio-restore before-body must
    # be refused UP FRONT. The dispatched op has no radio_table to invert, so its
    # derived reverse comes out empty -- and an empty reverse trivially passes every
    # downstream rail. A gate that trusted the before-body's shape alone would let
    # this one-way command through with applied=True and a single dispatch (the
    # verifier's exact repro). Revertibility must be tied to the DISPATCHED
    # endpoint+method+payload, not to "some valid before-body exists".
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    endpoint = f"rest/device/{AP_ID}"
    step = FixStep(
        action=ActionType.CHANNEL_CHANGE,
        target_entity_type=EntityType.RADIO,
        target_native_id=f"{AP_MAC}:ng",
        description="transient dispatch, unrelated valid before",
        risk=RiskLevel.LOW,
        method="POST",
        endpoint="cmd/devmgr",
        payload={"cmd": "power-cycle", "mac": AP_MAC},
        precondition=Precondition(target_native_id=f"{AP_MAC}:ng", expected={}),
        # A valid radio-config PUT before-body -- but it inverts a DIFFERENT
        # operation than the cmd/devmgr power-cycle the step actually dispatches.
        before={
            "method": "PUT",
            "endpoint": endpoint,
            "body": {"radio_table": [{"radio": "ng", "channel": "auto"}]},
        },
        after={"method": "POST", "endpoint": "cmd/devmgr", "body": {"cmd": "power-cycle"}},
        revertible=True,
    )
    plan = FixPlan("wired.port_flapping", f"{AP_MAC}:ng", "forged", steps=[step])
    with pytest.raises(SafetyViolation):
        await applier.apply(
            plan,
            dry_run=False,
            confirm_token=plan_confirm_token(plan),
            current_state={f"{AP_MAC}:ng": {}},
        )
    # Refused before any dispatch: nothing sent, no ledger row.
    assert writer.call_count == 0
    assert store.list_changes() == []


# --------------------------------------------------------------------------- #
# S2 (round 3): the inverse is derived from the DISPATCHED change, never from a
# trusted step.after. A forged after==before cannot make the revert a no-op.
# --------------------------------------------------------------------------- #
async def test_apply_refuses_noop_step_whose_after_equals_before(store):
    # A step that dispatches nothing its before-state does not already hold (payload
    # channel == before channel), with after also set == before, is a NO-OP apply:
    # there is nothing to revert, and a "revert" would merely re-send the current
    # value. The gate derives the inverse from the DISPATCHED payload vs before and
    # refuses a no-op as non-revertible (S2 r3). Nothing is sent.
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    endpoint = f"rest/device/{AP_ID}"
    before = {
        "method": "PUT",
        "endpoint": endpoint,
        "body": {"radio_table": [{"radio": "ng", "channel": 1}]},
    }
    step = FixStep(
        action=ActionType.CHANNEL_CHANGE,
        target_entity_type=EntityType.RADIO,
        target_native_id=f"{AP_MAC}:ng",
        description="no-op apply, after forged == before",
        risk=RiskLevel.LOW,
        method="PUT",
        endpoint=endpoint,
        payload={"radio_table": [{"radio": "ng", "channel": 1}]},  # == before: changes nothing
        precondition=Precondition(target_native_id=f"{AP_MAC}:ng", expected={}),
        before=before,
        after=before,  # forged: after set equal to before
        revertible=True,
    )
    plan = FixPlan("wifi.channel_plan", f"{AP_MAC}:ng", "forged", steps=[step])
    with pytest.raises(SafetyViolation):
        await applier.apply(
            plan,
            dry_run=False,
            confirm_token=plan_confirm_token(plan),
            current_state={f"{AP_MAC}:ng": {"channel": 1}},
        )
    assert writer.call_count == 0
    assert store.list_changes() == []


async def test_forged_after_equals_before_still_reverts_to_the_original(store):
    # The verifier's round-3 repro: ``after`` is set == ``before`` to DISGUISE a real
    # channel 3 -> 1 change. If the inverse trusted ``after``, the derived reverse
    # would touch nothing and the "revert" would be a no-op that re-sends 1 (the
    # already-applied value), never restoring 3. The gate + the recorded ledger +
    # revert now all derive the inverse from the DISPATCHED payload vs before, so the
    # step is genuinely revertible and its revert actually restores 3.
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    endpoint = f"rest/device/{AP_ID}"
    before = {
        "method": "PUT",
        "endpoint": endpoint,
        "body": {"radio_table": [{"radio": "ng", "channel": 3}]},
    }
    step = FixStep(
        action=ActionType.CHANNEL_CHANGE,
        target_entity_type=EntityType.RADIO,
        target_native_id=f"{AP_MAC}:ng",
        description="real 3->1, after forged == before",
        risk=RiskLevel.MEDIUM,
        method="PUT",
        endpoint=endpoint,
        payload={"radio_table": [{"radio": "ng", "channel": 1}]},  # real change: 3 -> 1
        precondition=Precondition(target_native_id=f"{AP_MAC}:ng", expected={"channel": 3}),
        before=before,
        after=before,  # forged: after == before, would make a trusted reverse a no-op
        revertible=True,
    )
    plan = FixPlan("wifi.channel_plan", f"{AP_MAC}:ng", "forged", steps=[step])
    result = await applier.apply(
        plan,
        dry_run=False,
        confirm_token=plan_confirm_token(plan),
        current_state={f"{AP_MAC}:ng": {"channel": 3}},
    )
    assert result.applied is True
    change_id = result.change_ids[0]

    # The ledger recorded the ACTUAL dispatched after (channel 1), not the forged one.
    after = json.loads(store.get_change(change_id)["after_json"])
    assert after["body"]["radio_table"][0]["channel"] == 1

    # Revert restores the ORIGINAL channel 3 (derived from before, not trusted after).
    live = {"ng": {"radio": "ng", "channel": 1}}
    revert = await applier.revert(change_id, current_radios=live)
    assert revert.ok
    last = writer.calls[-1]
    assert next(r for r in last.body["radio_table"] if r["radio"] == "ng")["channel"] == 3


async def test_legit_power_round_trip_applies_and_reverts(store):
    # A legitimate tx-power step-down (high -> medium) must remain revertible and its
    # revert must restore high -- the fix must not over-refuse genuine round trips.
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    endpoint = f"rest/device/{AP_ID}"
    step = FixStep(
        action=ActionType.TX_POWER_STEP_DOWN,
        target_entity_type=EntityType.RADIO,
        target_native_id=f"{AP_MAC}:ng",
        description="step power high -> medium",
        risk=RiskLevel.LOW,
        method="PUT",
        endpoint=endpoint,
        payload={"radio_table": [{"radio": "ng", "tx_power_mode": "medium"}]},
        precondition=Precondition(
            target_native_id=f"{AP_MAC}:ng", expected={"tx_power_mode": "high"}
        ),
        before={
            "method": "PUT",
            "endpoint": endpoint,
            "body": {"radio_table": [{"radio": "ng", "tx_power_mode": "high"}]},
        },
        after={
            "method": "PUT",
            "endpoint": endpoint,
            "body": {"radio_table": [{"radio": "ng", "tx_power_mode": "medium"}]},
        },
        revertible=True,
    )
    plan = FixPlan("wifi.tx_power_loud", f"{AP_MAC}:ng", "legit", steps=[step])
    result = await applier.apply(
        plan,
        dry_run=False,
        confirm_token=plan_confirm_token(plan),
        current_state={f"{AP_MAC}:ng": {"tx_power_mode": "high"}},
    )
    assert result.applied is True
    change_id = result.change_ids[0]

    live = {"ng": {"radio": "ng", "tx_power_mode": "medium"}}
    revert = await applier.revert(change_id, current_radios=live)
    assert revert.ok
    last = writer.calls[-1]
    assert next(r for r in last.body["radio_table"] if r["radio"] == "ng")["tx_power_mode"] == "high"


# --------------------------------------------------------------------------- #
# P3: the process-wide device-lock registry must not retain closed event loops
# --------------------------------------------------------------------------- #
async def test_closed_event_loops_are_not_retained_by_the_lock_registry(store):
    # A stored asyncio.Lock, once used, holds a strong reference to the loop that
    # ran it. Leaving its registry entry in place after that loop closes pins the
    # dead loop forever -- one leaked loop per finished loop. The registry must drop
    # the lock entries of closed/collected loops so the loops become collectable,
    # while two live appliers on the same device+loop still share one lock.
    from netadmin.fixes import applier as applier_mod

    applier = Applier(store, FakeControllerWriter())

    def _run_on_fresh_loop() -> "weakref.ref":
        loop = asyncio.new_event_loop()
        ref = weakref.ref(loop)

        async def _use_lock() -> None:
            async with applier._serialize(["dev-loop-leak"]):
                pass

        try:
            loop.run_until_complete(_use_lock())
        finally:
            loop.close()
        return ref

    refs: list["weakref.ref"] = []
    for _ in range(12):
        box: dict[str, "weakref.ref"] = {}

        def _worker() -> None:
            box["ref"] = _run_on_fresh_loop()

        # Run each child loop in its own thread so this async test's own loop is
        # never nested; each thread's loop gets a distinct id in the registry.
        th = threading.Thread(target=_worker)
        th.start()
        th.join()
        refs.append(box["ref"])

    # One more op on a fresh loop, to trigger the prune of the last straggler
    # (pruning runs on the NEXT lock lookup after a loop closes).
    th = threading.Thread(target=lambda: _run_on_fresh_loop())
    th.start()
    th.join()

    gc.collect()

    # Every one of the 12 closed loops must have been collected -- none retained by
    # the registry. Before the fix, each loop's lock lingers and pins its loop, so
    # all 12 weakrefs stay alive.
    alive = [r for r in refs if r() is not None]
    assert alive == [], f"{len(alive)} closed event loop(s) still retained by the lock registry"

    # And the registry does not grow with the number of finished loops: pruning
    # bounds it to at most the current live loop's entry (here the single, just-run
    # straggler that the next lookup would itself prune) -- never one-per-loop.
    # Before the fix this device key accumulates one entry for every loop (13).
    entries = [k for k in applier_mod._PROCESS_DEVICE_LOCKS if k[1] == "dev-loop-leak"]
    assert len(entries) <= 1, f"lock registry accumulated {len(entries)} entries for finished loops"
