"""Unit-suffix contract: every numeric threshold names its unit.

This is the mechanical guardrail for the bug class where a value in one unit is
compared against a threshold in another (isp_loss judged a packet COUNT against a
percentage; the same shape as the historical dBm-vs-index and kbps-vs-Mbps bugs).
The codebase already follows the convention -- ``sticky_rssi_dbm``,
``capacity_degraded_pct``, ``wan_latency_abs_ms``, ``low_rate_mbps`` -- so this test
makes it enforceable: a NEW numeric threshold whose name carries no unit fails here.

Two surfaces are checked:

1. **Config dataclass fields** -- ``SleConfig`` (``netadmin/sle/classifiers.py``) and
   every ``@dataclass`` in ``netadmin/detect/detectors/*.py``. Every field with a
   numeric literal default must end in an allowed unit/quantity suffix.
2. **Detector threshold keys** -- every ``ctx.threshold(key, "<name>", <default>)``
   call site in the detector modules whose default resolves to a number.

The suffix list is a CLOSED allowlist. ``_CANONICAL_SUFFIXES`` is the documented
convention; ``_LEGACY_UNIT_SUFFIXES`` are unit/quantity suffixes already in genuine
use in the detector keys that predate the doc (Celsius ``_c``, Watts ``_w``, and a
handful of count/rate suffixes) -- real units, not unit-free names. Two small,
explicit EXEMPTION sets cover the genuinely dimensionless numerics (weights,
multipliers, factors); a reviewer adds to them consciously and never parks a new
unsuffixed threshold there.

Adding ``wan_loss_threshold`` back (unsuffixed) makes this test fail -- which is how
it caught the isp_loss bug's naming gap in the first place.
"""

from __future__ import annotations

import ast
from pathlib import Path

import netadmin.detect.detectors as _detectors_pkg
import netadmin.sle.classifiers as _classifiers_mod
from netadmin.detect.detectors.wan import StarlinkWanProfile

# The documented convention: the closed set of unit/quantity suffixes.
_CANONICAL_SUFFIXES: tuple[str, ...] = (
    "_dbm",
    "_db",
    "_mbps",
    "_kbps",
    "_ms",
    "_s",
    "_pct",
    "_fraction",
    "_ratio",
    "_polls",
    "_samples",
    "_probes",
    "_windows",
    "_count",
    "_min",
    "_max",
    "_sec",
    "_hours",
    "_bytes",
    "_mhz",
)

# Unit/quantity suffixes already used by pre-existing detector keys. Each is a real
# unit or a countable dimension, kept CLOSED so a genuinely unit-free name still
# fails. Renaming these live detector keys is out of scope for the isp_loss fix.
_LEGACY_UNIT_SUFFIXES: tuple[str, ...] = (
    "_c",  # degrees Celsius (chassis_hot_c, crit_c, drift_c, warn_c, module_temp_max_c)
    "_w",  # Watts (poe_reboot_floor_w)
    "_days",  # lookback_days
    "_hops",  # deep_hops
    "_ports",  # min_ports
    "_scans",  # persist_min_scans
    "_roams",  # burst_min_roams, min_bad_roams
    "_failures",  # consecutive_failures
    "_distance",  # overlap_24_distance (channel-index distance)
    "_per_h",  # definite_rate_per_h, suspicious_rate_per_h
    "_per_hour",  # min_post_disconnects_per_hour
)

_ALLOWED_SUFFIXES: tuple[str, ...] = _CANONICAL_SUFFIXES + _LEGACY_UNIT_SUFFIXES

# Genuinely dimensionless config-dataclass fields. Not unit-bearing by nature.
_EXEMPT_FIELDS: frozenset[str] = frozenset(
    {
        "sigmas",  # SleConfig: statistical sigma multiplier, dimensionless
        "bucket_seconds",  # SleConfig: spelled-out seconds (predates the _s abbreviation)
    }
)

