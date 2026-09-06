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
#. a whole-``radio_table`` clobber guard: because a ``rest/device`` PUT replaces the
   entire table, the payload re-sends every field the fix did *not* target at its
   snapshot value; if any such field has diverged from live since, sending it would
   silently overwrite a concurrent change, so that too is drift.

The last two gates are the *binding*, live-state-dependent validation, and they run
**inside** the per-device lock against state read after the lock is held -- the
read -> validate -> write is one atomic critical section per device. Only past all
gates does it, per step: resolve the entity, write the before-state to the
``changes`` ledger, send through the writer, and mark the row applied/failed.
Before-state is captured first so a revert is always possible; a step's failure
stops the plan rather than pressing on mutating. Apply and revert are serialized per
target device so two operations on the same device can never interleave: a second
apply validates against the first's committed write (never a stale pre-lock
snapshot), and a second revert re-reads the row's status under the lock and refuses
once the first has marked it reverted, so a mutation is never replayed.
"""

from __future__ import annotations

import asyncio
import json
import time
import weakref
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
# Terminal-but-uncertain: the mutation was dispatched exactly once and its outcome
# is unknown (a lost response, or a 401 that may have landed after acceptance). It
# is NOT "failed" -- the write may have taken -- so it is never mistaken for either
# a clean failure or a confirmed apply, and the operator reconciles via a GET (C2).
_STATUS_UNKNOWN = "unknown"


# Process-wide per-device lock registry (C1). Two request-scoped appliers in the
# same process each build their own :class:`Applier`, so the serialization locks
# CANNOT live on the instance -- two independent locks serialize nothing. They live
# here, shared across every applier in the process, so a revert issued by one
# request serializes against an apply/revert issued by another. Keyed by
# ``(event-loop id, device key)``: an :class:`asyncio.Lock` is bound to the loop
# that created it, so a lock is never shared across event loops (each test's loop,
# the daemon's single long-lived loop).
#
# Lifecycle (P3): a stored :class:`asyncio.Lock`, once used, holds a strong
# reference to the loop that ran it, so leaving its entry in this dict after that
# loop closes would pin the (now dead) loop forever -- an unbounded leak of one
# loop per finished loop (each test loop, each ``asyncio.run``). To prevent that,
# :data:`_LOOP_REFS` tracks each loop-id by a *weak* reference, and
# :func:`_prune_dead_loops` -- run on every lock lookup -- drops the lock entries
# of any tracked loop that has closed or been collected (and any stale entry whose
# loop-id a new loop has since reused). The current, live loop is never pruned, so
# two live appliers on the same device+loop still share one lock.
_PROCESS_DEVICE_LOCKS: dict[tuple[int, str], "asyncio.Lock"] = {}
# loop-id -> weakref to that event loop, for closed/collected-loop pruning (P3).
_LOOP_REFS: dict[int, "weakref.ref[asyncio.AbstractEventLoop]"] = {}


def _prune_dead_loops(current_loop: "asyncio.AbstractEventLoop") -> None:
    """Drop lock entries for every tracked loop that is not the current live one and
    has closed or been garbage-collected (P3).

    A closed loop runs nothing, so removing its locks cannot interrupt an in-flight
    mutation; it merely releases the loop the retained lock would otherwise pin.
    Also clears a stale entry whose loop-id the *current* loop has reused (the old
    loop at that address is dead/closed), which would otherwise hand the new loop a
    lock bound to a defunct one.
    """
    for loop_id in list(_LOOP_REFS):
        ref = _LOOP_REFS.get(loop_id)
        loop = ref() if ref is not None else None
        if loop is current_loop:
            continue
        if loop is None or loop.is_closed():
            _LOOP_REFS.pop(loop_id, None)
            for key in [k for k in _PROCESS_DEVICE_LOCKS if k[0] == loop_id]:
                _PROCESS_DEVICE_LOCKS.pop(key, None)


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
        # Per-device serialization locks are PROCESS-wide, not per-instance (C1):
        # they live in the module-level registry so two request-scoped appliers
        # serialize against each other. See :func:`Applier._lock_for`.

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
        mesh_uplinks: Optional[set[str]] = None,
        state_reader: Optional[Callable[[], Any]] = None,
        now: Optional[int] = None,
    ):
        """Dry-run render (default) or, fully gated, a real apply.

        With ``dry_run=True`` (the default) returns a :class:`DryRunResult` and
        sends nothing. With ``dry_run=False`` runs the six-gate sequence in the
        module docstring and returns an :class:`ApplyResult`. ``current_state`` maps
        each step's precondition target native id to a flat ``{attr: value}`` of the
        freshly read live values; it is what the precondition re-check compares
        against. ``mesh_uplinks`` is the set of target device keys
        (:func:`_endpoint_device`) whose device is currently a mesh uplink -- fed to
        the revertibility gate so its reverse dry-run judges the min-RSSI rail
        against the AP's real mesh posture (S2).

        ``state_reader`` (C1 apply race) is the authoritative fresh-state source read
        **inside** the per-device lock: an async callable returning ``(current_state,
        full_state, mesh_uplinks)``, where ``full_state`` maps each target device key
        (:func:`_endpoint_device`) to ``{radio_code: {attr: value}}`` of its whole
        live ``radio_table``. When given, it overrides the passed ``current_state`` /
        ``mesh_uplinks`` -- the precondition re-check, the revertibility gate, and the
        whole-table clobber guard all run against state read after the lock is held,
        so a second apply on the same device sees the first's committed write and
        fails drift rather than overwriting it with its own stale snapshot. A caller
        that has already read state (no concurrency) may pass ``current_state`` /
        ``mesh_uplinks`` directly and omit ``state_reader``.
        """
        if dry_run:
            # The ONLY thing a dry run does: render. No writer reference exists on
            # this path, so no RealControllerWriter can be reached from here.
            return self.render(plan)

        return await self._apply_real(
            plan,
            confirm_token=confirm_token,
            current_state=current_state or {},
            mesh_uplinks=mesh_uplinks or set(),
            state_reader=state_reader,
            now=self._now_fn() if now is None else now,
        )

    async def _apply_real(
        self,
        plan: FixPlan,
        *,
        confirm_token: Optional[str],
        current_state: Mapping[str, Mapping[str, Any]],
        mesh_uplinks: set[str],
        state_reader: Optional[Callable[[], Any]],
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

        # Gate 5: the absolute min-RSSI rail. Payload-vs-before is plan-static, so it
        # is judged before the lock like the other invariants above.
        self._assert_min_rssi_safe(plan)

        results: list[StepResult] = []
        change_ids: list[int] = []
        applied_all = True
        full_state: Optional[Mapping[str, Mapping[str, Mapping[str, Any]]]] = None
        # Serialize the whole read -> validate -> write against any other apply/revert
        # touching the same device(s) (C1). The state-dependent gates (revertibility's
        # mesh posture, precondition drift, and the whole-table clobber guard) run
        # INSIDE this lock against state read after it is held, so a second apply on
        # the same device validates against the first's COMMITTED write instead of a
        # snapshot taken before either -- the fix for the apply-race clobber.
        async with self._serialize(_endpoint_device(s.endpoint) for s in plan.steps):
            # Authoritative fresh read under the lock. Reading before the lock (in
            # the caller) and validating that stale snapshot is exactly what let a
            # second apply overwrite the first's committed change; read it HERE.
            if state_reader is not None:
                current_state, full_state, mesh_uplinks = await state_reader()

            # Gate 5b: the revertibility rail, judged against the fresh mesh posture.
            # The applier does not trust the planner's ``revertible`` flag -- it
            # re-derives, per step, whether a genuine revert exists under the current
            # contract, and refuses a one-way/irreversible write outright.
            self._assert_revertible(plan, mesh_uplinks)

            # Gate 6: precondition re-check of every step against fresh live state --
            # any drift aborts the whole plan before a single call is sent.
            drifted = self._precondition_drift(plan, current_state)
            if drifted:
                raise PreconditionDrift(
                    f"{len(drifted)} step(s) drifted from expected state; plan aborted", drifted
                )

            # Gate 6b: whole-``radio_table`` clobber guard (C1). A ``rest/device`` PUT
            # replaces the entire radio_table, so the payload re-sends every UNTOUCHED
            # field at its snapshot value. If a concurrent apply committed a change to
            # one of those fields since the snapshot, re-sending it would silently undo
            # that change -- abort as drift. Only runs when full live state is
            # available (``state_reader`` supplied it); a caller that passed only the
            # narrow precondition ``current_state`` skips it (single-op, no race).
            if full_state is not None:
                self._assert_no_stale_overwrite(plan, full_state)

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
                elif _write_is_ambiguous(write):
                    # C2: the writer could not confirm the outcome (a lost response,
                    # or a 401 the write may have landed under). This is NOT a clean
                    # failure -- the change may be live -- so it is recorded as
                    # "unknown", never collapsed into generic "failed", and its
                    # ambiguity detail is preserved end to end for the API.
                    detail = _write_detail(write) or "mutation outcome unknown (ambiguous)"
                    self._store.update_change_status(change_id, _STATUS_UNKNOWN)
                    results.append(StepResult(step, _STATUS_UNKNOWN, change_id, write, detail))
                    applied_all = False
                    _log.warning("fix step outcome ambiguous, stopping plan: %s", detail)
                    break
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
        state_reader: Optional[Callable[[], Any]] = None,
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
        re-approve the resulting payload.

        The whole read-modify-write is **atomic** under the per-device lock (C1):
        the fresh live state is read INSIDE the lock, via ``state_reader`` (an async
        callable returning ``(current_radios, is_mesh_uplink)``), so two concurrent
        reverts on the same device cannot each build their restore against the same
        stale snapshot and have the second silently undo the first. Because the
        device lock is process-wide, this holds even across two request-scoped
        appliers. When no ``state_reader`` is given, the passed ``current_radios`` /
        ``is_mesh_uplink`` are used as-is (a caller that has already read state).
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

        method = str(before.get("method") or "PUT")
        async with self._serialize([_endpoint_device(str(endpoint))]):
            # Eligibility re-check UNDER the lock (P2): the status read before the lock
            # is stale the instant a concurrent revert of this same row commits. Two
            # reverts both saw 'applied' outside the lock, so both would dispatch --
            # replaying the mutation. Re-read the row now that we hold the device lock;
            # if a first revert already flipped it to 'reverted', refuse with no
            # dispatch. The check that gates the write must be under the same lock the
            # write is, not before it.
            fresh_row = self._store.get_change(change_id)
            if fresh_row is None:
                raise FixError(f"no change with id {change_id}")
            if fresh_row["status"] == _STATUS_REVERTED:
                raise FixError(f"change {change_id} already reverted")
            # Read-modify-write is atomic under the lock (C1): read fresh live state
            # HERE, not before acquiring it, so a concurrent revert's committed write
            # is visible and cannot be clobbered by a stale table.
            if state_reader is not None:
                current_radios, is_mesh_uplink = await state_reader()

            restore_radios = body.get("radio_table") if isinstance(body, dict) else None
            restore_body: Mapping[str, Any] = body
            if restore_radios:
                if current_radios is None:
                    raise SafetyViolation(
                        f"revert of change {change_id} touches radio config but no fresh live "
                        "state was read; refusing to restore on unverified state"
                    )
                after_body = after.get("body") if isinstance(after, dict) else {}
                # Defense in depth (verifier round 5): restore_body below carries ONLY
                # radio_table. If the recorded change ALSO modified another top-level
                # field (the apply-time gate now blocks creating such a change, but an
                # older ledger row or an out-of-band change might have one), reverting it
                # would silently leave that field changed while marking the row reverted.
                # Refuse rather than perform a partial, dishonest revert.
                if isinstance(after_body, dict):
                    non_revertible = sorted(
                        k
                        for k, v in after_body.items()
                        if k != "radio_table" and v != (body.get(k) if isinstance(body, dict) else None)
                    )
                    if non_revertible:
                        raise SafetyViolation(
                            f"revert of change {change_id} restores only radio_table but the "
                            f"change also modified {non_revertible}; refusing an incomplete revert"
                        )
                fresh_table = self._fresh_restore_table(
                    change_id,
                    body,
                    after_body if isinstance(after_body, dict) else {},
                    current_radios,
                )
                self._assert_revert_min_rssi_safe(
                    change_id, fresh_table, current_radios, is_mesh_uplink
                )
                restore_body = {"radio_table": fresh_table}

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
        """Insert the before-state ledger row (interim status) prior to sending.

        The stored ``after`` is the ACTUAL dispatched operation
        (``step.method``/``endpoint``/``payload``), never the planner-supplied
        ``step.after``. :meth:`revert` derives the fields to roll back from
        ``after`` vs ``before``; recording the real dispatch there means a forged or
        stale ``step.after`` (e.g. one set equal to ``before`` to disguise a change)
        can never make the later revert a no-op that re-sends the current value
        instead of restoring the original (S2). For every legitimate step
        ``step.after.body`` already equals ``step.payload``, so this is a no-op for
        them and only closes the forgery seam.
        """
        entity_id = None
        row = self._store.find_entity(step.target_entity_type, step.target_native_id)
        if row is not None:
            entity_id = int(row["entity_id"])
        dispatched_after = {
            "method": step.method,
            "endpoint": step.endpoint,
            "body": step.payload or {},
        }
        return self._store.insert_change(
            action=step.action.value,
            before=step.before or {},
            after=dispatched_after,
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
        """The PROCESS-wide lock for a device key, on the current event loop (C1).

        Shared across every :class:`Applier` in the process so two request-scoped
        appliers serialize against each other, and keyed by the running loop so a
        lock is never reused across event loops (each is bound to its creator loop).
        """
        loop = asyncio.get_running_loop()
        # Release the locks (and thus the loops) of any loop that has since closed,
        # so this registry never pins a dead loop (P3). Done here so a long-lived
        # process reclaims each finished loop as soon as any further mutation runs.
        _prune_dead_loops(loop)
        loop_id = id(loop)
        _LOOP_REFS.setdefault(loop_id, weakref.ref(loop))
        reg_key = (loop_id, key)
        lock = _PROCESS_DEVICE_LOCKS.get(reg_key)
        if lock is None:
            lock = asyncio.Lock()
            _PROCESS_DEVICE_LOCKS[reg_key] = lock
        return lock

    @asynccontextmanager
    async def _serialize(self, keys: Iterable[str]) -> AsyncIterator[None]:
        """Hold the per-device lock(s) for ``keys`` for the duration of a mutation.

        Locks are acquired in a stable sorted order so two operations touching the
        same set of devices can never deadlock, and released in reverse. This is
        what serializes a plan's apply against a concurrent revert on the same
        device so their writes cannot interleave (C1).

        Acquisition is inside try/finally over the locks acquired SO FAR: if a
        cancellation (or any exception) interrupts ``acquire`` while acquiring a
        later lock, every lock already held is released instead of being leaked --
        a leaked lock would hang every subsequent operation on that device forever.
        """
        ordered = sorted({k for k in keys if k})
        locks = [self._lock_for(k) for k in ordered]
        acquired: list[asyncio.Lock] = []
        try:
            for lock in locks:
                await lock.acquire()
                acquired.append(lock)
            yield
        finally:
            for lock in reversed(acquired):
                lock.release()

    def _assert_revertible(self, plan: FixPlan, mesh_uplinks: set[str]) -> None:
        """Refuse to apply any step whose real revert would be refused (S2).

        The applier does not trust ``step.revertible``, and it does not settle for a
        cheap proxy check either: it actually DERIVES the inverse operation and runs
        it through the very same rails :meth:`revert` would -- a dry-run of the
        reverse. A step is applied only if that reverse would pass; if the reverse
        would be rejected, the apply is refused *up front*, so a change whose revert
        the min-RSSI rail (or the restore builder) would later refuse is never
        applied in the first place.

        The reverse is dry-run against the state THIS apply establishes -- the
        step's own payload becomes the "current live" table (what a revert issued
        immediately after would read), and ``mesh_uplinks`` supplies the device's
        real mesh posture so the mesh min-RSSI prohibition is judged truthfully. A
        transient command (``before=None``, a PoE power-cycle) and any before-state
        that is not a restorable radio-config PUT are one-way and refused here -- a
        nonempty-but-irrelevant before-body (e.g. a ``cmd/devmgr`` body) does not
        make a step revertible.
        """
        for step in plan.steps:
            is_mesh = _endpoint_device(step.endpoint) in mesh_uplinks
            self._assert_step_reverse_ok(step, is_mesh)

    def _assert_step_reverse_ok(self, step: FixStep, is_mesh_uplink: bool) -> None:
        """Dry-run this step's reverse through the real revert rails; raise if refused.

        Revertibility is a property of THE DISPATCHED operation, not merely of some
        stored before-body. The gate derives the inverse from the step's own
        endpoint+method+payload and confirms it actually reverses THEM -- otherwise a
        step that dispatches a one-way command (a transient ``POST cmd/devmgr``
        power-cycle, say) but happens to carry an unrelated, valid-looking
        radio-restore before-body would sail through: its derived reverse table comes
        out empty (the dispatched payload has no ``radio_table`` to invert) and an
        empty reverse trivially passes every downstream rail.

        Crucially, the inverse is derived from the ACTUAL dispatched change, never
        from the claimed ``step.after``: the touched fields are those where the
        dispatched payload differs from the recorded ``before``, and the reverse
        restores exactly those to their before-values. A forged ``after == before``
        would otherwise empty the touched set and turn the "revert" into a no-op that
        re-sends the already-applied value (S2 round 3).

        Five things must hold: the dispatched op is itself a restorable radio-config
        PUT; the stored before restores that SAME endpoint; the dispatched payload
        actually changes at least one field vs ``before`` (a no-op apply has nothing
        to revert); the derived reverse is non-empty; and that reverse genuinely
        restores a before-value rather than merely re-sending the current one.
        """
        # (1) The dispatched op must itself be a restorable whole-``radio_table``
        # config PUT to ``rest/device/<id>``. This is the thing a revert would have
        # to invert; a transient command (``POST cmd/devmgr``) or any non-rest/device
        # write is one-way REGARDLESS of what before-body it carries -- tie the
        # judgement to endpoint+method+payload, never to "some before-body exists".
        dispatched_method = str(step.method or "").upper()
        dispatched_radios = (
            step.payload.get("radio_table") if isinstance(step.payload, Mapping) else None
        )
        if (
            dispatched_method != "PUT"
            or not _is_rest_device_endpoint(step.endpoint)
            or not dispatched_radios
        ):
            raise SafetyViolation(
                f"step '{step.description}' dispatches a "
                f"{dispatched_method or 'non-PUT'} to '{step.endpoint}' with no restorable "
                "radio config; a stored before-body cannot invert it -- refusing to apply "
                "a one-way change"
            )

        before = step.before
        if not isinstance(before, dict):
            raise SafetyViolation(
                f"step '{step.description}' has no restorable before-state; refusing to "
                "apply a one-way change"
            )
        body = before.get("body")
        endpoint = before.get("endpoint")
        if not body or not endpoint or not isinstance(body, dict):
            raise SafetyViolation(
                f"step '{step.description}' has no restorable before-state; refusing to "
                "apply a one-way change"
            )
        # A genuine revert is a whole-``radio_table`` config restore PUT to
        # ``rest/device/<id>``. A nonempty-but-irrelevant before-body (a transient
        # ``cmd/devmgr`` command body, a POST) restores no prior config and is NOT a
        # revert, however full it looks.
        method = str(before.get("method") or "PUT").upper()
        restore_radios = body.get("radio_table")
        if method != "PUT" or not _is_rest_device_endpoint(endpoint) or not restore_radios:
            raise SafetyViolation(
                f"step '{step.description}' before-state is not a restorable radio-config "
                "PUT; refusing to apply a change with no genuine revert"
            )
        # (2) The before must restore the SAME endpoint the apply mutates. A before
        # that targets a different device restores no prior config for THIS dispatch,
        # so it cannot make this step revertible.
        if str(endpoint) != str(step.endpoint):
            raise SafetyViolation(
                f"step '{step.description}' before-state restores '{endpoint}', not the "
                f"dispatched endpoint '{step.endpoint}'; it reverses a different op -- "
                "refusing to apply a change with no genuine revert"
            )
        # Dry-run the reverse exactly as :meth:`revert` builds and gates it, using
        # the payload this apply writes as the "current live" state a revert issued
        # right afterwards would read.
        current_radios = {
            str(r.get("radio")): dict(r)
            for r in (dispatched_radios or [])
            if isinstance(r, dict) and r.get("radio") is not None
        }
        before_radios = {
            str(r.get("radio")): r
            for r in (body.get("radio_table") or [])
            if isinstance(r, dict) and r.get("radio") is not None
        }
        # (3) Derive the inverse from the ACTUAL DISPATCHED change, never from the
        # claimed ``step.after``. The fields this apply MODIFIES are those where the
        # dispatched payload differs from the recorded ``before``; the revert must
        # restore exactly those to their before-values. Trusting ``step.after`` let a
        # forged ``after == before`` collapse the "touched" set to empty, so the
        # derived reverse re-sent the current (already-applied) value and restored
        # nothing -- e.g. apply channel 3 -> 1, then "revert" by sending 1 again
        # (S2 round 3). So the effective after IS the dispatched payload.
        touched: dict[str, dict[str, tuple[Any, Any]]] = {}
        for radio_code, disp_entry in current_radios.items():
            b = before_radios.get(radio_code, {})
            for field, disp_val in disp_entry.items():
                if field == "radio":
                    continue
                before_val = b.get(field)
                if before_val != disp_val:
                    touched.setdefault(radio_code, {})[field] = (before_val, disp_val)
        # An apply that changes nothing its before-state does not already hold is a
        # NO-OP: there is nothing to revert, so it must not be treated as a safely
        # revertible mutation (a no-op "revert" that re-sends the current value is not
        # a revert). Requires at least one touched field where before != dispatched.
        if not touched:
            raise SafetyViolation(
                f"step '{step.description}' dispatches nothing its before-state does not "
                "already hold (a no-op apply); there is nothing to revert -- refusing to "
                "treat a no-op as a safely-revertible mutation"
            )
        # The revert restores ONLY ``radio_table`` (see :meth:`revert`, which sends
        # ``restore_body = {"radio_table": ...}``). If this apply also changes any
        # OTHER top-level field the payload carries (e.g. ``disabled``, ``led_override``),
        # that change has NO inverse -- the revert would silently leave it in place while
        # marking the change reverted. Only a whole-``radio_table`` config change is
        # revertible, so refuse an apply that mutates anything else (verifier round 4).
        full_dispatched_body = step.payload if isinstance(step.payload, Mapping) else {}
        non_revertible = sorted(
            k
            for k, v in full_dispatched_body.items()
            if k != "radio_table" and v != body.get(k)
        )
        if non_revertible:
            raise SafetyViolation(
                f"step '{step.description}' also changes field(s) {non_revertible} outside "
                "radio_table; the revert restores only radio_table, so this change has no "
                "complete inverse -- refusing to apply a not-fully-revertible change"
            )
        # Build the reverse table with the DISPATCHED payload as the effective after,
        # so the touched fields are inverted back to their before-values regardless of
        # what ``step.after`` claims.
        dispatched_body = {"radio_table": [dict(r) for r in dispatched_radios if isinstance(r, dict)]}
        try:
            fresh_table = self._fresh_restore_table(-1, body, dispatched_body, current_radios)
            # The derived inverse must actually reverse the dispatched op. An empty
            # reverse table inverts nothing and would trivially pass every rail below.
            if not fresh_table:
                raise SafetyViolation(
                    f"step '{step.description}' derived reverse is empty; it inverts "
                    "nothing -- refusing to apply a change with no genuine revert"
                )
            # The reverse must, for at least one touched field, send the BEFORE value
            # rather than the current (post-apply) value -- i.e. it genuinely restores
            # the original state instead of re-sending what is already live.
            fresh_by_radio = {str(e.get("radio")): e for e in fresh_table}
            restores = False
            for radio_code, fields in touched.items():
                rev_entry = fresh_by_radio.get(radio_code, {})
                for field, (before_val, disp_val) in fields.items():
                    if rev_entry.get(field) == before_val and before_val != disp_val:
                        restores = True
                        break
                if restores:
                    break
            if not restores:
                raise SafetyViolation(
                    f"step '{step.description}' derived reverse does not restore the "
                    "before-state (it merely re-sends the current value) -- refusing to "
                    "apply a change with no genuine revert"
                )
            self._assert_revert_min_rssi_safe(-1, fresh_table, current_radios, is_mesh_uplink)
        except SafetyViolation:
            raise
        except PreconditionDrift as exc:  # a restore the builder itself would refuse
            raise SafetyViolation(
                f"step '{step.description}' reverse would be refused: {exc}"
            ) from exc

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
    def _assert_no_stale_overwrite(
        plan: FixPlan,
        full_state: Mapping[str, Mapping[str, Mapping[str, Any]]],
    ) -> None:
        """Refuse a whole-``radio_table`` PUT that would clobber a field changed since
        the plan was built (C1 apply race).

        The narrow precondition re-check only asserts the ONE attribute a fix targets.
        But a ``rest/device`` PUT replaces the entire ``radio_table``, so the payload
        carries the whole captured device snapshot with just the target field changed
        -- every OTHER field it re-sends equals its value at snapshot time (the step's
        recorded ``before``). If, by the time the per-device lock is held, a concurrent
        apply has committed a change to one of those untouched fields, re-sending the
        snapshot value would silently undo it. This compares each payload field against
        fresh live state (read under the lock) and aborts the whole plan as drift when
        an untouched field has diverged -- so the second of two concurrent applies
        fails rather than overwriting the first's committed change with stale bytes.

        ``full_state`` maps each target device key (:func:`_endpoint_device`) to the
        whole live ``{radio_code: {attr: value}}``. A target device (or radio) missing
        from it is unverifiable, and -- exactly as the precondition re-check treats a
        missing snapshot -- counts as drift: we never mutate on unverified state.
        """
        drifted: list[FixStep] = []
        for step in plan.steps:
            payload_radios = step.payload.get("radio_table") if step.payload else None
            if not payload_radios:
                continue  # not a whole-table PUT -- nothing to clobber here
            live_radios = full_state.get(_endpoint_device(step.endpoint))
            if live_radios is None:
                drifted.append(step)  # unverifiable device -> drift
                continue
            before_body = (step.before or {}).get("body", {}) if step.before else {}
            before_radios = {
                r.get("radio"): r for r in (before_body.get("radio_table") or [])
            }
            conflict = False
            for entry in payload_radios:
                radio = entry.get("radio")
                live = live_radios.get(radio)
                if live is None:
                    conflict = True  # radio vanished / unreadable -> unverified
                    break
                before_entry = before_radios.get(radio, {})
                for field, payload_val in entry.items():
                    if field == "radio":
                        continue
                    if payload_val != before_entry.get(field):
                        continue  # the field this step intends to change (or add)
                    # An UNTOUCHED field the PUT will re-send at its snapshot value;
                    # if live has diverged, sending it would overwrite a newer change.
                    if field in live and live[field] != payload_val:
                        conflict = True
                        break
                if conflict:
                    break
            if conflict:
                drifted.append(step)
        if drifted:
            raise PreconditionDrift(
                f"{len(drifted)} step(s) would overwrite a field changed since the plan "
                "was built (a concurrent apply); plan aborted",
                drifted,
            )

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


def _is_rest_device_endpoint(endpoint: Any) -> bool:
    """Whether ``endpoint`` is a ``rest/device/<id>`` config endpoint (a restorable
    device-config target, as opposed to a transient ``cmd/...`` command path)."""
    parts = str(endpoint).strip("/").split("/")
    return len(parts) >= 3 and parts[0] == "rest" and parts[1] == "device" and bool(parts[2])


def _write_is_ambiguous(write: WriteResult) -> bool:
    """Whether a failed write is *ambiguous* (outcome unknown) rather than a clean
    failure. The writer marks a lost/401-uncertain mutation with
    ``data={"ambiguous": True, ...}`` (C2); everything else is a definite failure."""
    return isinstance(write.data, dict) and bool(write.data.get("ambiguous"))


def _write_detail(write: WriteResult) -> Optional[str]:
    """The human-readable ambiguity/error detail the writer attached, if any."""
    if isinstance(write.data, dict):
        detail = write.data.get("error")
        if detail:
            return str(detail)
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False
