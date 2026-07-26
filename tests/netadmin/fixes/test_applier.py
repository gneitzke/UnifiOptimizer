"""Applier: dry-run sends nothing; a real apply is gated six ways; revert works.

The FakeControllerWriter's ``calls`` list is the load-bearing assertion throughout:
an empty list proves a code path reached no network. No test here ever constructs a
RealControllerWriter, and one test proves the dry-run path cannot.
"""

from __future__ import annotations

import json

import pytest

from netadmin.domain.types import EntityType, Severity
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

from .conftest import AP_ID, AP_MAC, SW_MAC, make_ap_device, make_finding, radio_entity

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

    # Fresh live radio state (post-apply): min-RSSI unchanged by a channel fix, so
    # restoring the original radio_table is not a min-RSSI re-enable.
    live_radios = {
        "ng": {"min_rssi_enabled": True, "min_rssi": -75},
        "na": {"min_rssi_enabled": False, "min_rssi": 0},
    }
    revert = await applier.revert(change_id, current_radios=live_radios)

    assert revert.ok
    # The revert PUT carries the ORIGINAL radio_table (channel back to 3).
    last = writer.calls[-1]
    assert last.method == "PUT"
    assert last.endpoint == f"rest/device/{AP_ID}"
    assert last.body["radio_table"][0]["channel"] == 3
    row = store.get_change(change_id)
    assert row["status"] == "reverted"
    assert row["reverted_ts"] is not None


async def test_revert_of_nonrevertible_change_is_refused(store, switch_device):
    from .conftest import port_entity

    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    finding = make_finding(
        "wired.port_flapping",
        port_entity(5),
        severity=Severity.P1,
        evidence={"poe_reboot_loop": True},
    )
    plan = plan_fix(finding, device=switch_device)
    result = await applier.apply(
        plan,
        dry_run=False,
        confirm_token=plan_confirm_token(plan),
        current_state={f"{SW_MAC}:5": {"poe_mode": "auto"}},
    )
    assert result.applied is True
    change_id = result.change_ids[0]
    with pytest.raises(FixError):
        await applier.revert(change_id)


async def test_revert_unknown_change_raises(store):
    applier = Applier(store, FakeControllerWriter())
    with pytest.raises(FixError):
        await applier.revert(999)


# --------------------------------------------------------------------------- #
# Revert is re-gated by the absolute min-RSSI rail (never a back door around it)
# --------------------------------------------------------------------------- #
def _min_rssi_remove_plan(ap_device, issue_id=None):
    finding = make_finding(
        "wifi.min_rssi_misconfig",
        radio_entity("ng"),
        evidence={"reason": "mesh_uplink_ap", "on_mesh_ap": True},
    )
    return plan_fix(finding, device=ap_device, issue_id=issue_id)


async def _apply_min_rssi_removal(store, writer):
    """Apply a genuine min-RSSI removal; return the ledgered change id."""
    from .conftest import make_ap_device

    ap_device = make_ap_device()  # ng radio has min_rssi_enabled=True
    applier = Applier(store, writer)
    plan = _min_rssi_remove_plan(ap_device)
    result = await applier.apply(
        plan,
        dry_run=False,
        confirm_token=plan_confirm_token(plan),
        current_state={f"{AP_MAC}:ng": {"min_rssi_enabled": True}},
    )
    assert result.applied is True
    return applier, result.change_ids[0]


async def test_revert_that_would_reenable_min_rssi_is_refused(store):
    # Reverting a min-RSSI *removal* would set min-RSSI back on: the invariant
    # ("only ever removed, never set") forbids it. Live state now has it off.
    writer = FakeControllerWriter()
    applier, change_id = await _apply_min_rssi_removal(store, writer)
    calls_after_apply = writer.call_count

    with pytest.raises(SafetyViolation):
        await applier.revert(change_id, current_radios={"ng": {"min_rssi_enabled": False}})
    assert writer.call_count == calls_after_apply  # revert sent nothing
    assert store.get_change(change_id)["status"] != "reverted"


async def test_revert_enabling_min_rssi_on_mesh_uplink_is_refused(store):
    # Even if min-RSSI happened to be on live, restoring it on an AP that is now a
    # mesh uplink is refused outright (mesh min-RSSI is removal-only).
    writer = FakeControllerWriter()
    applier, change_id = await _apply_min_rssi_removal(store, writer)
    calls_after_apply = writer.call_count

    with pytest.raises(SafetyViolation):
        await applier.revert(
            change_id,
            current_radios={"ng": {"min_rssi_enabled": True, "min_rssi": -75}},
            is_mesh_uplink=True,
        )
    assert writer.call_count == calls_after_apply


async def test_revert_of_radio_config_without_live_state_is_refused(store):
    # A radio-config restore with no fresh live state read is refused rather than
    # restored blind -- never mutate on unverified state.
    writer = FakeControllerWriter()
    applier, change_id = await _apply_min_rssi_removal(store, writer)
    calls_after_apply = writer.call_count

    with pytest.raises(SafetyViolation):
        await applier.revert(change_id, current_radios=None)
    assert writer.call_count == calls_after_apply


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


async def test_min_rssi_removal_passes_the_rail(store, ap_device):
    # The genuine removal template must NOT trip the guard.
    finding = make_finding(
        "wifi.min_rssi_misconfig",
        radio_entity("ng"),
        evidence={"reason": "mesh_uplink_ap", "on_mesh_ap": True},
    )
    plan = plan_fix(finding, device=ap_device)
    writer = FakeControllerWriter()
    applier = Applier(store, writer)
    result = await applier.apply(
        plan,
        dry_run=False,
        confirm_token=plan_confirm_token(plan),
        current_state={f"{AP_MAC}:ng": {"min_rssi_enabled": True}},
    )
    assert result.applied is True
    assert writer.calls[0].body["radio_table"][0]["min_rssi_enabled"] is False


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
