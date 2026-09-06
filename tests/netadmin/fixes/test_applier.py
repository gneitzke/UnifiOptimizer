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


# --------------------------------------------------------------------------- #
# #w13a-3: a REVERT must run the SAME device-wide uncertainty guard an apply does.
# A revert is itself a whole-radio_table PUT to rest/device/<id> (it replaces the
# ENTIRE device), so it must serialize behind resolution of EVERY uncertain change
# on that physical device -- not merely re-check its own row. Two real plans on one
# AP: apply ng channel (ok), then na power is ambiguous ('unknown'); reverting the
# ng change while the na 'unknown' still stands would dispatch a whole-table PUT and
# a delayed na PUT could undo the restore. Refuse until the sibling is resolved.
# --------------------------------------------------------------------------- #
async def test_revert_refused_while_sibling_uncertain_change_on_device(store, ap_device):
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    plan = _channel_plan(ap_device)
    result = await applier.apply(
        plan, dry_run=False, confirm_token=plan_confirm_token(plan), current_state=_state_ok()
    )
    ng_change_id = result.change_ids[0]
    assert store.get_change(ng_change_id)["status"] == "applied"

    # A SIBLING ambiguous ('unknown') change on the SAME physical device (a different
    # radio, na), exactly what an ambiguous na-power apply leaves behind. It records
    # its dispatch endpoint as rest/device/<id>, so the device-wide guard keys on it.
    na_change_id = store.insert_change(
        action="wifi.tx_power",
        before={
            "method": "PUT",
            "endpoint": f"rest/device/{AP_ID}",
            "body": {"radio_table": [{"radio": "na", "tx_power_mode": "high"}]},
        },
        after={
            "method": "PUT",
            "endpoint": f"rest/device/{AP_ID}",
            "body": {"radio_table": [{"radio": "na", "tx_power_mode": "low"}]},
        },
        status="unknown",
        ts=1,
    )

    live_radios = {
        "ng": {"radio": "ng", "channel": 3, "min_rssi_enabled": False, "min_rssi": 0},
        "na": {"radio": "na", "channel": 36, "min_rssi_enabled": False, "min_rssi": 0},
    }
    calls_before = writer.call_count
    # RED before the fix: the revert dispatched a whole-table PUT and marked 'reverted'
    # while the na change stayed 'unknown'. It must now REFUSE with zero dispatch.
    with pytest.raises(FixError, match="unresolved change"):
        await applier.revert(ng_change_id, current_radios=live_radios)
    assert writer.call_count == calls_before  # nothing dispatched
    assert store.get_change(ng_change_id)["status"] == "applied"  # not reverted

    # Once the operator reconciles the sibling to a terminal, resolved state, the
    # revert of the ng change is allowed and succeeds.
    store.update_change_status(na_change_id, "applied")
    revert = await applier.revert(ng_change_id, current_radios=live_radios)
    assert revert.ok
    assert store.get_change(ng_change_id)["status"] == "reverted"


async def test_lone_revert_not_blocked_by_its_own_state(store, ap_device):
    # A genuine lone revert (no sibling uncertain change on the device) still works:
    # the change's own in-flight/unknown state must never block its own revert.
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    plan = _channel_plan(ap_device)
    result = await applier.apply(
        plan, dry_run=False, confirm_token=plan_confirm_token(plan), current_state=_state_ok()
    )
    change_id = result.change_ids[0]
    live_radios = {"ng": {"radio": "ng", "channel": 3, "min_rssi_enabled": False, "min_rssi": 0}}
    revert = await applier.revert(change_id, current_radios=live_radios)
    assert revert.ok
    assert store.get_change(change_id)["status"] == "reverted"


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


async def test_revert_of_change_with_non_radio_field_is_refused(store):
    """Verifier round 5 (defense in depth): a ledger row whose change modified a
    field beyond radio_table (here ``disabled``) has no complete inverse -- revert()
    restores only radio_table. It must REFUSE rather than perform a partial revert
    that leaves ``disabled`` changed while marking the row reverted. The apply-time
    gate blocks creating such a change now, but an older/out-of-band row must still
    be refused on the revert path itself."""
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    endpoint = f"rest/device/{AP_ID}"
    change_id = store.insert_change(
        action="wifi.channel_plan",
        before={
            "method": "PUT",
            "endpoint": endpoint,
            "body": {"radio_table": [{"radio": "ng", "channel": 3}], "disabled": False},
        },
        after={
            "method": "PUT",
            "endpoint": endpoint,
            "body": {"radio_table": [{"radio": "ng", "channel": 1}], "disabled": True},
        },
        status="applied",
        ts=1,
    )
    with pytest.raises(SafetyViolation):
        await applier.revert(change_id, current_radios={"ng": {"radio": "ng", "channel": 1}})
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


async def test_concurrent_applies_second_merges_onto_committed_no_stale_clobber(store):
    # P1 (apply race), merge-at-dispatch: two concurrent applies to the SAME device.
    # Apply A retunes the CHANNEL (3 -> 1); apply B steps DOWN tx-power. B's plan-time
    # payload -- a whole-table snapshot taken before A committed -- still carries the
    # STALE channel 3, so re-sending it would silently undo A. The fix builds B's PUT
    # body INSIDE the per-device lock by merging B's intended tx-power delta onto the
    # FRESH live table (which now carries A's committed channel 1). So B does NOT
    # re-send the stale channel: it dispatches {channel 1, tx medium} and BOTH changes
    # coexist -- A's channel survives and B's tx-power lands, both rows 'applied'. The
    # two appliers share the PROCESS-wide device lock, so B runs strictly after A.
    import asyncio as _asyncio

    from netadmin.fixes.applier import _endpoint_device
    from netadmin.fixes.models import WriteResult
    from netadmin.fixes.planner import plan_fix

    from .conftest import make_finding, radio_entity

    dev_key = _endpoint_device(f"rest/device/{AP_ID}")
    # Shared mutable live radio state: both applies read it (inside their lock) and
    # the writer commits payloads through it.
    live = {
        "ng": {"radio": "ng", "channel": 3, "tx_power_mode": "high",
               "min_rssi_enabled": True, "min_rssi": -75, "ht": 20},
        "na": {"radio": "na", "channel": 36, "tx_power_mode": "auto",
               "min_rssi_enabled": False, "min_rssi": 0, "ht": 80},
    }
    gate = _asyncio.Event()  # blocks the FIRST writer until the second is parked

    class _LiveWriter:
        def __init__(self) -> None:
            self.puts: list[dict] = []

        async def put(self, ep, body):
            self.puts.append(body)
            if not gate.is_set():
                await gate.wait()  # hold the in-flight write open
            for r in body["radio_table"]:
                live[r["radio"]] = dict(r)
            return WriteResult(ok=True, status_code=200, data={"meta": {}})

        async def post(self, ep, body):  # pragma: no cover - unused
            return await self.put(ep, body)

    def _state_reader():
        async def _r():
            full = {dev_key: {k: dict(v) for k, v in live.items()}}
            cur = {
                f"{AP_MAC}:ng": {
                    "channel": live["ng"]["channel"],
                    "tx_power_mode": live["ng"]["tx_power_mode"],
                }
            }
            return cur, full, set()

        return _r

    # Plan A: channel 3 -> 1 (built from the current device).
    plan_a = _channel_plan(make_ap_device())
    # Plan B: tx-power high -> medium; its payload is built from a snapshot that still
    # has channel 3 -- the stale field that would clobber A.
    finding_b = make_finding(
        "wifi.tx_power_loud",
        radio_entity("ng"),
        dims={"band": "2.4"},
        evidence={"band": "2.4", "tx_power_mode": "high"},
    )
    plan_b = plan_fix(finding_b, device=make_ap_device(), issue_id=None)
    assert next(r for r in plan_b.steps[0].payload["radio_table"] if r["radio"] == "ng")[
        "channel"
    ] == 3  # sanity: B's payload really does carry the stale channel

    writer_a, writer_b = _LiveWriter(), _LiveWriter()
    applier_a = Applier(store, writer_a)
    applier_b = Applier(store, writer_b)

    task_a = _asyncio.create_task(
        applier_a.apply(
            plan_a, dry_run=False, confirm_token=plan_confirm_token(plan_a),
            state_reader=_state_reader(),
        )
    )
    for _ in range(6):
        await _asyncio.sleep(0)  # let A take the lock and park in its blocked write
    task_b = _asyncio.create_task(
        applier_b.apply(
            plan_b, dry_run=False, confirm_token=plan_confirm_token(plan_b),
            state_reader=_state_reader(),
        )
    )
    for _ in range(6):
        await _asyncio.sleep(0)  # let B park on the (A-held) device lock
    gate.set()  # release A's write; A commits, releases the lock, then B proceeds

    res_a = await task_a
    res_b = await task_b  # B merges onto A's committed table rather than clobbering it

    assert res_a.applied is True
    assert res_b.applied is True
    assert writer_a.puts and writer_a.puts[0]["radio_table"]  # A dispatched once
    assert writer_b.puts  # B dispatched once too
    # B's dispatched body carried A's committed channel 1 (merge preserved it), NOT the
    # stale channel 3 from B's plan-time snapshot.
    b_ng = next(r for r in writer_b.puts[0]["radio_table"] if r["radio"] == "ng")
    assert b_ng["channel"] == 1
    assert b_ng["tx_power_mode"] == "medium"
    # Live state carries BOTH changes: A's channel and B's tx-power.
    assert live["ng"]["channel"] == 1
    assert live["ng"]["tx_power_mode"] == "medium"
    # Ledger is honest: both changes recorded, both 'applied'.
    changes = store.list_changes()
    assert len(changes) == 2
    assert [c["status"] for c in changes] == ["applied", "applied"]


