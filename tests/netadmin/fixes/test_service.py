"""FixService: the full propose -> apply -> verify -> revert loop, fully offline.

Every controller seam is a fake: :class:`FakeDeviceReader` supplies the raw
device snapshot the planner needs, :class:`FakeControllerWriter` records the one
mutation an apply would send. Nothing here constructs a Real* seam, and a spy
proves it -- so the assertions double as a proof that no code path reached the
live controller. The store is a real migrated SQLite file seeded with a
``wifi.channel_plan`` issue on a 2.4 GHz radio (the canonical fixable finding).
"""

from __future__ import annotations

import pytest

from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType, FixState, IssueState
from netadmin.fixes import reader as reader_mod
from netadmin.fixes import writer as writer_mod
from netadmin.fixes.models import ConfirmTokenError, FixError, SafetyViolation, VerificationStatus
from netadmin.fixes.reader import FakeDeviceReader
from netadmin.fixes.service import FixService, IssueNotFound
from netadmin.fixes.writer import FakeControllerWriter
from netadmin.issues.engine import IssueEngine
from netadmin.issues.store_repository import StoreIssueRepository

from .conftest import AP_ID, AP_MAC, SW_MAC, make_ap_device, make_switch_device

pytestmark = pytest.mark.asyncio

NOW = 1_700_000_000


def _seed_channel_plan_issue(store, *, channel: int = 3) -> int:
    """An AP + its 2.4 GHz radio + an active off-grid channel_plan issue."""
    ap = store.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id=AP_MAC, name="Office AP"), ts=NOW
    )
    radio = store.upsert_entity(
        Entity(
            entity_type=EntityType.RADIO,
            native_id=f"{AP_MAC}:ng",
            name="Office AP ng",
            parent_id=ap,
            meta={"band": "ng"},
        ),
        ts=NOW,
    )
    return store.insert_issue(
        fingerprint="fp-channel",
        detector_key="wifi.channel_plan",
        severity="p3",
        state="active",
        first_seen_ts=NOW,
        last_seen_ts=NOW,
        title="2.4 GHz off 1/6/11 on Office AP",
        entity_id=radio,
        evidence={"subtype": "channel_off_grid", "band": "2.4", "channel": channel},
    )


def _service(store, *, reader=None, writer=None) -> FixService:
    engine = IssueEngine(StoreIssueRepository(store))
    return FixService(
        store,
        engine,
        device_reader=(
            reader if reader is not None else FakeDeviceReader({AP_MAC: make_ap_device()})
        ),
        writer=writer,
        now_fn=lambda: NOW,
    )


def _no_real_seams(monkeypatch) -> list[str]:
    """Spy that records any attempt to construct a real (networked) seam."""
    built: list[str] = []
    real_writer_init = writer_mod.RealControllerWriter.__init__
    real_reader_init = reader_mod.RealDeviceReader.__init__

    def _spy_writer(self, client):
        built.append("writer")
        return real_writer_init(self, client)

    def _spy_reader(self, client):
        built.append("reader")
        return real_reader_init(self, client)

    monkeypatch.setattr(writer_mod.RealControllerWriter, "__init__", _spy_writer)
    monkeypatch.setattr(reader_mod.RealDeviceReader, "__init__", _spy_reader)
    return built


# --------------------------------------------------------------------------- #
# Dry run
# --------------------------------------------------------------------------- #
async def test_dry_run_renders_exact_payload_and_sends_nothing(store, monkeypatch):
    built = _no_real_seams(monkeypatch)
    issue_id = _seed_channel_plan_issue(store)
    writer = FakeControllerWriter()
    svc = _service(store, writer=writer)

    dry = await svc.dry_run(issue_id)

    assert writer.call_count == 0  # nothing sent
    assert store.list_changes(issue_id=issue_id) == []  # nothing ledgered
    assert dry.rendered[0]["endpoint"] == f"rest/device/{AP_ID}"
    assert dry.rendered[0]["method"] == "PUT"
    # off-grid channel 3 -> nearest of 1/6/11 == 1, other radios preserved
    table = dry.rendered[0]["payload"]["radio_table"]
    assert next(r for r in table if r["radio"] == "ng")["channel"] == 1
    assert any(r["radio"] == "na" for r in table)  # 5 GHz radio untouched, still present
    assert dry.confirm_token
    assert built == []  # no real seam ever constructed


