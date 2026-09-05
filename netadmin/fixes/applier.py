"""The fix applier (``docs/ARCHITECTURE.md`` section 9).

The one component that can change the controller -- and it is built so that the
default, and every untrusted path, changes nothing. ``apply(plan)`` with the
default ``dry_run=True`` renders the exact payloads and returns them without ever
reaching a :class:`~netadmin.fixes.writer.ControllerWriter`; no socket, no ledger
row. A real apply is gated behind, in order:

#. an advisory plan (no steps) short-circuits -- there is nothing to send;
#. ``dry_run=False`` **and** a ``confirm_token`` matching a digest of this exact
   plan (:func:`~netadmin.fixes.models.plan_confirm_token`) -- proof the human
   confirmed the bytes they reviewed;
#. an injected writer -- absent it, we refuse rather than improvise;
#. the max-N-steps / max-N-devices guard;
#. the absolute min-RSSI rail: a step may only *remove* min-RSSI, never set it;
#. the revertibility rail: the applier itself re-derives whether every step is
   *genuinely* revertible under the current contract (a restorable before-state
   whose replay the min-RSSI rail would not itself refuse) and refuses to send a
   one-way/irreversible write, regardless of the planner's ``revertible`` flag;
#. a precondition re-check of **every** step against freshly read live state --
   any drift aborts the whole plan before a single call goes out. An empty
   precondition is only "satisfied" when the target was actually read; a target
   missing from the fresh snapshot is drift, never a free pass.

Only past all gates does it, per step: resolve the entity, write the before-state to
the ``changes`` ledger, send through the writer, and mark the row applied/failed.
Before-state is captured first so a revert is always possible; a step's failure
stops the plan rather than pressing on mutating. Apply and revert are serialized
per target device so two operations on the same device can never interleave.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Iterable, Mapping, Optional

from netadmin.fixes.models import (
    ApplyResult,
    ConfirmTokenError,
    DryRunResult,
    FixError,
    FixPlan,
    FixStep,
    MaxStepsExceeded,
    PreconditionDrift,
    RiskLevel,
    SafetyViolation,
    StepResult,
    WriteResult,
    WriterRequired,
    plan_confirm_token,
)
from netadmin.fixes.writer import ControllerWriter
from netadmin.logging import get_logger
from netadmin.store.repository import Repository

__all__ = ["Applier", "DEFAULT_MAX_STEPS", "DEFAULT_MAX_DEVICES"]

_log = get_logger("fixes.applier")

DEFAULT_MAX_STEPS = 4
DEFAULT_MAX_DEVICES = 3

# Interim ledger status: written before the send, flipped to a terminal
# applied/failed after. Not one of the doc's terminal states on purpose -- a row
# still reading "applying" means the process died mid-send and the outcome is
# unconfirmed, which must never be mistaken for a completed change.
_STATUS_APPLYING = "applying"
_STATUS_APPLIED = "applied"
_STATUS_FAILED = "failed"
_STATUS_REVERTED = "reverted"


class Applier:
    """Renders, gates, and (only when fully authorized) applies a :class:`FixPlan`.

    ``writer`` is the single mutation seam; leave it ``None`` for a render-only
    instance -- a dry run needs no writer, and constructing one signals intent to
    mutate. ``store`` is the repository that owns the ``changes`` ledger.
    """

    def __init__(
        self,
        store: Repository,
        writer: Optional[ControllerWriter] = None,
        *,
        max_steps: int = DEFAULT_MAX_STEPS,
        max_devices: int = DEFAULT_MAX_DEVICES,
        now_fn: Optional[Callable[[], int]] = None,
    ) -> None:
        self._store = store
        self._writer = writer
        self.max_steps = max_steps
        self.max_devices = max_devices
        self._now_fn = now_fn or (lambda: int(time.time()))
        # Per-device locks so a plan's apply and any revert on the same device are
        # serialized -- concurrent mutations on one device must never interleave
        # (C1). Keyed by the device id parsed from the endpoint.
        self._device_locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------ #
    # Dry run (default) -- pure render, no writer, no ledger
    # ------------------------------------------------------------------ #
    def render(self, plan: FixPlan) -> DryRunResult:
        """Render the exact calls a real apply would send. Touches no network.

        Returns the per-step ``{method, endpoint, payload, ...}`` list and the
        ``confirm_token`` a subsequent real apply must present. Deliberately never
        references ``self._writer`` -- a dry run cannot reach the mutation seam.
        """
        rendered = [
            {
                "action": s.action.value,
                "method": s.method,
                "endpoint": s.endpoint,
                "payload": s.payload,
                "description": s.description,
                "risk": s.risk.value if isinstance(s.risk, RiskLevel) else str(s.risk),
                "target": s.target_native_id,
                "precondition": {
                    "target": s.precondition.target_native_id,
                    "expected": s.precondition.expected,
                    "description": s.precondition.description,
                },
                "revertible": s.revertible,
            }
            for s in plan.steps
        ]
        return DryRunResult(
            plan=plan,
            rendered=rendered,
            confirm_token=plan_confirm_token(plan),
            manual_action_required=plan.manual_action_required,
            advisory=plan.advisory,
        )

    # ------------------------------------------------------------------ #
    # Apply
    # ------------------------------------------------------------------ #
    async def apply(
        self,
        plan: FixPlan,
        *,
        dry_run: bool = True,
        confirm_token: Optional[str] = None,
        current_state: Optional[Mapping[str, Mapping[str, Any]]] = None,
        now: Optional[int] = None,
    ):
        """Dry-run render (default) or, fully gated, a real apply.

        With ``dry_run=True`` (the default) returns a :class:`DryRunResult` and
        sends nothing. With ``dry_run=False`` runs the six-gate sequence in the
        module docstring and returns an :class:`ApplyResult`. ``current_state`` maps
        each step's precondition target native id to a flat ``{attr: value}`` of the
        freshly read live values; it is what the precondition re-check compares
        against.
        """
        if dry_run:
            # The ONLY thing a dry run does: render. No writer reference exists on
            # this path, so no RealControllerWriter can be reached from here.
            return self.render(plan)

        return await self._apply_real(
            plan,
            confirm_token=confirm_token,
            current_state=current_state or {},
            now=self._now_fn() if now is None else now,
        )

    async def _apply_real(
        self,
        plan: FixPlan,
        *,
        confirm_token: Optional[str],
        current_state: Mapping[str, Mapping[str, Any]],
        now: int,
    ) -> ApplyResult:
        # Gate 1: an advisory plan has nothing to apply.
        if plan.is_advisory:
            return ApplyResult(plan=plan, applied=False, aborted_reason="manual_action_required")

        # Gate 2: confirmation must match the exact rendered plan.
        expected_token = plan_confirm_token(plan)
        if confirm_token is None or confirm_token != expected_token:
            raise ConfirmTokenError(
                "apply requires a confirm_token matching the dry-run render of this exact plan"
            )

        # Gate 3: a real apply needs the mutation seam.
        if self._writer is None:
            raise WriterRequired("apply requires an injected ControllerWriter")

        # Gate 4: the max-N guard.
        if len(plan.steps) > self.max_steps:
            raise MaxStepsExceeded(f"plan has {len(plan.steps)} steps; max is {self.max_steps}")
        if plan.device_count > self.max_devices:
            raise MaxStepsExceeded(
                f"plan touches {plan.device_count} devices; max is {self.max_devices}"
            )

        # Gate 5: the absolute min-RSSI rail.
        self._assert_min_rssi_safe(plan)

        # Gate 5b: the revertibility rail. The applier does not trust the planner's
        # ``revertible`` flag -- it re-derives, per step, whether a genuine revert
        # exists under the current contract, and refuses a one-way/irreversible
        # write outright. Nothing that cannot be undone is ever applied.
        self._assert_revertible(plan)

        # Gate 6: precondition re-check of every step -- any drift aborts the whole
        # plan before a single call is sent.
        drifted = self._precondition_drift(plan, current_state)
        if drifted:
            raise PreconditionDrift(
                f"{len(drifted)} step(s) drifted from expected state; plan aborted", drifted
            )

        results: list[StepResult] = []
        change_ids: list[int] = []
        applied_all = True
        # Serialize the whole send loop against any other apply/revert touching the
        # same device(s), so concurrent operations cannot interleave writes.
        async with self._serialize(_endpoint_device(s.endpoint) for s in plan.steps):
            for step in plan.steps:
                change_id = self._record_before(plan, step, now)
                change_ids.append(change_id)
                try:
                    write = await self._dispatch(step)
                except Exception as exc:  # noqa: BLE001 - a transport failure is a step failure
                    self._store.update_change_status(change_id, _STATUS_FAILED)
                    results.append(StepResult(step, _STATUS_FAILED, change_id, None, str(exc)))
                    applied_all = False
                    _log.warning("fix step raised, stopping plan: %s", exc)
                    break

                if write.ok:
                    self._store.update_change_status(change_id, _STATUS_APPLIED)
                    results.append(StepResult(step, _STATUS_APPLIED, change_id, write))
                else:
                    self._store.update_change_status(change_id, _STATUS_FAILED)
                    results.append(
                        StepResult(
                            step, _STATUS_FAILED, change_id, write, "controller returned non-2xx"
                        )
                    )
                    applied_all = False
                    _log.warning("fix step failed (status=%s), stopping plan", write.status_code)
                    break

        return ApplyResult(
            plan=plan,
            applied=applied_all,
            steps=results,
            change_ids=change_ids,
            aborted_reason=None if applied_all else "step_failed",
        )

    # ------------------------------------------------------------------ #
    # Revert -- re-apply a stored before-state
    # ------------------------------------------------------------------ #
    async def revert(
        self,
        change_id: int,
        *,
        current_radios: Optional[Mapping[str, Mapping[str, Any]]] = None,
        is_mesh_uplink: bool = False,
        now: Optional[int] = None,
    ) -> WriteResult:
        """Restore a change's captured before-state through the writer -- re-gated.

        Reads the ledger row, replays its stored ``before`` call (the pre-change
        ``radio_table`` PUT), and marks the row ``reverted`` on success. A
        non-revertible change (a transient command like a power-cycle stores no
        before-body) or an already-reverted row is refused; a writer is required,
        exactly as for a forward apply.

        A revert is a mutation, so it passes the same absolute min-RSSI rail a
        forward apply does -- restoring a min-RSSI *removal* would re-enable
        min-RSSI, violating the "only ever removed, never set" invariant, and doing
        so blindly on an AP that has since become a mesh uplink re-creates the exact
        latent outage the detector guards against. Any restore that touches
        ``radio_table`` is therefore re-checked against freshly read live state:
        ``current_radios`` maps radio band code -> ``{attr: value}`` from a live
        read, and ``is_mesh_uplink`` is that device's current uplink posture. When a
        radio restore is requested without fresh state (the device could not be
        read) we refuse rather than restore blind -- never mutate on unverified
        state, exactly as the forward precondition re-check does.

        The restore is built **fresh** from current live state (C1): only the
        fields the original change actually touched are rolled back, layered on top
        of every other field's current live value, so a revert can never clobber
        unrelated work applied to the device since. If a touched field has drifted
        to a third value (someone changed the very thing this change set), the
        revert refuses with :class:`PreconditionDrift` and the operator must
        re-approve the resulting payload. The send is serialized per device against
        any concurrent apply/revert.
        """
        now = self._now_fn() if now is None else now
        row = self._store.get_change(change_id)
        if row is None:
            raise FixError(f"no change with id {change_id}")
        if row["status"] == _STATUS_REVERTED:
            raise FixError(f"change {change_id} already reverted")

        before = json.loads(row["before_json"]) if row["before_json"] else {}
        after = json.loads(row["after_json"]) if row["after_json"] else {}
        body = before.get("body") if isinstance(before, dict) else None
        endpoint = before.get("endpoint") if isinstance(before, dict) else None
        if not body or not endpoint:
            raise FixError(f"change {change_id} is not revertible (no stored before-state)")
        if self._writer is None:
            raise WriterRequired("revert requires an injected ControllerWriter")

        # Re-gate a radio-config restore against fresh live state before sending.
        restore_radios = body.get("radio_table") if isinstance(body, dict) else None
        restore_body: Mapping[str, Any] = body
        if restore_radios:
            if current_radios is None:
                raise SafetyViolation(
                    f"revert of change {change_id} touches radio config but no fresh live "
                    "state was read; refusing to restore on unverified state"
                )
            after_body = after.get("body") if isinstance(after, dict) else {}
            fresh_table = self._fresh_restore_table(
                change_id, body, after_body if isinstance(after_body, dict) else {}, current_radios
            )
            self._assert_revert_min_rssi_safe(
                change_id, fresh_table, current_radios, is_mesh_uplink
            )
            restore_body = {"radio_table": fresh_table}

        method = str(before.get("method") or "PUT")
        async with self._serialize([_endpoint_device(str(endpoint))]):
            write = await self._dispatch_raw(method, str(endpoint), restore_body)
            if write.ok:
                self._store.update_change_status(change_id, _STATUS_REVERTED, reverted_ts=now)
            else:
                _log.warning(
                    "revert of change %s failed (status=%s)", change_id, write.status_code
                )
        return write

    @staticmethod
    def _fresh_restore_table(
        change_id: int,
        before_body: Mapping[str, Any],
        after_body: Mapping[str, Any],
        current_radios: Mapping[str, Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build a revert ``radio_table`` that rolls back only the touched fields.

        The original change's touched fields are those that differ between its
        stored ``before`` and ``after`` bodies, per radio. The restore is the
        *current live* table with only those fields set back to their before-value;
        every other field keeps its current live value, so work applied to the
        device after the original change survives the revert. A touched field whose
        live value is neither the value we set nor the value we would restore has
        been changed by someone else -- that is a conflict, and we refuse rather
        than silently overwrite it.
        """
        before_radios = {
            r.get("radio"): r for r in (before_body.get("radio_table") or []) if isinstance(r, dict)
        }
        after_radios = {
            r.get("radio"): r for r in (after_body.get("radio_table") or []) if isinstance(r, dict)
        }
        touched: dict[Any, set[str]] = {}
        for radio, aentry in after_radios.items():
            bentry = before_radios.get(radio, {})
            fields = {k for k, av in aentry.items() if bentry.get(k) != av}
            if fields:
                touched[radio] = fields

        conflicts: list[str] = []
        fresh: list[dict[str, Any]] = []
        for radio_code, live_entry in current_radios.items():
            entry = dict(live_entry)
            for field in touched.get(radio_code, set()):
                before_val = before_radios.get(radio_code, {}).get(field)
                after_val = after_radios.get(radio_code, {}).get(field)
                if field not in live_entry:
                    conflicts.append(f"{radio_code}.{field} could not be read from live state")
                    continue
                live_val = live_entry.get(field)
                if live_val != after_val and live_val != before_val:
                    conflicts.append(
                        f"{radio_code}.{field} is now {live_val!r}, not the {after_val!r} "
                        "this change set"
                    )
                    continue
                entry[field] = before_val
            fresh.append(entry)

        for radio_code in touched:
            if radio_code not in current_radios:
                conflicts.append(f"radio '{radio_code}' is no longer present in live state")

        if conflicts:
            raise PreconditionDrift(
                f"revert of change {change_id} conflicts with newer live state "
                f"({'; '.join(conflicts)}); re-open the fix plan and re-approve the payload"
            )
        return fresh

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _record_before(self, plan: FixPlan, step: FixStep, now: int) -> int:
        """Insert the before-state ledger row (interim status) prior to sending."""
        entity_id = None
        row = self._store.find_entity(step.target_entity_type, step.target_native_id)
        if row is not None:
            entity_id = int(row["entity_id"])
        return self._store.insert_change(
            action=step.action.value,
            before=step.before or {},
            after=step.after or {},
            status=_STATUS_APPLYING,
            ts=now,
            issue_id=plan.issue_id,
            entity_id=entity_id,
        )

    async def _dispatch(self, step: FixStep) -> WriteResult:
        return await self._dispatch_raw(step.method, step.endpoint, step.payload)

    async def _dispatch_raw(
        self, method: str, endpoint: str, body: Mapping[str, Any]
    ) -> WriteResult:
        assert self._writer is not None  # gated by callers
        upper = method.upper()
        if upper == "PUT":
            return await self._writer.put(endpoint, body)
        if upper == "POST":
            return await self._writer.post(endpoint, body)
        raise SafetyViolation(f"unsupported mutation method: {method}")

    def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._device_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._device_locks[key] = lock
        return lock

    @asynccontextmanager
    async def _serialize(self, keys: Iterable[str]) -> AsyncIterator[None]:
        """Hold the per-device lock(s) for ``keys`` for the duration of a mutation.

        Locks are acquired in a stable sorted order so two operations touching the
        same set of devices can never deadlock, and released in reverse. This is
        what serializes a plan's apply against a concurrent revert on the same
        device so their writes cannot interleave (C1).
        """
        ordered = sorted({k for k in keys if k})
        locks = [self._lock_for(k) for k in ordered]
        for lock in locks:
            await lock.acquire()
        try:
            yield
        finally:
            for lock in reversed(locks):
                lock.release()

    def _assert_revertible(self, plan: FixPlan) -> None:
        """Refuse to apply any step that is not genuinely revertible.

        The applier does not trust ``step.revertible``: it re-derives the answer
        from the step itself. A step is genuinely revertible only if it carries a
        restorable before-state (a ``body`` and ``endpoint`` to replay) *and*
        replaying that before-state would not itself be refused by the min-RSSI
        rail. A transient command (a PoE power-cycle, ``before=None``) and a
        min-RSSI removal (whose revert would re-enable min-RSSI, which the rail
        forbids) are both one-way, so they are refused here -- they belong in an
        advisory plan, surfaced as a recommendation, not executed as an
        irreversible controller write.
        """
        for step in plan.steps:
            if not self._step_genuinely_revertible(step):
                raise SafetyViolation(
                    f"step '{step.description}' is not genuinely revertible under the "
                    "current contract (no restorable before-state, or its revert is barred "
                    "by the min-RSSI rail); refusing to apply a one-way change"
                )

    @staticmethod
    def _step_genuinely_revertible(step: FixStep) -> bool:
        before = step.before
        if not isinstance(before, dict):
            return False
        body = before.get("body")
        endpoint = before.get("endpoint")
        if not body or not endpoint:
            return False
        # Would replaying `before` re-enable or tighten min-RSSI relative to the
        # state this step establishes (its payload)? If so, the revert would be
        # refused by the rail, so the step is not genuinely revertible.
        before_radios = {
            r.get("radio"): r for r in (body.get("radio_table") or []) if isinstance(r, dict)
        }
        payload_radios = {
            r.get("radio"): r
            for r in ((step.payload or {}).get("radio_table") or [])
            if isinstance(r, dict)
        }
        for radio, bentry in before_radios.items():
            if not _truthy(bentry.get("min_rssi_enabled")):
                continue
            pentry = payload_radios.get(radio, {})
            if not _truthy(pentry.get("min_rssi_enabled")):
                return False  # revert would re-enable min-RSSI
            bv, pv = bentry.get("min_rssi"), pentry.get("min_rssi")
            if isinstance(bv, (int, float)) and isinstance(pv, (int, float)) and bv > pv:
                return False  # revert would tighten an already-set floor
        return True

    def _precondition_drift(
        self, plan: FixPlan, current_state: Mapping[str, Mapping[str, Any]]
    ) -> list[FixStep]:
        """Return the steps whose expected state no longer matches live state.

        A target absent from ``current_state`` (we could not read it) counts as
        drift: we never mutate on unverified state. Crucially this holds even when
        ``expected`` is empty -- an empty precondition is "satisfied" only once the
        target has actually been read. A step that reached the applier with an empty
        precondition *because its snapshot was missing* must never sail through on
        that emptiness; only a target that IS present with nothing left to assert
        genuinely passes.
        """
        drifted: list[FixStep] = []
        for step in plan.steps:
            expected = step.precondition.expected
            live = current_state.get(step.precondition.target_native_id)
            if live is None:
                # Missing snapshot -> drift, whether or not there was anything to
                # assert. An empty {} precondition is not a free pass.
                drifted.append(step)
                continue
            if not expected:
                continue  # read successfully, nothing left to assert -> satisfied
            # A key the live read could not produce is drift, not a match: an
            # expected value of None (a radio_table entry with no channel key)
            # must never be satisfied by the empty extract of a radio that has
            # vanished from the device entirely.
            if any(k not in live or live[k] != v for k, v in expected.items()):
                drifted.append(step)
        return drifted

    @staticmethod
    def _assert_min_rssi_safe(plan: FixPlan) -> None:
        """Absolute rail: no step may enable or tighten min-RSSI.

        Compares each step's payload ``radio_table`` against its captured before-
        state. Enabling min-RSSI where it was off, or lowering (making more
        negative-strict, i.e. a numerically larger) an already-set floor, is
        refused outright -- regardless of the step's declared action. Removal
        (disabling) always passes. This holds even against a hand-forged plan.
        """
        for step in plan.steps:
            payload_radios = step.payload.get("radio_table") if step.payload else None
            if not payload_radios:
                continue
            before_body = (step.before or {}).get("body", {}) if step.before else {}
            before_radios = {r.get("radio"): r for r in (before_body.get("radio_table") or [])}
            for entry in payload_radios:
                radio = entry.get("radio")
                new_enabled = _truthy(entry.get("min_rssi_enabled"))
                old = before_radios.get(radio, {})
                old_enabled = _truthy(old.get("min_rssi_enabled"))
                if new_enabled and not old_enabled:
                    raise SafetyViolation(
                        f"step would ENABLE min-RSSI on radio '{radio}'; only removal is allowed"
                    )
                if new_enabled and old_enabled:
                    new_v, old_v = entry.get("min_rssi"), old.get("min_rssi")
                    if isinstance(new_v, (int, float)) and isinstance(old_v, (int, float)):
                        if new_v > old_v:
                            raise SafetyViolation(
                                f"step would tighten min-RSSI on radio '{radio}'; refused"
                            )

    @staticmethod
    def _assert_revert_min_rssi_safe(
        change_id: int,
        restore_radios: list[Mapping[str, Any]],
        current_radios: Mapping[str, Mapping[str, Any]],
        is_mesh_uplink: bool,
    ) -> None:
        """The min-RSSI rail applied to a revert's restore body vs fresh live state.

        The forward rail forbids *ever* enabling or tightening min-RSSI; a revert
        must not become a back door around it. For each radio the restore would
        write:

        * restoring min-RSSI *off* is always safe -- that is itself a removal;
        * on a device that is now a mesh uplink, restoring min-RSSI *on* is refused
          outright (mesh min-RSSI is removal-only -- kicking the uplink is a latent
          outage), regardless of the prior live value;
        * otherwise, enabling min-RSSI where it is currently off, or tightening an
          already-set floor (a numerically larger, stricter value), is refused --
          the same invariant the forward apply enforces.
        """
        for entry in restore_radios:
            radio = entry.get("radio")
            restore_enabled = _truthy(entry.get("min_rssi_enabled"))
            if not restore_enabled:
                continue  # restoring min-RSSI off is a removal -- always allowed
            if is_mesh_uplink:
                raise SafetyViolation(
                    f"revert of change {change_id} would ENABLE min-RSSI on radio "
                    f"'{radio}' of an AP that is now a mesh uplink; refused "
                    "(mesh min-RSSI is removal-only)"
                )
            live = current_radios.get(radio, {})
            live_enabled = _truthy(live.get("min_rssi_enabled"))
            if not live_enabled:
                raise SafetyViolation(
                    f"revert of change {change_id} would ENABLE min-RSSI on radio "
                    f"'{radio}' where it is currently off; only removal is allowed"
                )
            new_v, live_v = entry.get("min_rssi"), live.get("min_rssi")
            if isinstance(new_v, (int, float)) and isinstance(live_v, (int, float)):
                if new_v > live_v:
                    raise SafetyViolation(
                        f"revert of change {change_id} would tighten min-RSSI on radio "
                        f"'{radio}'; refused"
                    )


def _endpoint_device(endpoint: str) -> str:
    """The device id a site-relative endpoint mutates, used as the serialize key.

    ``rest/device/<id>`` -> ``<id>``. Anything else (e.g. ``cmd/devmgr``) has no
    stable device id in the path, so the endpoint itself is the key -- still a
    correct, if coarser, serialization boundary.
    """
    parts = str(endpoint).strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "rest" and parts[1] == "device":
        return parts[2]
    return str(endpoint)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False