async def test_concurrent_reverts_dispatch_the_mutation_exactly_once(store):
    # P2 (revert replay): two concurrent reverts of the SAME change. Both read status
    # 'applied' before the lock, so both would dispatch -- replaying the mutation. The
    # fix re-reads the row's status UNDER the per-device lock: once the first revert
    # commits 'reverted', the second re-reads that status and refuses with no dispatch.
    # Exactly ONE PUT, one 'reverted' row.
    import asyncio as _asyncio

    from netadmin.fixes.models import WriteResult

    endpoint = f"rest/device/{AP_ID}"
    change_id = store.insert_change(
        action="wifi.channel_change",
        before={"method": "PUT", "endpoint": endpoint,
                "body": {"radio_table": [{"radio": "ng", "channel": 3}]}},
        after={"method": "PUT", "endpoint": endpoint,
               "body": {"radio_table": [{"radio": "ng", "channel": 1}]}},
        status="applied",
        ts=1,
    )

    class _CountingWriter:
        def __init__(self) -> None:
            self.puts = 0

        async def put(self, ep, body):
            self.puts += 1
            await _asyncio.sleep(0)
            return WriteResult(ok=True, status_code=200, data={"meta": {}})

        async def post(self, ep, body):  # pragma: no cover - unused
            return await self.put(ep, body)

    writer = _CountingWriter()
    applier = Applier(store, writer)
    live = {"ng": {"radio": "ng", "channel": 1}}

    async def _reader():
        return {k: dict(v) for k, v in live.items()}, False

    results = await _asyncio.gather(
        applier.revert(change_id, state_reader=_reader),
        applier.revert(change_id, state_reader=_reader),
        return_exceptions=True,
    )

    oks = [r for r in results if not isinstance(r, Exception)]
    refused = [r for r in results if isinstance(r, FixError)]
    assert writer.puts == 1  # dispatched exactly once, not replayed
    assert len(oks) == 1 and oks[0].ok
    assert len(refused) == 1  # the second revert was refused with no dispatch
    assert "already reverted" in str(refused[0])
    assert store.get_change(change_id)["status"] == "reverted"


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


# --------------------------------------------------------------------------- #
# Merge-at-dispatch: the whole-radio_table PUT is built from FRESH live at write
# time, not a plan-time snapshot. Root-cause fix for #5 (multi-step self-clobber),
# #1 (untouched field removed / radio added), and #6 (deleted-radio revert).
# --------------------------------------------------------------------------- #
def _radio_pre(native_id, expected):
    return Precondition(target_native_id=native_id, expected=expected)


async def test_multistep_same_device_plan_does_not_self_clobber(store):
    # #5: a plan with TWO steps on the SAME 2-radio device -- step 1 moves ng's channel
    # (3 -> 1), step 2 moves na's channel (36 -> 40). Each step's plan-time payload is a
    # WHOLE-table snapshot: step 2's snapshot still carries ng at its ORIGINAL 3. Sent
    # verbatim, step 2's PUT re-sends ng=3 and UNDOES step 1 (both rows still 'applied',
    # ng ends at 3). Merge-at-dispatch builds step 2's body by applying only its na
    # delta onto the live table carried forward from step 1, so ng stays 1. Both
    # changes persist; neither clobbers the other.
    from netadmin.fixes.models import WriteResult

    endpoint = f"rest/device/{AP_ID}"
    dev_key = AP_ID
    live = {
        "ng": {"radio": "ng", "channel": 3, "tx_power_mode": "high", "ht": 20},
        "na": {"radio": "na", "channel": 36, "tx_power_mode": "auto", "ht": 80},
    }

    class _LiveWriter:
        def __init__(self):
            self.puts = []

        async def put(self, ep, body):
            self.puts.append(body)
            for r in body["radio_table"]:
                live[r["radio"]] = dict(r)
            return WriteResult(ok=True, status_code=200, data={"meta": {}})

        async def post(self, ep, body):  # pragma: no cover - unused
            return await self.put(ep, body)

    def _step(native, radio, old_ch, new_ch, full_snapshot):
        # full_snapshot is the plan-time whole table (with only THIS radio changed).
        before_table = [
            {"radio": "ng", "channel": 3, "tx_power_mode": "high", "ht": 20},
            {"radio": "na", "channel": 36, "tx_power_mode": "auto", "ht": 80},
        ]
        return FixStep(
            action=ActionType.CHANNEL_CHANGE,
            target_entity_type=EntityType.RADIO,
            target_native_id=native,
            description=f"{radio} {old_ch}->{new_ch}",
            risk=RiskLevel.MEDIUM,
            method="PUT",
            endpoint=endpoint,
            payload={"radio_table": full_snapshot},
            precondition=_radio_pre(native, {"channel": old_ch}),
            before={"method": "PUT", "endpoint": endpoint, "body": {"radio_table": before_table}},
            after={"method": "PUT", "endpoint": endpoint, "body": {"radio_table": full_snapshot}},
            revertible=True,
        )

    # Step 1 snapshot: ng->1, na still 36. Step 2 snapshot: na->40 but ng STILL 3.
    step1 = _step(
        f"{AP_MAC}:ng", "ng", 3, 1,
        [{"radio": "ng", "channel": 1, "tx_power_mode": "high", "ht": 20},
         {"radio": "na", "channel": 36, "tx_power_mode": "auto", "ht": 80}],
    )
    step2 = _step(
        f"{AP_MAC}:na", "na", 36, 40,
        [{"radio": "ng", "channel": 3, "tx_power_mode": "high", "ht": 20},
         {"radio": "na", "channel": 40, "tx_power_mode": "auto", "ht": 80}],
    )
    plan = FixPlan("wifi.channel_plan", f"{AP_MAC}:ng", "two-radio", steps=[step1, step2])

    writer = _LiveWriter()
    applier = Applier(store, writer)

    async def _reader():
        cur = {
            f"{AP_MAC}:ng": {"channel": live["ng"]["channel"]},
            f"{AP_MAC}:na": {"channel": live["na"]["channel"]},
        }
        full = {dev_key: {k: dict(v) for k, v in live.items()}}
        return cur, full, set()

    result = await applier.apply(
        plan, dry_run=False, confirm_token=plan_confirm_token(plan), state_reader=_reader
    )

    assert result.applied is True
    assert len(writer.puts) == 2
    # Step 1's ng move SURVIVES step 2 (merge carried it forward); na moved too.
    assert live["ng"]["channel"] == 1
    assert live["na"]["channel"] == 40
    # Step 2's dispatched body carried ng at 1 (merged), not the stale snapshot 3.
    step2_ng = next(r for r in writer.puts[1]["radio_table"] if r["radio"] == "ng")
    assert step2_ng["channel"] == 1
    assert [c["status"] for c in store.list_changes()] == ["applied", "applied"]