async def test_dry_run_unknown_issue_raises(store):
    svc = _service(store)
    with pytest.raises(IssueNotFound):
        await svc.dry_run(4242)


# --------------------------------------------------------------------------- #
# Apply: gated, ledgered, verification armed
# --------------------------------------------------------------------------- #
async def test_apply_full_lifecycle_through_fake_writer(store, monkeypatch):
    built = _no_real_seams(monkeypatch)
    issue_id = _seed_channel_plan_issue(store)
    writer = FakeControllerWriter()
    svc = _service(store, writer=writer)

    dry = await svc.dry_run(issue_id)
    result = await svc.apply(issue_id, confirm_token=dry.confirm_token)

    # One mutation, through the fake writer, carrying the retuned channel.
    assert result.applied is True
    assert writer.call_count == 1
    sent = writer.calls[0]
    assert sent.method == "PUT"
    assert sent.endpoint == f"rest/device/{AP_ID}"
    assert next(r for r in sent.body["radio_table"] if r["radio"] == "ng")["channel"] == 1

    # Ledgered with before/after and marked applied.
    changes = store.list_changes(issue_id=issue_id)
    assert len(changes) == 1
    assert changes[0]["status"] == "applied"

    # The issue's fix_state advanced and the trail records fix_applied.
    issue = store.get_issue(issue_id)
    assert issue["fix_state"] == FixState.APPLIED.value
    kinds = [e["kind"] for e in store.list_issue_events(issue_id)]
    assert "fix_applied" in kinds

    # Verification window is armed and pending.
    v = svc.verification(issue_id)
    assert v.status is VerificationStatus.PENDING
    assert v.armed_ts == NOW

    assert built == []  # still no real seam


async def test_apply_with_wrong_token_is_refused_and_sends_nothing(store):
    issue_id = _seed_channel_plan_issue(store)
    writer = FakeControllerWriter()
    svc = _service(store, writer=writer)
    with pytest.raises(ConfirmTokenError):
        await svc.apply(issue_id, confirm_token="not-the-token")
    assert writer.call_count == 0
    assert store.list_changes(issue_id=issue_id) == []


async def test_apply_then_resolve_inside_window_verifies_fix(store):
    issue_id = _seed_channel_plan_issue(store)
    writer = FakeControllerWriter()
    engine = IssueEngine(StoreIssueRepository(store))
    svc = FixService(
        store,
        engine,
        device_reader=FakeDeviceReader({AP_MAC: make_ap_device()}),
        writer=writer,
        now_fn=lambda: NOW,
    )
    dry = await svc.dry_run(issue_id)
    await svc.apply(issue_id, confirm_token=dry.confirm_token)

    # The detector stops firing: clear the fingerprint K times to resolve, still
    # inside the 48 h verification window -> the fix is VERIFIED.
    fp = store.get_issue(issue_id)["fingerprint"]
    for i in range(engine.cfg.k_for("wifi.channel_plan")):
        engine.process_cycle(NOW + 60 * (i + 1), cleared=[fp])

    issue = store.get_issue(issue_id)
    assert issue["state"] == IssueState.RESOLVED.value
    assert issue["fix_state"] == FixState.VERIFIED.value
    assert svc.verification(issue_id).status is VerificationStatus.VERIFIED


# --------------------------------------------------------------------------- #
# Revert
# --------------------------------------------------------------------------- #
async def test_revert_restores_before_state(store):
    issue_id = _seed_channel_plan_issue(store)
    writer = FakeControllerWriter()
    svc = _service(store, writer=writer)
    dry = await svc.dry_run(issue_id)
    result = await svc.apply(issue_id, confirm_token=dry.confirm_token)
    change_id = result.change_ids[0]

    write = await svc.revert(change_id)

    assert write.ok
    # The revert PUT carries the ORIGINAL 2.4 GHz channel (back to 3).
    last = writer.calls[-1]
    assert last.endpoint == f"rest/device/{AP_ID}"
    assert next(r for r in last.body["radio_table"] if r["radio"] == "ng")["channel"] == 3
    assert store.get_change(change_id)["status"] == "reverted"


