"""Data shapes for the fix engine (``docs/ARCHITECTURE.md`` section 9).

Pure data, stdlib only. A :class:`FixPlan` is the planner's output: an ordered
list of :class:`FixStep` objects, each carrying the *exact* controller payload it
would send, the current state it expects (:class:`Precondition`), the before/after
snapshots that make it revertible, a human description, and a risk level. Nothing
here does I/O; the applier (:mod:`netadmin.fixes.applier`) renders and, only on an
explicit confirmed apply, sends these through the single mutation seam
(:class:`~netadmin.fixes.writer.ControllerWriter`).

The central safety property is that a plan is fully inspectable before anything is
sent: ``dry_run`` (the default) renders these payloads verbatim and touches no
network, and the ``confirm_token`` a real apply must present is a digest of the
exact payloads below, so a human confirms the bytes they reviewed and nothing else.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from netadmin.domain.types import EntityType

__all__ = [
    "RiskLevel",
    "ActionType",
    "Precondition",
    "FixStep",
    "FixPlan",
    "WriteResult",
    "StepResult",
    "DryRunResult",
    "ApplyResult",
    "VerificationStatus",
    "VerificationResult",
    "FixError",
    "ConfirmTokenError",
    "PreconditionDrift",
    "MaxStepsExceeded",
    "SafetyViolation",
    "WriterRequired",
    "plan_confirm_token",
]


class RiskLevel(str, Enum):
    """How disruptive a step is when it lands (drives UI emphasis + gating)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ActionType(str, Enum):
    """The concrete remediation a step performs. Only the safe, high-value set
    from section 9 is planned automatically; everything else is advisory."""

    CHANNEL_CHANGE = "wifi.channel_change"
    TX_POWER_STEP_DOWN = "wifi.tx_power_step_down"
    MIN_RSSI_REMOVE = "wifi.min_rssi_remove"
    POE_POWER_CYCLE = "wired.poe_power_cycle"


# ---------------------------------------------------------------------------- #
# Exceptions
# ---------------------------------------------------------------------------- #
class FixError(Exception):
    """Base class for every fix-engine refusal."""


class ConfirmTokenError(FixError):
    """A real apply was requested without a token matching the rendered plan.

    The token is a digest of the exact payloads (:func:`plan_confirm_token`); a
    mismatch means the caller never dry-ran *this* plan, so we refuse rather than
    send bytes no human reviewed.
    """


class PreconditionDrift(FixError):
    """Live state no longer matches what a step expected; the whole plan aborts.

    Carries the drifted steps so the caller can re-plan against fresh state.
    """

    def __init__(self, message: str, drifted: Optional[list["FixStep"]] = None) -> None:
        super().__init__(message)
        self.drifted = drifted or []


class MaxStepsExceeded(FixError):
    """The plan touches more steps/devices than the applier's hard guard allows."""


class SafetyViolation(FixError):
    """A step would breach an absolute rail (e.g. *setting* min-RSSI on a mesh AP)."""


class WriterRequired(FixError):
    """A real apply was requested but no :class:`ControllerWriter` was injected."""


# ---------------------------------------------------------------------------- #
# Plan shapes
# ---------------------------------------------------------------------------- #
@dataclass
class Precondition:
    """The live state a step expects before it will apply.

    ``expected`` is a flat map of attribute -> value keyed on the entity named by
    ``target_native_id``. At apply time the applier compares it against a freshly
    read ``current_state`` snapshot; any mismatch (or missing key) is drift and
    aborts the entire plan. An empty ``expected`` always passes (nothing to drift).
    """

    target_native_id: str
    expected: dict[str, Any] = field(default_factory=dict)
    description: str = ""


@dataclass
class FixStep:
    """One concrete, revertible mutation the planner proposes.

    ``method``/``endpoint``/``payload`` are the exact site-relative controller call
    a dry-run renders and a confirmed apply sends -- nothing is computed later. The
    endpoint is site-relative (``rest/device/<id>``, ``cmd/devmgr``); the writer's
    client resolves the ``s/<site>/`` prefix. ``before`` is the self-contained
    revert instruction (``{"method","endpoint","body"}`` or ``None`` for a
    non-revertible command like a power-cycle); ``after`` mirrors the applied call.
    """

    action: ActionType
    target_entity_type: EntityType
    target_native_id: str
    description: str
    risk: RiskLevel
    method: str
    endpoint: str
    payload: dict[str, Any]
    precondition: Precondition
    before: Optional[dict[str, Any]] = None
    after: dict[str, Any] = field(default_factory=dict)
    revertible: bool = True