async def test_overlapping_same_field_plan_is_refused(store):
    # S2 verifier round 15: two steps in ONE plan touch the SAME device+radio+FIELD
    # (ng channel 3->1, then ng channel 1->6). Merge-at-dispatch carries step 1's write
    # forward, so step 2 would dispatch onto channel 1 -- but the ledger records each
    # step's OWN plan-time before (step 2's before is 3, its plan-time original, NOT the
    # effective preceding value 1). A per-step revert of step 2 would then restore 3 and
    # OVERSHOOT past step 1's still-'applied' change. Such an overlapping same-field plan
    # has an ill-defined per-step revert and no legitimate planner emits it, so the apply
    # is refused UP FRONT (SafetyViolation) with no writer call and no ledger row.
    from netadmin.fixes.models import WriteResult

    endpoint = f"rest/device/{AP_ID}"

    class _RecordingWriter:
        def __init__(self):
            self.puts = []

        async def put(self, ep, body):  # pragma: no cover - must never be reached
            self.puts.append(body)
            return WriteResult(ok=True, status_code=200, data={"meta": {}})

        async def post(self, ep, body):  # pragma: no cover - unused
            return await self.put(ep, body)

    # Faithful adversarial forge: BOTH steps carry the SAME plan-time before-state
    # (channel 3) and the SAME precondition (channel 3), so without the guard both pass
    # precondition/delta re-checks, both apply (step 2 merges channel 6 onto step 1's
    # carried channel 1), and BOTH store before=3 -- the exact state that makes a per-step
    # revert of step 2 restore 3 and overshoot step 1. The guard refuses the plan first.
    def _ng_step(desc, new_ch):
        before_table = [{"radio": "ng", "channel": 3, "ht": 20}]
        payload_table = [{"radio": "ng", "channel": new_ch, "ht": 20}]
        return FixStep(
            action=ActionType.CHANNEL_CHANGE,
            target_entity_type=EntityType.RADIO,
            target_native_id=f"{AP_MAC}:ng",
            description=desc,
            risk=RiskLevel.MEDIUM,
            method="PUT",
            endpoint=endpoint,
            payload={"radio_table": payload_table},
            precondition=_radio_pre(f"{AP_MAC}:ng", {"channel": 3}),
            before={"method": "PUT", "endpoint": endpoint, "body": {"radio_table": before_table}},
            after={"method": "PUT", "endpoint": endpoint, "body": {"radio_table": payload_table}},
            revertible=True,
        )

    # Both steps change ng.channel: an overlapping same-field plan (adversarially forged).
    step1 = _ng_step("ng 3->1", 1)
    step2 = _ng_step("ng 3->6", 6)
    plan = FixPlan("wifi.channel_plan", f"{AP_MAC}:ng", "overlap", steps=[step1, step2])

    writer = _RecordingWriter()
    applier = Applier(store, writer)

    async def _reader():
        cur = {f"{AP_MAC}:ng": {"channel": 3}}
        full = {AP_ID: {"ng": {"radio": "ng", "channel": 3, "ht": 20}}}
        return cur, full, set()

    with pytest.raises(SafetyViolation) as exc:
        await applier.apply(
            plan, dry_run=False, confirm_token=plan_confirm_token(plan), state_reader=_reader
        )
    assert "same-field" in str(exc.value) or "both change field" in str(exc.value)
    # Refused before any dispatch: no network call, no ledger row.
    assert writer.puts == []
    assert store.list_changes() == []


async def test_two_radio_plan_applies_and_each_step_reverts_without_overshoot(store):
    # The legitimate counterpart to the overlap refusal: a two-step plan on the SAME
    # device but DIFFERENT radios (ng channel 3->1, na channel 36->40) still applies via
    # carry-forward, and reverting EITHER step restores exactly THAT step's field to its
    # own before-value, leaving the other radio's change intact (no overshoot).
    from netadmin.fixes.models import WriteResult

    endpoint = f"rest/device/{AP_ID}"
    live = {
        "ng": {"radio": "ng", "channel": 3, "ht": 20},
        "na": {"radio": "na", "channel": 36, "ht": 80},
    }

    class _LiveWriter:
        def __init__(self):
            self.puts = []

        async def put(self, ep, body):
            self.puts.append(body)
            for r in body["radio_table"]:
                live[r["radio"]] = dict(r)
            return WriteResult(ok=True, status_code=200, data={"meta": {}})

        async def post(self, ep, body):  # pragma: no cover - unused
            return await self.put(ep, body)

    def _step(native, radio, old_ch, new_ch):
        before_table = [
            {"radio": "ng", "channel": 3, "ht": 20},
            {"radio": "na", "channel": 36, "ht": 80},
        ]
        payload_table = [dict(r) for r in before_table]
        for r in payload_table:
            if r["radio"] == radio:
                r["channel"] = new_ch
        return FixStep(
            action=ActionType.CHANNEL_CHANGE,
            target_entity_type=EntityType.RADIO,
            target_native_id=native,
            description=f"{radio} {old_ch}->{new_ch}",
            risk=RiskLevel.MEDIUM,
            method="PUT",
            endpoint=endpoint,
            payload={"radio_table": payload_table},
            precondition=_radio_pre(native, {"channel": old_ch}),
            before={"method": "PUT", "endpoint": endpoint, "body": {"radio_table": before_table}},
            after={"method": "PUT", "endpoint": endpoint, "body": {"radio_table": payload_table}},
            revertible=True,
        )

    step1 = _step(f"{AP_MAC}:ng", "ng", 3, 1)
    step2 = _step(f"{AP_MAC}:na", "na", 36, 40)
    plan = FixPlan("wifi.channel_plan", f"{AP_MAC}:ng", "two-radio", steps=[step1, step2])

    writer = _LiveWriter()
    applier = Applier(store, writer)

    async def _apply_reader():
        cur = {
            f"{AP_MAC}:ng": {"channel": live["ng"]["channel"]},
            f"{AP_MAC}:na": {"channel": live["na"]["channel"]},
        }
        full = {AP_ID: {k: dict(v) for k, v in live.items()}}
        return cur, full, set()

    result = await applier.apply(
        plan, dry_run=False, confirm_token=plan_confirm_token(plan), state_reader=_apply_reader
    )
    assert result.applied is True
    assert live["ng"]["channel"] == 1 and live["na"]["channel"] == 40
    ng_change, na_change = result.change_ids

    async def _revert_reader():
        return {k: dict(v) for k, v in live.items()}, False

    # Revert step 2 (na): restores na to 36 and leaves ng's change (1) untouched.
    await applier.revert(na_change, state_reader=_revert_reader)
    assert live["na"]["channel"] == 36  # restored to ITS own before
    assert live["ng"]["channel"] == 1   # ng's change did not overshoot

    # Revert step 1 (ng): restores ng to 3, na stays at its reverted 36.
    await applier.revert(ng_change, state_reader=_revert_reader)
    assert live["ng"]["channel"] == 3
    assert live["na"]["channel"] == 36
    assert sorted(c["status"] for c in store.list_changes()) == ["reverted", "reverted"]


