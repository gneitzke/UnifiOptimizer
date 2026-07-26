"""netadmin.fixes: remediation planner, applier, and verifier (ARCHITECTURE §9).

The fix engine is the only part of netadmin that can mutate the controller, and it
is built so nothing mutates by accident: the planner is pure, dry-run is the
default, a real apply is gated on a confirm token and a live precondition re-check,
and every mutation goes through the one :class:`ControllerWriter` seam.

* :func:`plan_fix` -- finding -> :class:`FixPlan` (concrete steps, or advisory).
* :class:`Applier` -- render (default) / gated apply / revert against the ledger.
* :class:`Verifier` -- arm and read the issue-engine fix-verification window.
* :class:`ControllerWriter` -- the single mutation interface;
  :class:`RealControllerWriter` is the only code that sends a mutating call,
  :class:`FakeControllerWriter` is its non-networked test double.
"""

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
    StepResult,
    VerificationResult,
    VerificationStatus,
    WriteResult,
    WriterRequired,
    plan_confirm_token,
)
from netadmin.fixes.planner import PHYSICAL_REFUSAL_KEYS, plan_fix
from netadmin.fixes.reader import DeviceReader, FakeDeviceReader, RealDeviceReader, device_mac_of
from netadmin.fixes.service import FixSeams, FixService, IssueNotFound, build_fix_seams
from netadmin.fixes.verifier import Verifier
from netadmin.fixes.writer import ControllerWriter, FakeControllerWriter, RealControllerWriter

__all__ = [
    "plan_fix",
    "PHYSICAL_REFUSAL_KEYS",
    "Applier",
    "Verifier",
    "FixService",
    "FixSeams",
    "IssueNotFound",
    "build_fix_seams",
    "DeviceReader",
    "RealDeviceReader",
    "FakeDeviceReader",
    "device_mac_of",
    "ControllerWriter",
    "RealControllerWriter",
    "FakeControllerWriter",
    "FixPlan",
    "FixStep",
    "Precondition",
    "ActionType",
    "RiskLevel",
    "WriteResult",
    "StepResult",
    "DryRunResult",
    "ApplyResult",
    "VerificationResult",
    "VerificationStatus",
    "plan_confirm_token",
    "FixError",
    "ConfirmTokenError",
    "PreconditionDrift",
    "MaxStepsExceeded",
    "SafetyViolation",
    "WriterRequired",
]
