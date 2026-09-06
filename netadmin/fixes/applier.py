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
Then, past the gates, each step's PUT body is built by **merge-at-dispatch**: because
a ``rest/device`` PUT replaces the *entire* ``radio_table``, sending a plan-time
snapshot re-sends every untouched field/radio at its stale value. Instead the body is
assembled inside the lock by applying only the step's intended per-radio field delta
onto the FRESH live table -- carried forward across a plan's steps -- so step 2 of a
multi-step plan merges onto step 1's committed result (never undoing it), a concurrent
operator's untouched-field change is preserved rather than overwritten, and a radio
present in live but absent from the snapshot is kept rather than deleted. A merge that
would *drop* a radio the change's before-state defined is refused (the change could not
be fully reverted).

The precondition re-check is the *binding*, live-state-dependent validation, and it
runs **inside** the per-device lock against state read after the lock is held -- the
read -> merge -> write is one atomic critical section per device. Only past the gates
does it, per step: resolve the entity, write the before-state to the ``changes``
ledger, send the merged body through the writer, and mark the row applied/failed.
Before-state is captured first so a revert is always possible; a step's failure stops
the plan rather than pressing on mutating. Apply and revert are serialized per target
device so two operations on the same device can never interleave: a second apply
merges onto the first's committed write (never a stale pre-lock snapshot), and a
second revert re-reads the row's status under the lock and refuses once the first has
marked it reverted (or left it revert-uncertain), so a mutation is never replayed.
"""

from __future__ import annotations

import asyncio
import copy
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
# Terminal-but-uncertain for a REVERT: the restore was dispatched exactly once and
# its outcome is unknown (a lost response / a 401 it may have landed under). Like
# _STATUS_UNKNOWN for a forward apply, it is NOT "reverted" (the restore may not
# have taken) and NOT "applied" (a restore was attempted) -- so a second concurrent
# revert reads this and REFUSES to re-dispatch, never replaying the mutation (#2).
_STATUS_REVERT_UNKNOWN = "revert_unknown"
# Interim-but-durable for a REVERT: written UNDER THE LOCK immediately BEFORE the
# restore PUT is dispatched, and flipped to a terminal reverted/revert_unknown/applied
# only once the dispatch's outcome is known. If the revert task is cancelled (or the
# process dies) mid-send -- after the PUT went out but before it returned -- the row is
# LEFT reading "reverting": a durable marker that a restore was already dispatched with
# an unconfirmed outcome. A second revert re-reads this under the lock and REFUSES to
# re-dispatch, so a cancelled-mid-send revert can never be replayed as if fresh (#5).
_STATUS_REVERTING = "reverting"

# The UNCERTAIN / unresolved ledger states: a mutation was dispatched (or its send
# was interrupted) and its outcome is NOT confirmed. A change sitting in any of these
# means an earlier mutation on that target is still unreconciled:
#   * ``applying``       -- interim: the process died between record and send-return;
#   * ``unknown``        -- an ambiguous forward apply (lost response / 401);
#   * ``reverting``      -- a restore dispatched, outcome not yet known (or interrupted);
#   * ``revert_unknown`` -- an ambiguous restore.
# A NEW apply against the same target must REFUSE while one of these stands (#w10a-2):
# a GET showing the before-state does NOT prove an earlier timed-out mutation is not
# still processing upstream, so re-dispatching risks a DOUBLE-apply. The prior outcome
# must be reconciled by a human first -- never auto-replayed. This is the apply-side
# mirror of the revert eligibility recheck (:meth:`_assert_revertible_status`).
_UNCERTAIN_STATUSES = frozenset(
    {_STATUS_APPLYING, _STATUS_UNKNOWN, _STATUS_REVERTING, _STATUS_REVERT_UNKNOWN}
)


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

            # Apply-replay guard (#w10a-2), UNDER THE LOCK and mirroring the revert
            # eligibility recheck. Before dispatching, refuse if ANY target this plan
            # would mutate already carries an UNCERTAIN change (an earlier apply/revert
            # whose outcome was never confirmed). Re-dispatching against an unresolved
            # prior mutation risks a double-apply -- a GET showing the before-state does
            # not prove a timed-out earlier write is not still processing upstream. The
            # human must reconcile the prior outcome first; we never auto-replay it.
            self._assert_no_uncertain_change(plan)

            # Merge-at-dispatch (P1 root cause). A ``rest/device`` PUT replaces the
            # ENTIRE ``radio_table``, so sending a plan-time snapshot re-sends every
            # untouched field/radio at its stale value -- which (a) lets step 2 of a
            # multi-step plan re-send step 1's radio at its OLD value and undo it (#5),
            # and (b) lets a concurrent operator's change to an untouched field be
            # overwritten, or a radio added to live be DELETED (#1). Instead, each
            # step's body is built HERE, inside the lock, by applying ONLY the step's
            # intended per-radio field delta onto the FRESH live table:
            #
            #   * across a plan's steps the live table is carried forward in-memory
            #     (``carried``), so step 2 merges onto step 1's committed result and
            #     cannot clobber it (#5);
            #   * untouched fields keep their live value and radios present in live but
            #     absent from the snapshot are preserved by construction (#1);
            #   * an unchanged top-level (non-``radio_table``) field the step never
            #     intends to change is dropped rather than re-sent, so it cannot clobber
            #     a concurrent change to that device-level field (#2).
            #
            # The base is fresh live (``full_state``, read under the lock) when a
            # ``state_reader`` supplied it; otherwise the step's own before-snapshot
            # (a single-op caller with no concurrency). The device lock means no
            # external write lands mid-plan, so the in-memory carry-forward is exact.
            #
            # Pass 1 (no network): build and VALIDATE every step's merged dispatch
            # body. Crucially the revertibility rail (#3) runs here, against the
            # MERGED table that will actually be dispatched -- not the plan-time
            # payload -- so a change whose merged form (carrying a concurrent live
            # value, e.g. min-RSSI re-enabled on a now-mesh AP) could not be reverted
            # is refused UP FRONT, before a single call goes out. All steps are
            # validated before any dispatch, so a non-revertible later step aborts the
            # whole plan rather than leaving an earlier step half-applied.
            carried: dict[str, dict[str, dict[str, Any]]] = {}
            prepared: list[tuple[FixStep, dict[str, Any], Optional[dict[str, dict[str, Any]]]]] = []
            for step in plan.steps:
                dev_key = _endpoint_device(step.endpoint)
                base = self._merge_base(step, dev_key, carried, full_state)
                dispatch_body, merged = self._merge_step_onto(step, base)
                merged_radios = merged if merged is not None else {
                    str(r.get("radio")): dict(r)
                    for r in (dispatch_body.get("radio_table") or [])
                    if isinstance(r, dict) and r.get("radio") is not None
                }
                self._assert_step_reverse_ok(
                    step, dev_key in mesh_uplinks, merged_radios
                )
                if merged is not None:
                    carried[dev_key] = merged
                prepared.append((step, dispatch_body, merged))

            # Gate 6: precondition re-check of every step against fresh live state --
            # any drift aborts the whole plan before a single call is sent. Runs AFTER
            # the revertibility validation above so a non-revertible change is refused
            # as such rather than masked by an incidental drift.
            drifted = self._precondition_drift(plan, current_state)
            if drifted:
                raise PreconditionDrift(
                    f"{len(drifted)} step(s) drifted from expected state; plan aborted", drifted
                )

            # Gate 6b (#1): bind EVERY field the merged PUT will actually SEND to its
            # before-value. The declared precondition only covers a step's headline
            # attribute (a channel), so a delta field the payload ALSO changes (a
            # bundled tx-power move, say) escapes it -- and merge-at-dispatch layers
            # that delta straight onto fresh live, silently overwriting a concurrent
            # operator's change to that very field. Refuse when fresh live diverges
            # from the recorded before on any field this dispatch would change; the
            # human must re-review the payload against the state as it now is.
            field_drift = self._delta_field_drift(plan, full_state)
            if field_drift:
                raise PreconditionDrift(
                    "; ".join(msg for _, msg in field_drift) + "; plan aborted",
                    [s for s, _ in field_drift],
                )

            # Pass 2: dispatch the validated bodies in order, stopping at the first
            # failure/ambiguity. Bodies were built assuming each prior step commits;
            # under the device lock no external write intervenes, so that holds.
            for step, dispatch_body, merged in prepared:
                change_id = self._record_before(plan, step, now, dispatch_body)
                change_ids.append(change_id)
                try:
                    write = await self._dispatch_raw(step.method, step.endpoint, dispatch_body)
                except Exception as exc:  # noqa: BLE001 - a transport failure is a step failure
                    self._store.update_change_status(change_id, _STATUS_FAILED)
                    results.append(StepResult(step, _STATUS_FAILED, change_id, None, str(exc)))
                    applied_all = False
                    _log.warning("fix step raised, stopping plan: %s", exc)
                    break

                if write.ok:
                    # The next step's body was already built (pass 1) by merging onto
                    # THIS step's committed table, carried forward in-memory under the
                    # device lock -- so a later step on the SAME device never re-sends
                    # the pre-plan snapshot (#5).
                    _ = merged  # carry-forward happened in pass 1; kept for symmetry
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
        # Cheap pre-lock early-out: refuse the TERMINAL revert states outright. The
        # transient 'reverting' is deliberately allowed through here -- it may be a
        # concurrent revert still in flight, and the authoritative re-check under the
        # lock (below) blocks a genuine replay once we actually hold the device lock.
        self._assert_revertible_status(change_id, row["status"], allow_in_flight=True)

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
            prior_status = fresh_row["status"]
            self._assert_revertible_status(change_id, prior_status)
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

            # Durably record that a restore is IN FLIGHT before the PUT is dispatched,
            # under the lock (#5). If this task is cancelled mid-send -- after the PUT
            # went out but before it returned -- neither the ok/ambiguous/failed branch
            # below runs, so the row is LEFT reading 'reverting': a durable marker that a
            # restore was already dispatched with an unconfirmed outcome. A second revert
            # re-reads that status under the lock (:meth:`_assert_revertible_status`) and
            # REFUSES to re-dispatch, so a cancelled-mid-send revert is never replayed as
            # if fresh. On a KNOWN outcome the interim is flipped to a terminal status.
            self._store.update_change_status(change_id, _STATUS_REVERTING)
            write = await self._dispatch_raw(method, str(endpoint), restore_body)
            if write.ok:
                self._store.update_change_status(change_id, _STATUS_REVERTED, reverted_ts=now)
            elif _write_is_ambiguous(write):
                # #2: the restore was dispatched but its outcome is unknown (a lost
                # response, or a 401 it may have landed under). Record it as terminal-
                # uncertain UNDER THE LOCK so a second concurrent revert re-reads this
                # status and REFUSES to re-dispatch -- an ambiguous first revert must
                # block the second from replaying the mutation. It is NOT 'reverted'
                # (the restore may not have taken); the operator reconciles via a read.
                self._store.update_change_status(change_id, _STATUS_REVERT_UNKNOWN)
                _log.warning(
                    "revert of change %s ambiguous; marked revert_unknown to block replay",
                    change_id,
                )
            else:
                # A DEFINITIVE failure (non-2xx / meta.rc=error): the restore did not
                # land, so roll the interim 'reverting' back to the change's PRIOR
                # state -- NOT unconditionally to 'applied' (#w10a-3). A rejected
                # RESTORE proves only that the restore failed; it says NOTHING about
                # whether the ORIGINAL apply succeeded. If the apply was confirmed
                # ('applied'), it stays 'applied' and is legitimately retryable. But if
                # the apply outcome was itself never confirmed ('unknown'), promoting
                # it to 'applied' here would fabricate certainty the system never had --
                # upgrading an uncertain apply into a confirmed one via a revert
                # rejection. An uncertain apply must stay 'unknown'; only a genuinely
                # 'applied' change rolls back to 'applied'.
                restored = _STATUS_UNKNOWN if prior_status == _STATUS_UNKNOWN else _STATUS_APPLIED
                self._store.update_change_status(change_id, restored)
                _log.warning(
                    "revert of change %s failed (status=%s); left status=%s "
                    "(a rejected restore does not confirm the original apply)",
                    change_id,
                    write.status_code,
                    restored,
                )
        return write

    def _assert_no_uncertain_change(self, plan: FixPlan) -> None:
        """Refuse a fresh apply while any target carries an UNRESOLVED change (#w10a-2).

        The apply-side of the concurrent/duplicate-mutation guard, and the mirror of
        :meth:`_assert_revertible_status` on the revert path. For each device/radio this
        plan would mutate, look up its ledger entity and scan its change rows: if one is
        in an UNCERTAIN state (:data:`_UNCERTAIN_STATUSES` -- an ambiguous apply, an
        in-flight/interrupted or ambiguous revert), the prior mutation's outcome was
        never confirmed. Dispatching a NEW mutation against that same target could
        double-apply an earlier write that a timed-out response left still processing
        upstream, so refuse: the operator must reconcile the live state via a read and
        resolve the stale row before a new apply is allowed. Runs UNDER THE DEVICE LOCK
        (its caller holds it), so a concurrent apply's just-recorded uncertain row is
        visible here rather than raced past.
        """
        def _refuse_if_uncertain(rows: Iterable[Any], target: str) -> None:
            for change in rows:
                status = change["status"] if "status" in change.keys() else None
                if status in _UNCERTAIN_STATUSES:
                    raise FixError(
                        f"target '{target}' has an unresolved change "
                        f"(id {int(change['id'])}, status '{status}') whose outcome was "
                        "never confirmed; refusing to apply a new mutation over it -- "
                        "reconcile the live state via a read and resolve that change "
                        "first, do not replay an uncertain outcome"
                    )

        # (a) Per physical target (device/radio): the strongest identity, and the one
        # that holds in production where ingest has registered the entity. A resolved
        # entity's uncertain ledger row blocks a new mutation on that exact target.
        seen: set[int] = set()
        for step in plan.steps:
            row = self._store.find_entity(step.target_entity_type, step.target_native_id)
            if row is None:
                continue
            entity_id = int(row["entity_id"])
            if entity_id in seen:
                continue
            seen.add(entity_id)
            _refuse_if_uncertain(
                self._store.list_changes(entity_id=entity_id), step.target_native_id
            )
        # (b) By issue: a re-apply presents the SAME confirm token => the SAME plan and
        # issue, so an ambiguous change this very issue produced blocks its own replay
        # even if the entity is not separately registered. Only when the plan carries an
        # issue id (a NULL issue_id would over-match every unattributed change).
        if plan.issue_id is not None:
            target = plan.steps[0].target_native_id if plan.steps else f"issue {plan.issue_id}"
            _refuse_if_uncertain(
                self._store.list_changes(issue_id=plan.issue_id), target
            )
        # (c) Per physical DEVICE ENDPOINT (#w11a-3). A ``rest/device/<id>`` PUT
        # replaces the ENTIRE device (its whole ``radio_table``), so an unresolved
        # uncertain change on ANY radio of that device -- even one recorded against a
        # DIFFERENT radio/entity than this plan touches -- must block a new apply to
        # it. The per-entity scan in (a) is keyed on the exact radio entity, so a
        # sibling radio on the SAME AP (a different ``entity_id``) slips past it and
        # this plan's whole-table PUT would re-send that radio's still-unresolved
        # value. Key on the device the change actually DISPATCHED to (parsed from its
        # recorded ``after`` endpoint), and refuse if it matches any device this plan
        # would mutate. Runs under the same device lock as (a)/(b).
        plan_devices = {_endpoint_device(step.endpoint) for step in plan.steps}
        for change in self._store.list_changes():
            status = change["status"] if "status" in change.keys() else None
            if status not in _UNCERTAIN_STATUSES:
                continue
            dev = _change_device_endpoint(change)
            if dev is not None and dev in plan_devices:
                raise FixError(
                    f"device '{dev}' has an unresolved change "
                    f"(id {int(change['id'])}, status '{status}') on it whose outcome "
                    "was never confirmed; a whole-radio_table PUT replaces the entire "
                    "device, so refusing to apply a new mutation to any radio on it -- "
                    "reconcile the live state via a read and resolve that change first, "
                    "do not replay an uncertain outcome"
                )

    @staticmethod
    def _assert_revertible_status(
        change_id: int, status: Any, *, allow_in_flight: bool = False
    ) -> None:
        """Refuse a revert whose row is already reverted, in-flight, or revert-uncertain.

        ``reverted`` is a completed rollback; ``revert_unknown`` is a rollback that was
        already dispatched once with an unconfirmed outcome (#2); ``reverting`` is a
        rollback whose PUT was dispatched but not yet confirmed -- still in flight, or
        interrupted mid-send by a cancelled task / dead process (#5) so its outcome is
        likewise unknown. Re-dispatching any of the three would replay the mutation, so
        all are refused UNDER THE LOCK -- the second of two concurrent reverts sees the
        first's status and stops with no dispatch, and an interrupted revert is treated
        as uncertain, not retryable-as-fresh.

        ``allow_in_flight`` is set only for the CHEAP pre-lock early-out: there
        ``reverting`` is let through (a concurrent revert may still be in flight and may
        yet resolve), because the authoritative re-check happens once the device lock is
        actually held. The terminal states are always refused, lock or not.
        """
        if status == _STATUS_REVERTED:
            raise FixError(f"change {change_id} already reverted")
        if status == _STATUS_REVERT_UNKNOWN:
            raise FixError(
                f"change {change_id} revert was already attempted with an unknown/ambiguous "
                "outcome; not replaying -- reconcile the live state via a read first"
            )
        if status == _STATUS_REVERTING and not allow_in_flight:
            raise FixError(
                f"change {change_id} revert is already in flight or was interrupted mid-send; "
                "its outcome is unknown -- not replaying, reconcile the live state via a read first"
            )

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

        A radio present in ``before`` but ABSENT from ``after`` was DELETED by the
        dispatched change (its whole-table PUT dropped it). Its inverse iterates only
        ``after`` entries, so the deletion would never enter the touched set and the
        revert would silently leave the radio gone while marking the row 'reverted'
        (#6). Detect it and refuse: a change that deleted a radio has no complete
        inverse from ``radio_table`` alone, so it is not fully revertible.

        A FIELD present in ``before`` but absent from a SURVIVING radio's ``after`` was
        likewise DELETED by the change (its whole-table PUT dropped that key). Iterating
        only after-fields missed it, so the revert left the field gone while marking the
        row 'reverted' -- an incomplete, dishonest rollback (#10). The touched set is
        therefore keyed on the UNION of before- and after-fields per radio, and a
        deleted field is restored by re-adding its before-value (or refused as a
        conflict if live has since given it a different value).
        """
        before_radios = {
            r.get("radio"): r for r in (before_body.get("radio_table") or []) if isinstance(r, dict)
        }
        after_radios = {
            r.get("radio"): r for r in (after_body.get("radio_table") or []) if isinstance(r, dict)
        }
        deleted_radios = sorted(
            str(code) for code in before_radios if code not in after_radios
        )
        if deleted_radios:
            raise SafetyViolation(
                f"revert of change {change_id} cannot complete: the applied change dropped "
                f"radio(s) {deleted_radios} that its before-state defined, so restoring only "
                "the radios it touched would leave them absent -- this change is not fully "
                "revertible"
            )
        # Touched = fields whose STATE differs between before and after, where a field
        # present in one and absent in the other counts as a difference. Keying on the
        # union of before+after field names (not just after) is what catches a field the
        # change DELETED from a surviving radio (#10).
        touched: dict[Any, set[str]] = {}
        for radio, aentry in after_radios.items():
            bentry = before_radios.get(radio, {})
            names = (set(aentry) | set(bentry)) - {"radio"}
            fields = {
                k
                for k in names
                if (k in bentry) != (k in aentry) or bentry.get(k) != aentry.get(k)
            }
            if fields:
                touched[radio] = fields

        conflicts: list[str] = []
        fresh: list[dict[str, Any]] = []
        for radio_code, live_entry in current_radios.items():
            entry = dict(live_entry)
            bentry = before_radios.get(radio_code, {})
            aentry = after_radios.get(radio_code, {})
            for field in touched.get(radio_code, set()):
                before_present, after_present = field in bentry, field in aentry
                live_present = field in live_entry
                before_val, after_val, live_val = (
                    bentry.get(field),
                    aentry.get(field),
                    live_entry.get(field),
                )
                # The change set the field to its AFTER state (a value, or absent). Live
                # must still match that AFTER state, else someone changed it since -- unless
                # live already sits at the BEFORE state we would restore to.
                live_state = (live_present, live_val)
                if live_state != (after_present, after_val) and live_state != (
                    before_present,
                    before_val,
                ):
                    if not live_present:
                        conflicts.append(
                            f"{radio_code}.{field} could not be read from live state"
                        )
                    else:
                        conflicts.append(
                            f"{radio_code}.{field} is now {live_val!r}, not the {after_val!r} "
                            "this change set"
                        )
                    continue
                # Restore the BEFORE state: set the value, or drop the field if before
                # lacked it (the change ADDED it; the revert removes it).
                if before_present:
                    entry[field] = before_val
                else:
                    entry.pop(field, None)
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
    def _record_before(
        self, plan: FixPlan, step: FixStep, now: int, dispatched_body: Mapping[str, Any]
    ) -> int:
        """Insert the before-state ledger row (interim status) prior to sending.

        The stored ``after`` is the ACTUAL dispatched operation, never the
        planner-supplied ``step.after``. :meth:`revert` derives the fields to roll
        back from ``after`` vs ``before``; recording the real dispatch means a forged
        or stale ``step.after`` (e.g. one set equal to ``before`` to disguise a
        change) can never make the later revert a no-op that re-sends the current
        value instead of restoring the original (S2).

        The recorded ``after`` is the step's before-body with ONLY the step's field
        delta applied -- NOT the raw merged bytes on the wire. The merged bytes carry
        live values for fields the step never touched (merge-at-dispatch preserves a
        concurrent operator's work); recording those would make revert think WE
        changed them and try to roll them back, clobbering that work. Recording
        before+delta keeps ``after``-vs-``before`` equal to exactly the fields this
        step changed, so revert restores those and leaves everything else at its
        current live value.
        """
        _ = dispatched_body  # the wire bytes carry live values for untouched fields;
        # the recorded after is before+delta (see docstring), not the raw merge.
        after_body = self._intended_after_body(step)
        return self._insert_change_row(plan, step, now, after_body)

    def _insert_change_row(
        self, plan: FixPlan, step: FixStep, now: int, after_body: Mapping[str, Any]
    ) -> int:
        entity_id = None
        row = self._store.find_entity(step.target_entity_type, step.target_native_id)
        if row is not None:
            entity_id = int(row["entity_id"])
        dispatched_after = {
            "method": step.method,
            "endpoint": step.endpoint,
            "body": dict(after_body),
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

    # ------------------------------------------------------------------ #
    # Merge-at-dispatch (P1 root cause)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _step_radio_delta(step: FixStep) -> dict[str, dict[str, Any]]:
        """The per-radio ``{field: new_value}`` this step *intends* to set.

        Derived from the step's own payload vs its recorded before-body -- the fields
        where the payload differs from before, per radio. This is the "intended
        change" that merge-at-dispatch layers onto the fresh live table, and the delta
        that :meth:`_intended_after_body` records so a revert rolls back exactly it.
        The ``radio`` key itself is never a delta field.
        """
        payload = step.payload if isinstance(step.payload, Mapping) else {}
        payload_radios = payload.get("radio_table") or []
        before_body = (step.before or {}).get("body") if isinstance(step.before, dict) else {}
        before_radios = {
            str(r.get("radio")): r
            for r in ((before_body or {}).get("radio_table") or [])
            if isinstance(r, dict) and r.get("radio") is not None
        }
        delta: dict[str, dict[str, Any]] = {}
        for r in payload_radios:
            if not isinstance(r, dict) or r.get("radio") is None:
                continue
            code = str(r["radio"])
            brow = before_radios.get(code, {})
            fields = {k: v for k, v in r.items() if k != "radio" and brow.get(k) != v}
            if fields:
                delta[code] = fields
        return delta

    @staticmethod
    def _intended_after_body(step: FixStep) -> dict[str, Any]:
        """The step's before-body with its field delta applied (the recorded after).

        Structurally identical to ``before`` -- same radios, same top-level keys --
        with only the fields the step changes set to their new value. Keeping the full
        before shape (rather than the delta alone) means ``after`` and ``before`` name
        the same radios, so the revert's deleted-radio guard never false-fires, and
        ``after``-vs-``before`` is exactly this step's change.
        """
        before_body = (step.before or {}).get("body") if isinstance(step.before, dict) else None
        delta = Applier._step_radio_delta(step)
        if not isinstance(before_body, dict) or not isinstance(
            before_body.get("radio_table"), list
        ):
            # No restorable before radio_table (a gated radio step always has one);
            # fall back to the raw payload so nothing is silently dropped.
            return dict(step.payload or {})
        body = copy.deepcopy(before_body)
        existing: set[str] = set()
        for entry in body.get("radio_table", []):
            if not isinstance(entry, dict):
                continue
            code = str(entry.get("radio"))
            existing.add(code)
            if code in delta:
                entry.update(delta[code])
        for code, fields in delta.items():
            if code not in existing:
                body.setdefault("radio_table", []).append({"radio": code, **fields})
        return body

    def _merge_base(
        self,
        step: FixStep,
        dev_key: str,
        carried: Mapping[str, dict[str, dict[str, Any]]],
        full_state: Optional[Mapping[str, Mapping[str, Mapping[str, Any]]]],
    ) -> Optional[dict[str, dict[str, Any]]]:
        """The ``{radio_code: {attr: value}}`` table this step's delta merges onto.

        Preference order: the table carried forward from an earlier step on the SAME
        device (so step 2 merges onto step 1's committed result, #5); else the fresh
        live table read under the lock (``full_state``, which also carries a concurrent
        operator's untouched-field/added-radio changes, #1); else the step's own
        before-snapshot (a single-op caller that passed no ``state_reader``). Returns
        ``None`` only when there is no radio_table to merge (a non-radio step), so the
        caller sends the payload unchanged.
        """
        if dev_key in carried:
            return carried[dev_key]
        base: dict[str, dict[str, Any]] = {}
        if full_state is not None and dev_key in full_state:
            for code, entry in full_state[dev_key].items():
                if entry is not None:
                    base[str(code)] = dict(entry)
        else:
            before_body = (step.before or {}).get("body") if isinstance(step.before, dict) else None
            radios = before_body.get("radio_table") if isinstance(before_body, dict) else None
            for r in radios or []:
                if isinstance(r, dict) and r.get("radio") is not None:
                    base[str(r["radio"])] = dict(r)
        if not base:
            return None
        return base

    def _merge_step_onto(
        self, step: FixStep, base: Optional[Mapping[str, dict[str, Any]]]
    ) -> tuple[dict[str, Any], Optional[dict[str, dict[str, Any]]]]:
        """Build the whole-``radio_table`` PUT body by merging the step's delta onto
        ``base``. Returns ``(dispatch_body, merged_table)``; ``merged_table`` is the
        carry-forward state for the next step (``None`` when no merge happened).

        A radio present in the step's before-state must survive the merge: if the live
        base no longer carries it, the whole-table PUT would DELETE it and the change
        could never be fully reverted (the deleted radio has no inverse). That is an
        anomalous device state (a radio vanished between plan and apply), so refuse
        rather than dispatch a not-fully-revertible write (defends the revertibility
        invariant; the revert side detects the same class for older rows, #6).
        """
        if base is None:
            return dict(step.payload or {}), None
        delta = self._step_radio_delta(step)
        merged: dict[str, dict[str, Any]] = {code: dict(entry) for code, entry in base.items()}
        for code, fields in delta.items():
            if code in merged:
                merged[code].update(fields)
            else:
                merged[code] = {"radio": code, **fields}
        for code, entry in merged.items():
            entry.setdefault("radio", code)
        before_body = (step.before or {}).get("body") if isinstance(step.before, dict) else {}
        before_codes = {
            str(r.get("radio"))
            for r in ((before_body or {}).get("radio_table") or [])
            if isinstance(r, dict) and r.get("radio") is not None
        }
        dropped = sorted(c for c in before_codes if c not in merged)
        if dropped:
            raise SafetyViolation(
                f"step '{step.description}' would drop radio(s) {dropped} present in its "
                "before-state (no longer in live state) from a whole-table PUT; the "
                "resulting change could not be fully reverted -- refusing"
            )
        # Top-level (non-``radio_table``) fields: send ONLY those the step actually
        # INTENDS to change (payload differs from before). An UNCHANGED top-level field
        # (payload == before) is not this step's change; re-sending it would blindly
        # overwrite a concurrent change to that device-level field in fresh live and,
        # because the revert restores only ``radio_table``, escape rollback entirely
        # (#2). Dropping it binds the dispatched op to what the step means to change.
        # A CHANGED top-level field never reaches here -- the revertibility gate refuses
        # it up front (it has no ``radio_table`` inverse), so this loop yields {} in
        # practice; the guard is defence in depth.
        before_top = {
            k: v for k, v in (before_body or {}).items() if k != "radio_table"
        } if isinstance(before_body, dict) else {}
        body = {
            k: v
            for k, v in (step.payload or {}).items()
            if k != "radio_table" and before_top.get(k) != v
        }
        body["radio_table"] = list(merged.values())
        return body, merged

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

    def _assert_step_reverse_ok(
        self,
        step: FixStep,
        is_mesh_uplink: bool,
        merged_radios: Mapping[str, Mapping[str, Any]],
    ) -> None:
        """Dry-run this step's reverse through the real revert rails; raise if refused.

        Revertibility is a property of THE DISPATCHED operation, not merely of some
        stored before-body. The gate derives the inverse from the step's own
        endpoint+method+payload and confirms it actually reverses THEM -- otherwise a
        step that dispatches a one-way command (a transient ``POST cmd/devmgr``
        power-cycle, say) but happens to carry an unrelated, valid-looking
        radio-restore before-body would sail through: its derived reverse table comes
        out empty (the dispatched payload has no ``radio_table`` to invert) and an
        empty reverse trivially passes every downstream rail.

        Crucially, the reverse is validated against ``merged_radios`` -- the MERGED
        whole-table this apply will ACTUALLY dispatch (the step's delta layered onto
        fresh live), not the plan-time payload (#3). That is exactly the state an
        immediate revert would read, and it carries any concurrent live value the
        merge preserved (e.g. min-RSSI re-enabled on a now-mesh AP). Validating the
        reverse against the plan-time payload would pass a change whose merged form
        cannot in fact be reverted; validating against the merged op refuses it up
        front, so we never apply something whose real rollback the rails would bar.

        The touched fields (what the revert must roll back) are derived from the
        step's INTENDED after -- its own before with only its field delta applied,
        the very body recorded in the ledger and used by :meth:`revert` -- never from
        the claimed ``step.after``. A forged ``after == before`` cannot empty the
        touched set (S2 round 3), and a live-carried field the step never touched is
        correctly left out of the reverse's touched set.
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
        # The post-apply live state a revert issued right afterwards would read IS the
        # merged table this apply dispatches (#3), so build the reverse against it.
        current_radios = {
            str(code): dict(entry)
            for code, entry in merged_radios.items()
            if entry is not None
        }
        before_radios = {
            str(r.get("radio")): r
            for r in (body.get("radio_table") or [])
            if isinstance(r, dict) and r.get("radio") is not None
        }
        # (3) Derive the inverse from the step's INTENDED after -- its before-body
        # with ONLY its field delta applied (:meth:`_intended_after_body`, the exact
        # body the ledger records and :meth:`revert` inverts) -- never from a trusted
        # ``step.after``. The touched fields are those where the intended after
        # differs from ``before``; the revert restores exactly those to their before-
        # values. Trusting ``step.after`` let a forged ``after == before`` collapse the
        # touched set to empty (S2 round 3); deriving from the delta also keeps a
        # live-carried field the step never touched OUT of the touched set, so the
        # reverse rolls back only what the step actually changed.
        intended_after_body = self._intended_after_body(step)
        after_radios = {
            str(r.get("radio")): r
            for r in (intended_after_body.get("radio_table") or [])
            if isinstance(r, dict) and r.get("radio") is not None
        }
        # A change that ADDS a radio (present in the step's intended-after table,
        # absent from its before-state) has no clean whole-table inverse under this
        # model (#D8): the revert rolls back only the fields it TOUCHED, so it removes
        # the added radio's fields but leaves the radio entry itself behind -- the
        # added radio survives while the row is marked 'reverted'. Symmetric with the
        # delete-radio refusal (a dropped radio has no inverse either): the planner
        # never adds radios, so refuse it at apply time rather than mark a dishonest
        # revert. Judged on the step's INTENDED after (before + its delta), never on
        # ``merged_radios`` -- the merged table also carries a concurrent operator's
        # live-added radio, which merge-at-dispatch preserves (#1) and the revert
        # correctly leaves untouched; that is not a radio THIS step adds.
        added_radios = sorted(code for code in after_radios if code not in before_radios)
        if added_radios:
            raise SafetyViolation(
                f"step '{step.description}' ADDS radio(s) {added_radios} not present in its "
                "before-state; a whole-table revert would strip their fields but leave the "
                "radio entries behind -- the added radio has no complete inverse, refusing "
                "to apply a not-fully-revertible change"
            )
        touched: dict[str, dict[str, tuple[Any, Any]]] = {}
        for radio_code, aentry in after_radios.items():
            b = before_radios.get(radio_code, {})
            for field, after_val in aentry.items():
                if field == "radio":
                    continue
                before_val = b.get(field)
                if before_val != after_val:
                    touched.setdefault(radio_code, {})[field] = (before_val, after_val)
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
        # Build the reverse table against the MERGED live table, with the step's
        # intended after as the effective after -- so the touched fields are inverted
        # back to their before-values regardless of what ``step.after`` claims and any
        # live-carried field is judged in place (#3).
        try:
            fresh_table = self._fresh_restore_table(
                -1, body, intended_after_body, current_radios
            )
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

    def _delta_field_drift(
        self,
        plan: FixPlan,
        full_state: Optional[Mapping[str, Mapping[str, Mapping[str, Any]]]],
    ) -> list[tuple[FixStep, str]]:
        """Bind every field the merged PUT will SEND to its recorded before-value (#1).

        The declared precondition only asserts a step's headline attribute (a
        channel), but a step's payload can carry a *delta* on other fields too (a
        bundled tx-power move). Merge-at-dispatch layers that whole delta onto fresh
        live, so a delta field whose fresh-live value diverges from the ``before`` we
        recorded is a concurrent change this dispatch would silently overwrite -- and
        the precondition never looked at it.

        For each step, compare every field in its per-radio delta against fresh live
        (``full_state``, read under the lock). If live differs from the delta's
        before-value on any such field, that step drifted: the operation the human
        confirmed no longer matches the network, so refuse. When no fresh full table
        is available (a single-op caller passing only ``current_state``) there is no
        live to bind against and the check is skipped -- the precondition re-check and
        the merge base then both derive from the step's own before-snapshot.
        """
        if full_state is None:
            return []
        drift: list[tuple[FixStep, str]] = []
        for step in plan.steps:
            dev_key = _endpoint_device(step.endpoint)
            live_radios = full_state.get(dev_key)
            if live_radios is None:
                continue  # device absent from fresh state -> precondition drift handles it
            delta = self._step_radio_delta(step)
            before_body = (
                (step.before or {}).get("body") if isinstance(step.before, dict) else {}
            )
            before_radios = {
                str(r.get("radio")): r
                for r in ((before_body or {}).get("radio_table") or [])
                if isinstance(r, dict) and r.get("radio") is not None
            }
            for code, fields in delta.items():
                before_entry = before_radios.get(code, {})
                live_entry = live_radios.get(code)
                if live_entry is None:
                    continue  # radio absent from live -> merge/clobber guard handles it
                for field in fields:
                    if field not in live_entry:
                        # A field the delta will SEND that is ABSENT from fresh live.
                        # If the recorded ``before`` HELD this field (a value we
                        # reviewed against and would restore on revert), its
                        # disappearance means the device's shape changed since review
                        # (#D1): we can neither confirm the before-value the human
                        # reviewed nor guarantee a clean revert -- the merge would
                        # silently re-add the field, and a later revert would write a
                        # now-stale before-value. Refuse as drift. (A field ``before``
                        # also lacked -- the delta is genuinely ADDING it, consistent
                        # with live -- is not a divergence and is left to the merge.)
                        if field in before_entry:
                            drift.append(
                                (
                                    step,
                                    f"step '{step.description}' would set radio '{code}' "
                                    f"field '{field}', but that field is absent from fresh "
                                    "live though the reviewed before-state held it "
                                    f"({before_entry.get(field)!r}); the device's shape "
                                    "changed -- its before-value can no longer be confirmed",
                                )
                            )
                        continue
                    before_val = before_entry.get(field)
                    live_val = live_entry.get(field)
                    if live_val != before_val:
                        drift.append(
                            (
                                step,
                                f"step '{step.description}' would set radio '{code}' field "
                                f"'{field}', but fresh live is {live_val!r}, not the recorded "
                                f"{before_val!r} it was reviewed against",
                            )
                        )
        return drift

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


def _change_device_endpoint(change: Any) -> Optional[str]:
    """The device key a ledger change dispatched to, from its recorded ``after``.

    A change row stores the actual dispatched op as ``after_json`` =
    ``{"method", "endpoint", "body"}`` (:meth:`Applier._insert_change_row`). Parse
    that endpoint and reduce it to its device key (:func:`_endpoint_device`) so the
    apply-replay guard can match an uncertain change to the physical device it
    touched -- even when it was recorded against a different radio/entity (#w11a-3).
    Returns ``None`` when there is no parseable endpoint to key on.
    """
    try:
        raw = change["after_json"] if "after_json" in change.keys() else None
    except Exception:  # noqa: BLE001 - a row without the column is simply unkeyable
        return None
    if not raw:
        return None
    try:
        after = json.loads(raw)
    except (TypeError, ValueError):
        return None
    endpoint = after.get("endpoint") if isinstance(after, dict) else None
    if not endpoint:
        return None
    return _endpoint_device(str(endpoint))


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