async def test_untouched_field_removed_from_live_is_not_restored(store):
    # #1: a concurrent operator has REMOVED tx_power_mode from ng in live since the
    # plan was built. The plan-time payload still carries tx_power_mode=high (an
    # untouched field). Sent verbatim, the whole-table PUT would RESTORE that stale
    # value. Merge-at-dispatch builds the body from fresh live (which no longer has
    # tx_power_mode) plus only the channel delta, so the stale field is NOT re-added.
    from netadmin.fixes.models import WriteResult

    endpoint = f"rest/device/{AP_ID}"
    live = {"ng": {"radio": "ng", "channel": 3, "ht": 20}}  # tx_power_mode GONE in live

    class _LiveWriter:
        def __init__(self):
            self.puts = []

        async def put(self, ep, body):
            self.puts.append(body)
            return WriteResult(ok=True, status_code=200, data={"meta": {}})

        async def post(self, ep, body):  # pragma: no cover - unused
            return await self.put(ep, body)

    # Plan snapshot HAD tx_power_mode=high; only channel is the intended change.
    step = FixStep(
        action=ActionType.CHANNEL_CHANGE,
        target_entity_type=EntityType.RADIO,
        target_native_id=f"{AP_MAC}:ng",
        description="ng 3->1",
        risk=RiskLevel.MEDIUM,
        method="PUT",
        endpoint=endpoint,
        payload={"radio_table": [{"radio": "ng", "channel": 1, "tx_power_mode": "high", "ht": 20}]},
        precondition=_radio_pre(f"{AP_MAC}:ng", {"channel": 3}),
        before={"method": "PUT", "endpoint": endpoint,
                "body": {"radio_table": [{"radio": "ng", "channel": 3, "tx_power_mode": "high", "ht": 20}]}},
        after={"method": "PUT", "endpoint": endpoint,
               "body": {"radio_table": [{"radio": "ng", "channel": 1, "tx_power_mode": "high", "ht": 20}]}},
        revertible=True,
    )
    plan = FixPlan("wifi.channel_plan", f"{AP_MAC}:ng", "one", steps=[step])
    writer = _LiveWriter()
    applier = Applier(store, writer)

    async def _reader():
        cur = {f"{AP_MAC}:ng": {"channel": live["ng"]["channel"]}}
        full = {AP_ID: {k: dict(v) for k, v in live.items()}}
        return cur, full, set()

    result = await applier.apply(
        plan, dry_run=False, confirm_token=plan_confirm_token(plan), state_reader=_reader
    )
    assert result.applied is True
    dispatched_ng = next(r for r in writer.puts[0]["radio_table"] if r["radio"] == "ng")
    assert dispatched_ng["channel"] == 1  # the intended change landed
    assert "tx_power_mode" not in dispatched_ng  # the stale untouched field was NOT restored


async def test_radio_added_to_live_is_preserved_not_deleted(store):
    # #1: a radio '6e' has appeared in live since the plan was built (the plan snapshot
    # knew only ng+na). A whole-table PUT that omits 6e would DELETE it. Merge-at-
    # dispatch rebuilds the table from fresh live, so 6e is carried through untouched.
    from netadmin.fixes.models import WriteResult

    endpoint = f"rest/device/{AP_ID}"
    live = {
        "ng": {"radio": "ng", "channel": 3, "ht": 20},
        "na": {"radio": "na", "channel": 36, "ht": 80},
        "6e": {"radio": "6e", "channel": 100, "ht": 160},  # appeared in live
    }

    class _LiveWriter:
        def __init__(self):
            self.puts = []

        async def put(self, ep, body):
            self.puts.append(body)
            return WriteResult(ok=True, status_code=200, data={"meta": {}})

        async def post(self, ep, body):  # pragma: no cover - unused
            return await self.put(ep, body)

    step = FixStep(
        action=ActionType.CHANNEL_CHANGE,
        target_entity_type=EntityType.RADIO,
        target_native_id=f"{AP_MAC}:ng",
        description="ng 3->1",
        risk=RiskLevel.MEDIUM,
        method="PUT",
        endpoint=endpoint,
        payload={"radio_table": [{"radio": "ng", "channel": 1, "ht": 20},
                                 {"radio": "na", "channel": 36, "ht": 80}]},
        precondition=_radio_pre(f"{AP_MAC}:ng", {"channel": 3}),
        before={"method": "PUT", "endpoint": endpoint,
                "body": {"radio_table": [{"radio": "ng", "channel": 3, "ht": 20},
                                         {"radio": "na", "channel": 36, "ht": 80}]}},
        after={"method": "PUT", "endpoint": endpoint,
               "body": {"radio_table": [{"radio": "ng", "channel": 1, "ht": 20},
                                        {"radio": "na", "channel": 36, "ht": 80}]}},
        revertible=True,
    )
    plan = FixPlan("wifi.channel_plan", f"{AP_MAC}:ng", "one", steps=[step])
    writer = _LiveWriter()
    applier = Applier(store, writer)

    async def _reader():
        cur = {f"{AP_MAC}:ng": {"channel": live["ng"]["channel"]}}
        full = {AP_ID: {k: dict(v) for k, v in live.items()}}
        return cur, full, set()

    result = await applier.apply(
        plan, dry_run=False, confirm_token=plan_confirm_token(plan), state_reader=_reader
    )
    assert result.applied is True
    dispatched_codes = {r["radio"] for r in writer.puts[0]["radio_table"]}
    assert "6e" in dispatched_codes  # the live-only radio was preserved, not deleted
    dispatched_6e = next(r for r in writer.puts[0]["radio_table"] if r["radio"] == "6e")
    assert dispatched_6e["channel"] == 100


async def test_revert_of_a_change_that_deleted_a_radio_is_refused_not_falsely_reverted(store):
    # #6: the ledgered change's DISPATCHED after dropped radio 'na' (present in before).
    # Its inverse iterates only after-entries, so na never enters the touched set and a
    # naive revert restores ng, leaves na absent, and marks the row 'reverted' -- a
    # dishonest, incomplete rollback. The revert must instead DETECT the deleted radio
    # and refuse; the row must NOT read 'reverted' and nothing is dispatched.
    from netadmin.fixes.models import WriteResult

    endpoint = f"rest/device/{AP_ID}"

    class _CountingWriter:
        def __init__(self):
            self.calls = 0

        async def put(self, ep, body):
            self.calls += 1
            return WriteResult(ok=True, status_code=200, data={"meta": {}})

        async def post(self, ep, body):  # pragma: no cover - unused
            return await self.put(ep, body)

    change_id = store.insert_change(
        action="wifi.channel_change",
        before={"method": "PUT", "endpoint": endpoint,
                "body": {"radio_table": [{"radio": "ng", "channel": 3}, {"radio": "na", "channel": 36}]}},
        # Dispatched after DROPPED na (only ng survives the whole-table PUT).
        after={"method": "PUT", "endpoint": endpoint,
               "body": {"radio_table": [{"radio": "ng", "channel": 1}]}},
        status="applied",
        ts=1,
    )
    writer = _CountingWriter()
    applier = Applier(store, writer)
    live = {"ng": {"radio": "ng", "channel": 1}}

    with pytest.raises(SafetyViolation):
        await applier.revert(change_id, current_radios=live)
    assert writer.calls == 0  # nothing dispatched
    assert store.get_change(change_id)["status"] != "reverted"  # never falsely reverted


async def test_concurrent_reverts_first_ambiguous_blocks_the_second_from_replaying(store):
    # #2: two concurrent reverts of the SAME change. The FIRST restore's outcome is
    # AMBIGUOUS (lost response). Before the fix, revert only refused status=='reverted'
    # and recorded no ambiguous/in-progress state, so the row stayed 'applied' and the
    # SECOND revert dispatched AGAIN -- two PUTs. The fix records the ambiguous revert
    # as terminal-uncertain under the lock, so the second re-reads it and refuses. The
    # mutation is dispatched exactly ONCE.
    import asyncio as _asyncio

    from netadmin.fixes.models import WriteResult

    endpoint = f"rest/device/{AP_ID}"
    change_id = store.insert_change(
        action="wifi.channel_change",
        before={"method": "PUT", "endpoint": endpoint,
                "body": {"radio_table": [{"radio": "ng", "channel": 3}]}},
        after={"method": "PUT", "endpoint": endpoint,
               "body": {"radio_table": [{"radio": "ng", "channel": 1}]}},
        status="applied",
        ts=1,
    )

    class _AmbiguousWriter:
        def __init__(self):
            self.puts = 0

        async def put(self, ep, body):
            self.puts += 1
            await _asyncio.sleep(0)
            return WriteResult(
                ok=False, status_code=None,
                data={"ambiguous": True, "error": "lost response after PUT"},
            )

        async def post(self, ep, body):  # pragma: no cover - unused
            return await self.put(ep, body)

    writer = _AmbiguousWriter()
    applier = Applier(store, writer)
    live = {"ng": {"radio": "ng", "channel": 1}}

    async def _reader():
        return {k: dict(v) for k, v in live.items()}, False

    results = await _asyncio.gather(
        applier.revert(change_id, state_reader=_reader),
        applier.revert(change_id, state_reader=_reader),
        return_exceptions=True,
    )

    assert writer.puts == 1  # dispatched exactly once -- the ambiguous first blocks a replay
    ambiguous = [r for r in results if isinstance(r, WriteResult)]
    refused = [r for r in results if isinstance(r, FixError)]
    assert len(ambiguous) == 1 and ambiguous[0].data.get("ambiguous") is True
    assert len(refused) == 1
    assert "already" in str(refused[0]) or "unknown" in str(refused[0]) or "ambiguous" in str(refused[0])
    # The row is terminal-uncertain, not 'reverted' and not plain 'applied'.
    assert store.get_change(change_id)["status"] == "revert_unknown"