def _seed_min_rssi_issue(store) -> int:
    """An AP + its 2.4 GHz radio + an active min-RSSI-misconfig issue."""
    ap = store.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id=AP_MAC, name="Office AP"), ts=NOW
    )
    radio = store.upsert_entity(
        Entity(
            entity_type=EntityType.RADIO,
            native_id=f"{AP_MAC}:ng",
            name="Office AP ng",
            parent_id=ap,
            meta={"band": "ng"},
        ),
        ts=NOW,
    )
    return store.insert_issue(
        fingerprint="fp-minrssi",
        detector_key="wifi.min_rssi_misconfig",
        severity="p2",
        state="active",
        first_seen_ts=NOW,
        last_seen_ts=NOW,
        title="min-RSSI set on Office AP",
        entity_id=radio,
        evidence={"reason": "mesh_uplink_ap", "on_mesh_ap": True},
    )


async def test_revert_refused_when_ap_is_now_a_mesh_uplink(store):
    # Apply a genuine min-RSSI removal, then the AP becomes a mesh uplink. Reverting
    # would re-enable min-RSSI on the mesh uplink -- the latent-outage case. The
    # service re-reads the (now mesh) device and the applier refuses; nothing is
    # sent beyond the original apply.
    issue_id = _seed_min_rssi_issue(store)
    writer = FakeControllerWriter()
    reader = FakeDeviceReader({AP_MAC: make_ap_device()})  # ng min_rssi_enabled=True
    svc = _service(store, reader=reader, writer=writer)

    dry = await svc.dry_run(issue_id)
    result = await svc.apply(issue_id, confirm_token=dry.confirm_token)
    assert result.applied is True
    change_id = result.change_ids[0]
    calls_after_apply = writer.call_count

    # The AP is now a wireless (mesh) uplink and min-RSSI has been removed (off).
    mesh_device = make_ap_device(
        radios=[
            {"radio": "ng", "channel": 3, "min_rssi_enabled": False, "min_rssi": 0},
            {"radio": "na", "channel": 36, "min_rssi_enabled": False, "min_rssi": 0},
        ]
    )
    mesh_device["uplink"] = {"type": "wireless"}
    reader.set_device(AP_MAC, mesh_device)

    with pytest.raises(SafetyViolation):
        await svc.revert(change_id)
    assert writer.call_count == calls_after_apply  # revert sent nothing
    assert store.get_change(change_id)["status"] != "reverted"


# --------------------------------------------------------------------------- #
# The site-scoped, per-band channel issue: one issue, several radios, one apply
# --------------------------------------------------------------------------- #
BAND_MACS = ("aa:bb:cc:00:00:11", "aa:bb:cc:00:00:12", "aa:bb:cc:00:00:13")


def _band_devices(channels: tuple[int, ...]) -> dict:
    return {
        mac: make_ap_device(
            device_id=f"dev-{i}",
            mac=mac,
            radios=[
                {"radio": "ng", "channel": channel, "ht": 20},
                {"radio": "na", "channel": 36, "ht": 80},
            ],
        )
        for i, (mac, channel) in enumerate(zip(BAND_MACS, channels))
    }


def _seed_band_conflict_issue(store, *, macs=BAND_MACS) -> int:
    """Three 2.4 GHz radios piled on channel 1, as ONE site-scoped issue.

    The issue carries a NULL ``entity_id`` -- the ``rf:2.4`` anchor is not a stored
    row -- which is exactly the shape the fix path has to cope with.
    """
    for i, mac in enumerate(macs):
        ap = store.upsert_entity(
            Entity(entity_type=EntityType.AP, native_id=mac, name=f"AP {i}"), ts=NOW
        )
        store.upsert_entity(
            Entity(
                entity_type=EntityType.RADIO,
                native_id=f"{mac}:ng",
                name=f"AP {i} ng",
                parent_id=ap,
                meta={"band": "ng"},
            ),
            ts=NOW,
        )
    return store.insert_issue(
        fingerprint="fp-band-24",
        detector_key="wifi.channel_plan",
        severity="p3",
        state="active",
        first_seen_ts=NOW,
        last_seen_ts=NOW,
        title="Avoidable co-channel reuse on 2.4 GHz: 3 radios on 1 channel",
        entity_id=None,
        evidence={
            "subtype": "co_channel_reuse",
            "band": "2.4",
            "conflict_groups": [
                {"channel": 1, "radios": [{"native_id": f"{mac}:ng"} for mac in macs]}
            ],
            "candidate_channels": [1, 6, 11],
            "per_channel": {"1": 3, "6": 0, "11": 0},
            "unused_candidates": [6, 11],
        },
    )


