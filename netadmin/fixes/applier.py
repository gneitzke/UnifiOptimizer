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
#. a precondition re-check of **every** step against freshly read live state --
   any drift aborts the whole plan before a single call goes out.

Only past all six does it, per step: resolve the entity, write the before-state to
the ``changes`` ledger, send through the writer, and mark the row applied/failed.
Before-state is captured first so a revert is always possible; a step's failure
stops the plan rather than pressing on mutating.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Mapping, Optional

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
        """
        now = self._now_fn() if now is None else now
        row = self._store.get_change(change_id)
        if row is None:
            raise FixError(f"no change with id {change_id}")
        if row["status"] == _STATUS_REVERTED:
            raise FixError(f"change {change_id} already reverted")

        before = json.loads(row["before_json"]) if row["before_json"] else {}
        body = before.get("body") if isinstance(before, dict) else None
        endpoint = before.get("endpoint") if isinstance(before, dict) else None
        if not body or not endpoint:
            raise FixError(f"change {change_id} is not revertible (no stored before-state)")
        if self._writer is None:
            raise WriterRequired("revert requires an injected ControllerWriter")

        # Re-gate a radio-config restore against fresh live state before sending.
        restore_radios = body.get("radio_table") if isinstance(body, dict) else None
        if restore_radios:
            if current_radios is None:
                raise SafetyViolation(
                    f"revert of change {change_id} touches radio config but no fresh live "
                    "state was read; refusing to restore on unverified state"
                )
            self._assert_revert_min_rssi_safe(
                change_id, restore_radios, current_radios, is_mesh_uplink
            )

        method = str(before.get("method") or "PUT")
        write = await self._dispatch_raw(method, str(endpoint), body)
        if write.ok:
            self._store.update_change_status(change_id, _STATUS_REVERTED, reverted_ts=now)
        else:
            _log.warning("revert of change %s failed (status=%s)", change_id, write.status_code)
        return write

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

    def _precondition_drift(
        self, plan: FixPlan, current_state: Mapping[str, Mapping[str, Any]]
    ) -> list[FixStep]:
        """Return the steps whose expected state no longer matches live state.

        An empty ``expected`` always matches. A target absent from
        ``current_state`` (we could not read it) counts as drift: we never mutate
        on unverified state.
        """
        drifted: list[FixStep] = []
        for step in plan.steps:
            expected = step.precondition.expected
            if not expected:
                continue
            live = current_state.get(step.precondition.target_native_id)
            if live is None:
                drifted.append(step)
                continue
            if any(live.get(k) != v for k, v in expected.items()):
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


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False