# --------------------------------------------------------------------------- #
# Verifier round 8: the merged operation actually dispatched -- not the plan-time
# payload -- is what gets validated against fresh live and judged for revertibility.
# --------------------------------------------------------------------------- #
class _RecordingLiveWriter:
    """A writer that records each PUT body and reports success."""

    def __init__(self) -> None:
        self.puts: list[dict] = []

    async def put(self, ep, body):
        from netadmin.fixes.models import WriteResult

        self.puts.append(body)
        return WriteResult(ok=True, status_code=200, data={"meta": {}})

    async def post(self, ep, body):  # pragma: no cover - unused
        return await self.put(ep, body)


async def test_round8_1_delta_field_diverging_from_live_is_drift(store):
    # #1: the payload changes channel 3->1 AND bundles a tx-power delta (high->medium),
    # but the precondition only asserts the channel. Fresh live still has channel 3
    # (precondition holds) but tx-power is LOW -- a concurrent change. Merge-at-dispatch
    # would layer the medium delta straight onto live and silently clobber that LOW.
    # Binding every SENT delta field to its before-value catches the divergence and
    # refuses (PreconditionDrift); nothing is dispatched.
    endpoint = f"rest/device/{AP_ID}"
    step = FixStep(
        action=ActionType.CHANNEL_CHANGE,
        target_entity_type=EntityType.RADIO,
        target_native_id=f"{AP_MAC}:ng",
        description="channel + bundled power delta",
        risk=RiskLevel.MEDIUM,
        method="PUT",
        endpoint=endpoint,
        payload={"radio_table": [{"radio": "ng", "channel": 1, "tx_power_mode": "medium"}]},
        precondition=_radio_pre(f"{AP_MAC}:ng", {"channel": 3}),  # channel-only
        before={"method": "PUT", "endpoint": endpoint,
                "body": {"radio_table": [{"radio": "ng", "channel": 3, "tx_power_mode": "high"}]}},
        after={"method": "PUT", "endpoint": endpoint,
               "body": {"radio_table": [{"radio": "ng", "channel": 1, "tx_power_mode": "medium"}]}},
        revertible=True,
    )
    plan = FixPlan("wifi.channel_plan", f"{AP_MAC}:ng", "bundle", steps=[step])
    writer = _RecordingLiveWriter()
    applier = Applier(store, writer)
    live = {"ng": {"radio": "ng", "channel": 3, "tx_power_mode": "low"}}  # power drifted to LOW

    async def _reader():
        cur = {f"{AP_MAC}:ng": {"channel": 3}}  # channel precondition still holds
        full = {AP_ID: {k: dict(v) for k, v in live.items()}}
        return cur, full, set()

    with pytest.raises(PreconditionDrift):
        await applier.apply(
            plan, dry_run=False, confirm_token=plan_confirm_token(plan), state_reader=_reader
        )
    assert writer.puts == []  # refused before any dispatch


async def test_round8_2_unchanged_top_level_field_not_resent_over_live(store):
    # #2: an UNCHANGED top-level field (disabled == before) rides along in the payload.
    # Live has disabled=True (a concurrent change). A whole-device PUT that re-sent
    # disabled=False would clobber that AND escape rollback (revert restores only
    # radio_table). The dispatched body must NOT carry an unchanged top-level field the
    # step never intends to change; it is dropped, so live's disabled is untouched.
    endpoint = f"rest/device/{AP_ID}"
    step = FixStep(
        action=ActionType.CHANNEL_CHANGE,
        target_entity_type=EntityType.RADIO,
        target_native_id=f"{AP_MAC}:ng",
        description="channel move, incidental disabled=False",
        risk=RiskLevel.MEDIUM,
        method="PUT",
        endpoint=endpoint,
        payload={"radio_table": [{"radio": "ng", "channel": 1}], "disabled": False},
        precondition=_radio_pre(f"{AP_MAC}:ng", {"channel": 3}),
        before={"method": "PUT", "endpoint": endpoint,
                "body": {"radio_table": [{"radio": "ng", "channel": 3}], "disabled": False}},
        after={"method": "PUT", "endpoint": endpoint,
               "body": {"radio_table": [{"radio": "ng", "channel": 1}], "disabled": False}},
        revertible=True,
    )
    plan = FixPlan("wifi.channel_plan", f"{AP_MAC}:ng", "toplevel", steps=[step])
    writer = _RecordingLiveWriter()
    applier = Applier(store, writer)
    live = {"ng": {"radio": "ng", "channel": 3}}  # radio-level live; device 'disabled' is True out-of-band

    async def _reader():
        cur = {f"{AP_MAC}:ng": {"channel": 3}}
        full = {AP_ID: {k: dict(v) for k, v in live.items()}}
        return cur, full, set()

    result = await applier.apply(
        plan, dry_run=False, confirm_token=plan_confirm_token(plan), state_reader=_reader
    )
    assert result.applied is True
    dispatched = writer.puts[0]
    assert "disabled" not in dispatched  # the unchanged top-level field was NOT re-sent over live
    assert next(r for r in dispatched["radio_table"] if r["radio"] == "ng")["channel"] == 1


async def test_round8_3_merged_reverse_min_rssi_on_mesh_refused_up_front(store):
    # #3: the plan captured min-RSSI DISABLED. Fresh live has since ENABLED it, and the
    # AP is now a mesh uplink. The merged table this apply dispatches carries that live
    # min-RSSI=on; an immediate revert (restoring the channel onto that merged table)
    # would re-assert min-RSSI on a mesh uplink, which the rail bars. Validating the
    # reverse against the MERGED op (not the stale payload) refuses the apply UP FRONT.
    endpoint = f"rest/device/{AP_ID}"
    step = FixStep(
        action=ActionType.CHANNEL_CHANGE,
        target_entity_type=EntityType.RADIO,
        target_native_id=f"{AP_MAC}:ng",
        description="channel move; plan captured min-RSSI off",
        risk=RiskLevel.MEDIUM,
        method="PUT",
        endpoint=endpoint,
        payload={"radio_table": [{"radio": "ng", "channel": 1, "min_rssi_enabled": False, "min_rssi": 0}]},
        precondition=_radio_pre(f"{AP_MAC}:ng", {"channel": 3}),
        before={"method": "PUT", "endpoint": endpoint,
                "body": {"radio_table": [{"radio": "ng", "channel": 3, "min_rssi_enabled": False, "min_rssi": 0}]}},
        after={"method": "PUT", "endpoint": endpoint,
               "body": {"radio_table": [{"radio": "ng", "channel": 1, "min_rssi_enabled": False, "min_rssi": 0}]}},
        revertible=True,
    )
    plan = FixPlan("wifi.channel_plan", f"{AP_MAC}:ng", "merged-reverse", steps=[step])
    writer = _RecordingLiveWriter()
    applier = Applier(store, writer)
    # Live: min-RSSI re-enabled since the plan, and the AP is now a mesh uplink.
    live = {"ng": {"radio": "ng", "channel": 3, "min_rssi_enabled": True, "min_rssi": -75}}

    async def _reader():
        cur = {f"{AP_MAC}:ng": {"channel": 3}}
        full = {AP_ID: {k: dict(v) for k, v in live.items()}}
        return cur, full, {AP_ID}  # mesh uplink

    with pytest.raises(SafetyViolation):
        await applier.apply(
            plan, dry_run=False, confirm_token=plan_confirm_token(plan), state_reader=_reader
        )
    assert writer.puts == []  # never applied something whose merged form can't be reverted
    assert store.list_changes() == []