@dataclass
class FixPlan:
    """The planner's output for one finding/issue.

    A real plan has ``steps``; an advisory plan (physical fix, or a detector with
    no safe template) has ``steps == []``, ``manual_action_required == True``, and
    an ``advisory`` note explaining what a human must do on site. ``entity_native_id``
    is the finding's entity; ``issue_id`` links the plan back to the tracked issue
    for the changes ledger and fix-verification window.
    """

    detector_key: str
    entity_native_id: str
    title: str
    steps: list[FixStep] = field(default_factory=list)
    advisory: Optional[str] = None
    manual_action_required: bool = False
    issue_id: Optional[int] = None

    @property
    def is_advisory(self) -> bool:
        return not self.steps

    @property
    def device_count(self) -> int:
        """Distinct target devices the plan mutates (the max-N guard counts these).

        A radio native id is ``"<mac>:<band>"`` and a port native id is
        ``"<mac>:<idx>"`` -- seven colon-separated segments, one more than the
        six-octet device MAC underneath. Stripping that trailing segment collapses
        an AP whose two radios both change to a single device, matching "never
        change more than N devices per apply". A bare device MAC (five colons) is
        already a device id and is kept whole.
        """
        return len({_device_of(s.target_native_id) for s in self.steps})


@dataclass
class WriteResult:
    """The outcome of one mutation through a :class:`ControllerWriter`.

    ``ok`` is the single source of truth the applier branches on; ``status_code``
    and ``data`` carry the controller's response for the ledger and debugging.
    """

    ok: bool
    status_code: Optional[int] = None
    data: Any = None


@dataclass
class StepResult:
    """Per-step record of a real apply: what was sent and how it landed."""

    step: FixStep
    status: str  # "applied" | "failed" | "skipped"
    change_id: Optional[int] = None
    write: Optional[WriteResult] = None
    error: Optional[str] = None


@dataclass
class DryRunResult:
    """What ``dry_run=True`` returns: the rendered calls and the confirm token.

    Sends nothing. ``rendered`` is the exact list of ``{method, endpoint, payload,
    description, risk}`` a real apply would send; ``confirm_token`` is the digest a
    subsequent real apply must present to prove it reviewed this exact render.
    """

    plan: FixPlan
    rendered: list[dict[str, Any]]
    confirm_token: str
    manual_action_required: bool = False
    advisory: Optional[str] = None


@dataclass
class ApplyResult:
    """What a real apply returns: per-step outcomes and the ledger row ids."""

    plan: FixPlan
    applied: bool
    steps: list[StepResult] = field(default_factory=list)
    change_ids: list[int] = field(default_factory=list)
    aborted_reason: Optional[str] = None


class VerificationStatus(str, Enum):
    """Where an applied fix sits in its verification window (section 7)."""

    NOT_ARMED = "not_armed"  # no fix_applied event on the issue
    PENDING = "pending"  # applied, window open, issue not yet resolved
    VERIFIED = "verified"  # issue resolved inside the window
    FAILED = "failed"  # issue refired / fix marked failed
    EXPIRED = "expired"  # window lapsed without resolution (unverified)


@dataclass
class VerificationResult:
    """Snapshot of an applied fix's verification state for the UI/CLI."""

    issue_id: int
    status: VerificationStatus
    armed_ts: Optional[int] = None
    window_end_ts: Optional[int] = None
    resolved_ts: Optional[int] = None


def _device_of(native_id: str) -> str:
    """The device MAC underlying a target native id.

    A radio (``"<mac>:<band>"``) or port (``"<mac>:<idx>"``) native id carries a
    trailing segment beyond the six-octet MAC (six colons total); strip it. A bare
    device MAC has five colons and is returned unchanged.
    """
    if native_id.count(":") >= 6:
        return native_id.rsplit(":", 1)[0]
    return native_id


# ---------------------------------------------------------------------------- #
# Confirm token
# ---------------------------------------------------------------------------- #
def plan_confirm_token(plan: FixPlan) -> str:
    """Deterministic digest of a plan's exact payloads.

    A real apply must present a token equal to this, recomputed from the plan in
    hand, so confirmation is bound to the precise ``(method, endpoint, payload)``
    the human dry-ran. Canonicalized with sorted keys so dict ordering never shifts
    the hash. Advisory plans (no steps) still hash deterministically (over an empty
    step list) so the token API is uniform.
    """
    material = {
        "detector_key": plan.detector_key,
        "entity": plan.entity_native_id,
        "steps": [
            {"method": s.method, "endpoint": s.endpoint, "payload": s.payload} for s in plan.steps
        ],
    }
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