# Genuinely dimensionless detector threshold keys (grandfathered; do NOT add a new
# unsuffixed *unit-bearing* threshold here -- that is the bug this test guards).
_EXEMPT_THRESHOLD_KEYS: frozenset[str] = frozenset(
    {
        "benign_weight",  # dimensionless scoring weight
        "default_weight",  # dimensionless scoring weight
        "weighted_threshold",  # dimensionless weighted-score cutoff
        "multiplier",  # dimensionless multiplier
        "regression_factor",  # dimensionless factor
        "min_baseline_delta",  # dimensionless baseline delta
        "transitions_long",  # transition count (long window); count, no unit
        "transitions_short",  # transition count (short window); count, no unit
        "transitions_sustained",  # transition count (24 h window); count, no unit
    }
)

_PROFILE = StarlinkWanProfile()


def _numeric_literal(node: ast.expr | None) -> bool:
    """True when ``node`` is a numeric literal (``5``, ``0.03``, ``-72``)."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return _numeric_literal(node.operand)
    return False


def _resolves_to_number(node: ast.expr | None) -> bool:
    """True when a ctx.threshold default resolves to a number.

    Handles numeric literals and ``_PROFILE.<attr>`` references (resolved against a
    real :class:`StarlinkWanProfile`), which is how the wan detectors spell defaults.
    """
    if _numeric_literal(node):
        return True
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "_PROFILE"
    ):
        val = getattr(_PROFILE, node.attr, None)
        return isinstance(val, (int, float)) and not isinstance(val, bool)
    return False


def _ok(name: str, exempt: frozenset[str]) -> bool:
    return name in exempt or name.endswith(_ALLOWED_SUFFIXES)


def _detector_files() -> list[Path]:
    root = Path(_detectors_pkg.__file__).parent
    return sorted(p for p in root.glob("*.py") if p.name != "__init__.py")


def _dataclass_field_offenders(path: Path) -> list[str]:
    """Numeric-literal-default dataclass fields whose name carries no unit."""
    tree = ast.parse(path.read_text())
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and _is_dataclass(node)):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                if _numeric_literal(stmt.value) and not _ok(stmt.target.id, _EXEMPT_FIELDS):
                    offenders.append(f"{path.name}:{node.name}.{stmt.target.id}")
    return offenders


def _is_dataclass(node: ast.ClassDef) -> bool:
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name) and target.id == "dataclass":
            return True
        if isinstance(target, ast.Attribute) and target.attr == "dataclass":
            return True
    return False


def _threshold_key_offenders(path: Path) -> list[str]:
    """ctx.threshold(...) numeric call sites whose name carries no unit."""
    tree = ast.parse(path.read_text())
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "threshold" or len(node.args) < 2:
            continue
        name_node = node.args[1]
        if not (isinstance(name_node, ast.Constant) and isinstance(name_node.value, str)):
            continue
        default = node.args[2] if len(node.args) >= 3 else None
        if not _resolves_to_number(default):
            continue
        name = name_node.value
        if not _ok(name, _EXEMPT_THRESHOLD_KEYS):
            offenders.append(f'{path.name}:"{name}"')
    return offenders


def test_config_dataclass_numeric_fields_carry_a_unit_suffix() -> None:
    files = [Path(_classifiers_mod.__file__)] + _detector_files()
    offenders = sorted({o for f in files for o in _dataclass_field_offenders(f)})
    assert not offenders, (
        "Numeric config fields must end in a unit/quantity suffix "
        f"(or be a conscious exemption): {offenders}"
    )


def test_detector_threshold_keys_carry_a_unit_suffix() -> None:
    offenders = sorted({o for f in _detector_files() for o in _threshold_key_offenders(f)})
    assert not offenders, (
        "Numeric ctx.threshold() keys must end in a unit/quantity suffix "
        f"(or be a conscious exemption): {offenders}"
    )