async def test_round8_10_revert_restores_a_field_the_change_deleted(store):
    # #10: the ledgered change's DISPATCHED after DROPPED a field (tx_power_mode) from a
    # SURVIVING radio -- before ng={channel:3, tx_power_mode:high}, after ng={channel:1}.
    # Iterating only after-fields missed the deletion, so a naive revert restored the
    # channel, left tx_power_mode absent, and still marked the row 'reverted'. The revert
    # must restore the deleted field too (a complete inverse), not a partial rollback.
    endpoint = f"rest/device/{AP_ID}"
    change_id = store.insert_change(
        action="wifi.channel_change",
        before={"method": "PUT", "endpoint": endpoint,
                "body": {"radio_table": [{"radio": "ng", "channel": 3, "tx_power_mode": "high"}]}},
        after={"method": "PUT", "endpoint": endpoint,
               "body": {"radio_table": [{"radio": "ng", "channel": 1}]}},  # tx_power_mode dropped
        status="applied",
        ts=1,
    )
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    live = {"ng": {"radio": "ng", "channel": 1}}  # tx_power_mode absent, consistent with the after

    revert = await applier.revert(change_id, current_radios=live)
    assert revert.ok
    sent_ng = next(r for r in writer.calls[-1].body["radio_table"] if r["radio"] == "ng")
    assert sent_ng["channel"] == 3  # touched field restored
    assert sent_ng["tx_power_mode"] == "high"  # the DELETED field restored too (not left absent)
    assert store.get_change(change_id)["status"] == "reverted"


async def test_round8_5_cancelled_mid_send_revert_blocks_replay(store):
    # #5: cancel revert A after its writer sent the PUT but before it returned. Without a
    # durable in-flight marker the row stayed 'applied' and revert B replayed the
    # mutation (a second PUT). Recording 'reverting' UNDER THE LOCK before the dispatch
    # returns makes a cancelled-mid-send revert leave a state that blocks B: exactly ONE
    # dispatch, and B refuses rather than replaying.
    import asyncio as _asyncio

    from netadmin.fixes.models import WriteResult

    endpoint = f"rest/device/{AP_ID}"
    change_id = store.insert_change(
        action="wifi.channel_change",
        before={"method": "PUT", "endpoint": endpoint,
                "body": {"radio_table": [{"radio": "ng", "channel": 3}]}},
        after={"method": "PUT", "endpoint": endpoint,
               "body": {"radio_table": [{"radio": "ng", "channel": 1}]}},
        status="applied",
        ts=1,
    )
    parked = _asyncio.Event()
    release = _asyncio.Event()  # never set -- holds the in-flight write open

    class _HangingWriter:
        def __init__(self) -> None:
            self.puts = 0

        async def put(self, ep, body):
            self.puts += 1
            parked.set()  # the PUT has been "sent"
            await release.wait()  # park before returning
            return WriteResult(ok=True, status_code=200, data={"meta": {}})

        async def post(self, ep, body):  # pragma: no cover - unused
            return await self.put(ep, body)

    writer = _HangingWriter()
    applier = Applier(store, writer)
    live = {"ng": {"radio": "ng", "channel": 1}}

    async def _reader():
        return {k: dict(v) for k, v in live.items()}, False

    task_a = _asyncio.create_task(applier.revert(change_id, state_reader=_reader))
    await _asyncio.wait_for(parked.wait(), timeout=1)  # A dispatched the PUT and parked

    assert writer.puts == 1
    # The row was durably marked 'reverting' BEFORE the dispatch returned.
    assert store.get_change(change_id)["status"] == "reverting"

    task_a.cancel()
    with pytest.raises(_asyncio.CancelledError):
        await task_a

    # Revert B must NOT replay: it re-reads the durable 'reverting' status under the
    # lock and refuses, treating the interrupted revert as uncertain (not retryable).
    with pytest.raises(FixError):
        await applier.revert(change_id, state_reader=_reader)
    assert writer.puts == 1  # exactly one dispatch, never replayed
    assert store.get_change(change_id)["status"] == "reverting"


# --------------------------------------------------------------------------- #
# Verifier round 9, D1: a delta field DELETED from fresh live (that the reviewed
# before HELD) is drift. The precondition asserts only the headline attribute, so a
# bundled delta field whose live value has VANISHED escapes it -- and the earlier
# delta-field guard only compared present fields, skipping absent ones. The device's
# shape has changed since review: the before-value can no longer be confirmed, and a
# later revert would write a now-stale value. Refuse (PreconditionDrift), send nothing.
# --------------------------------------------------------------------------- #
async def test_d1_delta_field_absent_from_live_is_drift(store):
    endpoint = f"rest/device/{AP_ID}"
    step = FixStep(
        action=ActionType.CHANNEL_CHANGE,
        target_entity_type=EntityType.RADIO,
        target_native_id=f"{AP_MAC}:ng",
        description="channel move + bundled power delta",
        risk=RiskLevel.MEDIUM,
        method="PUT",
        endpoint=endpoint,
        # Delta: channel 3->1 AND tx_power_mode high->medium.
        payload={"radio_table": [{"radio": "ng", "channel": 1, "tx_power_mode": "medium"}]},
        precondition=_radio_pre(f"{AP_MAC}:ng", {"channel": 3}),  # channel-only
        before={"method": "PUT", "endpoint": endpoint,
                "body": {"radio_table": [{"radio": "ng", "channel": 3, "tx_power_mode": "high"}]}},
        after={"method": "PUT", "endpoint": endpoint,
               "body": {"radio_table": [{"radio": "ng", "channel": 1, "tx_power_mode": "medium"}]}},
        revertible=True,
    )
    plan = FixPlan("wifi.channel_plan", f"{AP_MAC}:ng", "d1", steps=[step])
    writer = _RecordingLiveWriter()
    applier = Applier(store, writer)
    # Fresh live: channel STILL 3 (precondition holds) but tx_power_mode is GONE --
    # the device's shape changed since the plan was reviewed.
    live = {"ng": {"radio": "ng", "channel": 3}}

    async def _reader():
        cur = {f"{AP_MAC}:ng": {"channel": 3}}  # channel precondition still satisfied
        full = {AP_ID: {k: dict(v) for k, v in live.items()}}
        return cur, full, set()

    with pytest.raises(PreconditionDrift):
        await applier.apply(
            plan, dry_run=False, confirm_token=plan_confirm_token(plan), state_reader=_reader
        )
    assert writer.puts == []  # refused before any dispatch -- no stale-before revert armed


# --------------------------------------------------------------------------- #
# Verifier round 9, D8: a change that ADDS a radio (present in dispatched/after,
# absent from before) has no complete whole-table inverse -- the revert rolls back
# the added radio's fields but leaves the radio entry behind, so the added radio
# survives while the row is marked 'reverted'. Refuse it AT APPLY, symmetric with the
# delete-radio refusal; nothing is dispatched.
# --------------------------------------------------------------------------- #
async def test_d8_change_that_adds_a_radio_is_refused_at_apply(store):
    endpoint = f"rest/device/{AP_ID}"
    step = FixStep(
        action=ActionType.CHANNEL_CHANGE,
        target_entity_type=EntityType.RADIO,
        target_native_id=f"{AP_MAC}:ng",
        description="ng channel move AND adds na",
        risk=RiskLevel.MEDIUM,
        method="PUT",
        endpoint=endpoint,
        # before has ONLY ng; payload changes ng.channel AND adds a brand-new na radio.
        payload={"radio_table": [{"radio": "ng", "channel": 1, "ht": 20},
                                 {"radio": "na", "channel": 36}]},
        precondition=_radio_pre(f"{AP_MAC}:ng", {"channel": 3}),
        before={"method": "PUT", "endpoint": endpoint,
                "body": {"radio_table": [{"radio": "ng", "channel": 3, "ht": 20}]}},
        after={"method": "PUT", "endpoint": endpoint,
               "body": {"radio_table": [{"radio": "ng", "channel": 1, "ht": 20},
                                        {"radio": "na", "channel": 36}]}},
        revertible=True,
    )
    plan = FixPlan("wifi.channel_plan", f"{AP_MAC}:ng", "d8", steps=[step])
    writer = _RecordingLiveWriter()
    applier = Applier(store, writer)
    live = {"ng": {"radio": "ng", "channel": 3, "ht": 20}}  # live knows only ng

    async def _reader():
        cur = {f"{AP_MAC}:ng": {"channel": 3}}
        full = {AP_ID: {k: dict(v) for k, v in live.items()}}
        return cur, full, set()

    with pytest.raises(SafetyViolation, match="[Aa]dd"):
        await applier.apply(
            plan, dry_run=False, confirm_token=plan_confirm_token(plan), state_reader=_reader
        )
    assert writer.puts == []  # refused up front -- no half-revertible added radio dispatched