async def test_band_issue_reads_every_conflicted_device_and_plans_one_step_each(store):
    issue_id = _seed_band_conflict_issue(store)
    reader = FakeDeviceReader(_band_devices((1, 1, 1)))
    writer = FakeControllerWriter()
    svc = _service(store, reader=reader, writer=writer)

    dry = await svc.dry_run(issue_id)

    # One read per conflicted device, no writes.
    assert reader.calls == list(BAND_MACS)
    assert writer.call_count == 0
    # Two of the three radios move, onto the two free channels.
    assert len(dry.rendered) == 2
    targets = [
        next(r for r in call["payload"]["radio_table"] if r["radio"] == "ng")["channel"]
        for call in dry.rendered
    ]
    assert sorted(targets) == [6, 11]
    assert {call["endpoint"] for call in dry.rendered} == {"rest/device/dev-0", "rest/device/dev-1"}


async def test_band_issue_applies_every_step_and_ledgers_each_revertibly(store):
    issue_id = _seed_band_conflict_issue(store)
    writer = FakeControllerWriter()
    svc = _service(store, reader=FakeDeviceReader(_band_devices((1, 1, 1))), writer=writer)

    dry = await svc.dry_run(issue_id)
    result = await svc.apply(issue_id, confirm_token=dry.confirm_token)

    assert result.applied is True
    assert writer.call_count == 2
    assert len(result.change_ids) == 2
    changes = store.list_changes(issue_id=issue_id)
    assert [c["status"] for c in changes] == ["applied", "applied"]
    # Each change is pinned to its own radio and reverts on its own.
    assert len({c["entity_id"] for c in changes}) == 2
    write = await svc.revert(result.change_ids[0])
    assert write.ok
    assert store.get_change(result.change_ids[0])["status"] == "reverted"


async def test_band_issue_apply_refuses_when_one_device_drifted(store):
    issue_id = _seed_band_conflict_issue(store)
    reader = FakeDeviceReader(_band_devices((1, 1, 1)))
    writer = FakeControllerWriter()
    svc = _service(store, reader=reader, writer=writer)

    dry = await svc.dry_run(issue_id)
    # A human retunes one of the radios between the dry-run and the apply. The band
    # layout the joint plan was solved against no longer holds, so the rebuild at
    # apply time must refuse. It refuses as an advisory ("the band changed, it will
    # re-plan") rather than a token mismatch, because the planner now declines to
    # solve a joint move against a layout it knows is stale: solving anyway can
    # pick a destination that is now occupied and make the spread worse.
    reader.set_device(
        BAND_MACS[0],
        make_ap_device(
            device_id="dev-0",
            mac=BAND_MACS[0],
            radios=[{"radio": "ng", "channel": 11, "ht": 20}],
        ),
    )
    result = await svc.apply(issue_id, confirm_token=dry.confirm_token)

    # The invariants that actually matter: nothing was applied, the controller was
    # never written to, and no change was ledgered.
    assert result.applied is False
    assert result.aborted_reason == "manual_action_required"
    assert writer.call_count == 0
    assert store.list_changes(issue_id=issue_id) == []


async def test_band_issue_stops_at_the_failed_step_and_keeps_prior_change_ids(store):
    issue_id = _seed_band_conflict_issue(store)
    # The second device's PUT fails; the first has already landed and is ledgered.
    writer = FakeControllerWriter(fail_on={"PUT rest/device/dev-1"})
    svc = _service(store, reader=FakeDeviceReader(_band_devices((1, 1, 1))), writer=writer)

    dry = await svc.dry_run(issue_id)
    result = await svc.apply(issue_id, confirm_token=dry.confirm_token)

    assert result.applied is False
    assert result.aborted_reason == "step_failed"
    assert len(result.change_ids) == 2  # both rows exist: one applied, one failed
    statuses = [c["status"] for c in store.list_changes(issue_id=issue_id)]
    assert sorted(statuses) == ["applied", "failed"]
    # A failed apply never arms verification.
    # A partially applied plan DID change the network, so verification must be
    # armed. This previously asserted NOT_ARMED, which encoded the defect: a real,
    # ledgered controller change that nothing was watching.
    assert svc.verification(issue_id).status is VerificationStatus.PENDING