async def test_d8_concurrent_live_added_radio_is_still_preserved_not_refused(store):
    # Guard against over-refusal: a radio the concurrent OPERATOR added to live (not
    # this step) must still be PRESERVED by merge-at-dispatch (#1), not mistaken for a
    # step-added radio. The step touches only ng; na appeared in live out-of-band.
    endpoint = f"rest/device/{AP_ID}"
    step = FixStep(
        action=ActionType.CHANNEL_CHANGE,
        target_entity_type=EntityType.RADIO,
        target_native_id=f"{AP_MAC}:ng",
        description="ng channel move only",
        risk=RiskLevel.MEDIUM,
        method="PUT",
        endpoint=endpoint,
        payload={"radio_table": [{"radio": "ng", "channel": 1, "ht": 20}]},
        precondition=_radio_pre(f"{AP_MAC}:ng", {"channel": 3}),
        before={"method": "PUT", "endpoint": endpoint,
                "body": {"radio_table": [{"radio": "ng", "channel": 3, "ht": 20}]}},
        after={"method": "PUT", "endpoint": endpoint,
               "body": {"radio_table": [{"radio": "ng", "channel": 1, "ht": 20}]}},
        revertible=True,
    )
    plan = FixPlan("wifi.channel_plan", f"{AP_MAC}:ng", "d8-ok", steps=[step])
    writer = _RecordingLiveWriter()
    applier = Applier(store, writer)
    # na appeared in live since the plan was built; the STEP never mentions it.
    live = {"ng": {"radio": "ng", "channel": 3, "ht": 20},
            "na": {"radio": "na", "channel": 40, "ht": 80}}

    async def _reader():
        cur = {f"{AP_MAC}:ng": {"channel": 3}}
        full = {AP_ID: {k: dict(v) for k, v in live.items()}}
        return cur, full, set()

    result = await applier.apply(
        plan, dry_run=False, confirm_token=plan_confirm_token(plan), state_reader=_reader
    )
    assert result.applied is True  # NOT refused -- na is a live-carried radio, not step-added
    dispatched_codes = {r["radio"] for r in writer.puts[0]["radio_table"]}
    assert dispatched_codes == {"ng", "na"}  # the concurrent na is preserved


# --------------------------------------------------------------------------- #
# #w10a-2: an AMBIGUOUS apply must not be replayable with the same confirm token.
# A GET showing the before-state does NOT exclude an earlier timed-out mutation
# still processing upstream, so re-applying risks a double-apply. Before the fix a
# second apply (same token, unchanged snapshot) dispatched AGAIN -- two 'unknown'
# rows. The fix refuses the new apply while an uncertain change for the same
# target/issue stands, the apply-side mirror of the revert eligibility recheck.
# --------------------------------------------------------------------------- #
async def test_w10a2_ambiguous_apply_cannot_be_replayed_with_same_token(store, ap_device):
    from netadmin.fixes.models import WriteResult

    # Register the radio entity so the ledger row carries a real entity_id -- exactly
    # as production ingest does -- and the guard resolves the target.
    store.upsert_entity(radio_entity("ng"))

    ambiguous = WriteResult(
        ok=False, status_code=None, data={"ambiguous": True, "error": "outcome unknown; not retried"}
    )
    writer = FakeControllerWriter(response=ambiguous)
    applier = Applier(store, writer)
    # issue_id left None: the ledger row still carries the resolved entity_id, so the
    # apply-replay guard blocks on the physical target -- exactly the production path.
    plan = _channel_plan(ap_device)
    token = plan_confirm_token(plan)

    first = await applier.apply(
        plan, dry_run=False, confirm_token=token, current_state=_state_ok()
    )
    assert first.steps[0].status == "unknown"
    assert store.list_changes()[0]["status"] == "unknown"
    assert writer.call_count == 1  # dispatched exactly once so far

    # SAME token, SAME unchanged snapshot: the replay must be REFUSED, not dispatched.
    with pytest.raises(FixError) as exc:
        await applier.apply(
            plan, dry_run=False, confirm_token=token, current_state=_state_ok()
        )
    assert "unresolved" in str(exc.value) or "uncertain" in str(exc.value)
    # No second dispatch, and still exactly ONE ledger row -- no duplicate 'unknown'.
    assert writer.call_count == 1
    assert len(store.list_changes()) == 1


async def test_w10a2_reverting_change_blocks_a_fresh_apply_on_same_target(store, ap_device):
    # An in-flight/interrupted revert (status 'reverting') is likewise unresolved: a
    # fresh apply on that same target must refuse, not race a mutation the revert may
    # still be performing.
    store.upsert_entity(radio_entity("ng"))
    entity = store.find_entity(EntityType.RADIO, f"{AP_MAC}:ng")
    store.insert_change(
        action="wifi.channel_change",
        before={"method": "PUT", "endpoint": f"rest/device/{AP_ID}",
                "body": {"radio_table": [{"radio": "ng", "channel": 3}]}},
        after={"method": "PUT", "endpoint": f"rest/device/{AP_ID}",
               "body": {"radio_table": [{"radio": "ng", "channel": 1}]}},
        status="reverting",
        ts=1,
        entity_id=int(entity["entity_id"]),
    )
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    plan = _channel_plan(ap_device)
    with pytest.raises(FixError):
        await applier.apply(
            plan, dry_run=False, confirm_token=plan_confirm_token(plan), current_state=_state_ok()
        )
    assert writer.call_count == 0  # nothing dispatched over the unresolved revert


# --------------------------------------------------------------------------- #
# #w11a-3: the apply-replay guard must key on the physical DEVICE, not the radio.
# A whole-radio_table PUT to rest/device/<id> replaces the ENTIRE device, so an
# unresolved uncertain change on ONE radio (ng) of an AP must block a fresh apply
# to ANY OTHER radio (na) on that SAME AP -- its PUT would re-send ng at its still-
# unresolved value. Before the fix the guard compared only ledger entity_id/issue_id,
# so a DIFFERENT radio's entity slipped past it and dispatched.
# --------------------------------------------------------------------------- #
async def test_w11a3_uncertain_change_on_one_radio_blocks_apply_to_sibling_radio(store):
    endpoint = f"rest/device/{AP_ID}"
    # Register BOTH radios as distinct entities -- exactly as production ingest does,
    # each with its own entity_id -- so the per-entity guard cannot match ng's row to
    # an na-only plan; only the device-keyed guard can.
    store.upsert_entity(radio_entity("ng"))
    store.upsert_entity(radio_entity("na"))
    ng_entity = store.find_entity(EntityType.RADIO, f"{AP_MAC}:ng")

    # An unresolved (ambiguous) apply on the ng radio of this AP still stands.
    store.insert_change(
        action="wifi.channel_change",
        before={"method": "PUT", "endpoint": endpoint,
                "body": {"radio_table": [{"radio": "ng", "channel": 3}]}},
        after={"method": "PUT", "endpoint": endpoint,
               "body": {"radio_table": [{"radio": "ng", "channel": 1}]}},
        status="unknown",
        ts=1,
        entity_id=int(ng_entity["entity_id"]),
    )

    # A genuine, SEPARATE plan on the na radio of the SAME AP (different entity, no
    # shared issue_id) -- the reproduction's "second radio on the same device".
    na_step = FixStep(
        action=ActionType.CHANNEL_CHANGE,
        target_entity_type=EntityType.RADIO,
        target_native_id=f"{AP_MAC}:na",
        description="na 36->40",
        risk=RiskLevel.MEDIUM,
        method="PUT",
        endpoint=endpoint,
        payload={"radio_table": [{"radio": "na", "channel": 40}]},
        precondition=_radio_pre(f"{AP_MAC}:na", {"channel": 36}),
        before={"method": "PUT", "endpoint": endpoint,
                "body": {"radio_table": [{"radio": "na", "channel": 36}]}},
        after={"method": "PUT", "endpoint": endpoint,
               "body": {"radio_table": [{"radio": "na", "channel": 40}]}},
        revertible=True,
    )
    plan = FixPlan("wifi.channel_plan", f"{AP_MAC}:na", "na-only", steps=[na_step])
    writer = FakeControllerWriter()
    applier = Applier(store, writer)

    with pytest.raises(FixError) as exc:
        await applier.apply(
            plan,
            dry_run=False,
            confirm_token=plan_confirm_token(plan),
            current_state={f"{AP_MAC}:na": {"channel": 36}},
        )
    assert AP_ID in str(exc.value)  # the refusal names the physical device
    # Nothing dispatched over the sibling's unresolved change, and no new ledger row.
    assert writer.call_count == 0
    assert len(store.list_changes()) == 1


# --------------------------------------------------------------------------- #
# #w10a-3: a rejected REVERT must not promote an UNCERTAIN apply to 'applied'.
# A definitively-rejected restore proves only that the restore failed; it says
# NOTHING about whether the original (uncertain) apply landed. Before the fix the
# rejection branch unconditionally wrote 'applied', fabricating a confirmation the
# system never had. The fix keeps the change at its PRIOR state.
# --------------------------------------------------------------------------- #
async def test_w10a3_rejected_revert_leaves_unknown_apply_unknown(store):
    from netadmin.fixes.models import WriteResult

    endpoint = f"rest/device/{AP_ID}"
    change_id = store.insert_change(
        action="wifi.channel_change",
        before={"method": "PUT", "endpoint": endpoint,
                "body": {"radio_table": [{"radio": "ng", "channel": 3}]}},
        after={"method": "PUT", "endpoint": endpoint,
               "body": {"radio_table": [{"radio": "ng", "channel": 1}]}},
        status="unknown",  # the forward apply outcome was NEVER confirmed
        ts=1,
    )

    class _RejectingWriter:
        def __init__(self):
            self.puts = 0

        async def put(self, ep, body):
            self.puts += 1
            # A DEFINITIVE rejection (HTTP 400, no 'ambiguous' marker).
            return WriteResult(ok=False, status_code=400, data={"error": "rejected"})

        async def post(self, ep, body):  # pragma: no cover - unused
            return await self.put(ep, body)

    writer = _RejectingWriter()
    applier = Applier(store, writer)
    live = {"ng": {"radio": "ng", "channel": 1}}
    write = await applier.revert(change_id, current_radios=live)

    assert write.ok is False and write.status_code == 400
    assert writer.puts == 1
    # The rejected restore does NOT prove the uncertain apply succeeded: stay 'unknown',
    # NEVER promote to 'applied'.
    assert store.get_change(change_id)["status"] == "unknown"


async def test_w10a3_rejected_revert_of_applied_change_stays_applied(store):
    # Control: a genuinely CONFIRMED apply ('applied') whose revert is rejected DOES
    # roll back to 'applied' -- it legitimately still stands and is retryable.
    from netadmin.fixes.models import WriteResult

    endpoint = f"rest/device/{AP_ID}"
    change_id = store.insert_change(
        action="wifi.channel_change",
        before={"method": "PUT", "endpoint": endpoint,
                "body": {"radio_table": [{"radio": "ng", "channel": 3}]}},
        after={"method": "PUT", "endpoint": endpoint,
               "body": {"radio_table": [{"radio": "ng", "channel": 1}]}},
        status="applied",
        ts=1,
    )

    class _RejectingWriter:
        async def put(self, ep, body):
            return WriteResult(ok=False, status_code=400, data={"error": "rejected"})

        async def post(self, ep, body):  # pragma: no cover - unused
            return await self.put(ep, body)

    applier = Applier(store, _RejectingWriter())
    live = {"ng": {"radio": "ng", "channel": 1}}
    write = await applier.revert(change_id, current_radios=live)

    assert write.ok is False
    assert store.get_change(change_id)["status"] == "applied"


# --------------------------------------------------------------------------- #
# #w12a-4: a rejected REVERT must never fabricate an 'applied' change.
# Repro (unmodified channel planner): a rejected apply leaves 'failed'; a
# cancelled apply leaves 'applying'. NEITHER confirmed a mutation, so neither is
# revertible -- the revert is refused UP FRONT (no dispatch), and the prior status
# is preserved verbatim. Before the fix, revert was permitted on 'failed'/'applying'
# and the rejection branch promoted EVERY non-'unknown' prior status to 'applied',
# turning a change that never landed into a confirmed one.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("prior", ["failed", "applying"])
async def test_w12a4_revert_of_unconfirmed_change_is_refused_no_promotion(store, prior):
    from netadmin.fixes.models import FixError

    endpoint = f"rest/device/{AP_ID}"
    change_id = store.insert_change(
        action="wifi.channel_change",
        before={"method": "PUT", "endpoint": endpoint,
                "body": {"radio_table": [{"radio": "ng", "channel": 3}]}},
        after={"method": "PUT", "endpoint": endpoint,
               "body": {"radio_table": [{"radio": "ng", "channel": 1}]}},
        status=prior,  # a change that NEVER confirmed a mutation
        ts=1,
    )

    class _RejectingWriter:
        def __init__(self):
            self.calls = 0

        async def put(self, ep, body):  # pragma: no cover - must never be reached
            self.calls += 1
            return WriteResult(ok=False, status_code=400, data={"error": "rejected"})

        async def post(self, ep, body):  # pragma: no cover - unused
            return await self.put(ep, body)

    writer = _RejectingWriter()
    applier = Applier(store, writer)
    live = {"ng": {"radio": "ng", "channel": 1}}
    # The revert is refused before any dispatch -- there is no confirmed change to
    # restore ('failed') / the forward apply is still unresolved ('applying').
    with pytest.raises(FixError):
        await applier.revert(change_id, current_radios=live)
    assert writer.calls == 0  # nothing dispatched
    # And, crucially, the change was NOT promoted to 'applied': it keeps its status.
    assert store.get_change(change_id)["status"] == prior


async def test_w12a4_rejected_revert_of_applied_change_still_stays_applied(store):
    # Control (defense in depth): a genuinely CONFIRMED apply whose revert is rejected
    # DOES roll back to 'applied' -- it legitimately still stands and is retryable.
    from netadmin.fixes.models import WriteResult as _WR

    endpoint = f"rest/device/{AP_ID}"
    change_id = store.insert_change(
        action="wifi.channel_change",
        before={"method": "PUT", "endpoint": endpoint,
                "body": {"radio_table": [{"radio": "ng", "channel": 3}]}},
        after={"method": "PUT", "endpoint": endpoint,
               "body": {"radio_table": [{"radio": "ng", "channel": 1}]}},
        status="applied",
        ts=1,
    )

    class _RejectingWriter:
        async def put(self, ep, body):
            return _WR(ok=False, status_code=400, data={"error": "rejected"})

        async def post(self, ep, body):  # pragma: no cover - unused
            return await self.put(ep, body)

    applier = Applier(store, _RejectingWriter())
    live = {"ng": {"radio": "ng", "channel": 1}}
    write = await applier.revert(change_id, current_radios=live)
    assert write.ok is False
    assert store.get_change(change_id)["status"] == "applied"


async def test_w12a4_assert_revertible_status_refuses_failed_and_applying():
    # Unit-level defense in depth: the eligibility gate itself refuses 'failed' and
    # 'applying' (both pre-lock and under-lock), while 'applied'/'unknown' pass.
    from netadmin.fixes.models import FixError

    for bad in ("failed", "applying"):
        with pytest.raises(FixError):
            Applier._assert_revertible_status(1, bad)
        with pytest.raises(FixError):
            Applier._assert_revertible_status(1, bad, allow_in_flight=True)
    # A confirmed apply and an ambiguous apply remain revertible (no raise).
    Applier._assert_revertible_status(1, "applied")
    Applier._assert_revertible_status(1, "unknown")