async def test_port_issue_still_reads_its_own_switch_for_the_precondition(store):
    """Entity-scoped findings keep reading exactly their own device: the port's
    live PoE mode is what the plan's precondition is built from."""
    sw = store.upsert_entity(
        Entity(entity_type=EntityType.SWITCH, native_id=SW_MAC, name="Closet switch"), ts=NOW
    )
    port = store.upsert_entity(
        Entity(entity_type=EntityType.PORT, native_id=f"{SW_MAC}:5", parent_id=sw), ts=NOW
    )
    issue_id = store.insert_issue(
        fingerprint="fp-poe",
        detector_key="wired.port_flapping",
        severity="p2",
        state="active",
        first_seen_ts=NOW,
        last_seen_ts=NOW,
        title="port 5 is rebooting in a loop",
        entity_id=port,
        evidence={"poe_reboot_loop": True},
    )
    reader = FakeDeviceReader({SW_MAC: make_switch_device()})
    svc = _service(store, reader=reader, writer=FakeControllerWriter())

    dry = await svc.dry_run(issue_id)

    assert reader.calls == [SW_MAC]
    assert dry.rendered[0]["precondition"]["expected"] == {"poe_mode": "auto"}


async def test_site_scoped_issue_without_a_band_is_refused_cleanly(store):
    issue_id = store.insert_issue(
        fingerprint="fp-anon",
        detector_key="wifi.channel_plan",
        severity="p3",
        state="active",
        first_seen_ts=NOW,
        last_seen_ts=NOW,
        title="no entity, no band",
        entity_id=None,
        evidence={"subtype": "co_channel_reuse"},
    )
    svc = _service(store)
    with pytest.raises(FixError):
        await svc.dry_run(issue_id)


# --------------------------------------------------------------------------- #
# Advisory (physical) issue: no automatic fix, no controller contact
# --------------------------------------------------------------------------- #
async def test_advisory_issue_plans_manual_action_only(store, monkeypatch):
    built = _no_real_seams(monkeypatch)
    ap = store.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id=AP_MAC, name="Office AP"), ts=NOW
    )
    port = store.upsert_entity(
        Entity(entity_type=EntityType.PORT, native_id=f"{AP_MAC}:5", parent_id=ap), ts=NOW
    )
    issue_id = store.insert_issue(
        fingerprint="fp-cable",
        detector_key="wired.bad_cable",
        severity="p2",
        state="active",
        first_seen_ts=NOW,
        last_seen_ts=NOW,
        title="rx_errors climbing on port 5",
        entity_id=port,
        evidence={"rx_errors_per_min": 42},
    )
    reader = FakeDeviceReader({AP_MAC: make_ap_device()})
    writer = FakeControllerWriter()
    svc = _service(store, reader=reader, writer=writer)

    dry = await svc.dry_run(issue_id)

    assert dry.manual_action_required is True
    assert dry.advisory
    assert dry.rendered == []
    # A physical fix reads no device and sends nothing.
    assert reader.calls == []
    assert writer.call_count == 0
    assert built == []


async def test_partial_apply_still_arms_verification(store):
    """A step that landed must be verified even though later steps failed.

    Keying arming off ``applied`` meant a multi-step plan that wrote one radio and
    then failed left a real, ledgered controller change that nothing was watching.
    """
    issue_id = _seed_band_conflict_issue(store)
    writer = FakeControllerWriter(fail_on={"PUT rest/device/dev-1"})
    svc = _service(store, reader=FakeDeviceReader(_band_devices((1, 1, 1))), writer=writer)

    dry = await svc.dry_run(issue_id)
    result = await svc.apply(issue_id, confirm_token=dry.confirm_token)

    assert result.applied is False  # the plan did not complete
    assert result.change_ids  # but the network WAS changed
    v = svc.verification(issue_id)
    assert v.status is VerificationStatus.PENDING, "a partial apply must still arm"
    assert v.armed_ts == NOW
