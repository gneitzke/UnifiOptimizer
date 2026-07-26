"""CVSS-aligned severity ladder for the report, mapped from netadmin's P1/P2/P3.

The report speaks the reader's language (Critical / High / Medium / Low / Info),
not the engine's internal ``p1/p2/p3``. This module owns the one mapping used end
to end (``docs/REPORT_SPEC.md``: "the same chip colour in the scorecard, the
findings table, and any per-entity status") so the scorecard, findings, and
appendix rubric never disagree.

The mapping is refined by *measured impact* rather than a flat table
(``P1 -> Critical/High`` etc., "per the SLE impact"): a P1 that actually burned
failed SLE client-minutes over the window is Critical; a P1 with no measured
client impact yet is High. This keeps the top of the ladder for problems that
are demonstrably hurting users, and every step traces to a real number
(``sle_minutes``), never a guess.

Colours reconcile ``docs/REPORT_SPEC.md`` (Critical ``#d70015``, Low green
``#1e7a34``, Info grey) with the AA-verified severity ramp in
``docs/DESIGN_FOUNDATION.md``: Critical red, High orange, Medium amber, Low
green, Info grey, each with a dark-mode variant re-picked for contrast (never an
inverted light fill). The backend ships the ramp so the UI renders one system and
computes no colour of its own.
"""

from __future__ import annotations

from typing import Optional

from netadmin.domain.types import Severity

__all__ = [
    "CRITICAL",
    "HIGH",
    "MEDIUM",
    "LOW",
    "INFO",
    "CVSS_ORDER",
    "SEVERITY_COLORS",
    "cvss_rank",
    "to_cvss",
    "severity_rubric",
]

CRITICAL = "critical"
HIGH = "high"
MEDIUM = "medium"
LOW = "low"
INFO = "info"

# Most severe first. Sort keys and rank lookups both derive from this one tuple.
CVSS_ORDER: tuple[str, ...] = (CRITICAL, HIGH, MEDIUM, LOW, INFO)

# AA-verified text/glyph colours per level, light and dark. Sourced from
# docs/DESIGN_FOUNDATION.md (contrast re-verified there); Critical and Low keep
# the explicit docs/REPORT_SPEC.md hexes.
SEVERITY_COLORS: dict[str, dict[str, str]] = {
    CRITICAL: {"light": "#D70015", "dark": "#FF6961"},
    HIGH: {"light": "#C93400", "dark": "#FFB340"},
    MEDIUM: {"light": "#B25000", "dark": "#FFD426"},
    LOW: {"light": "#1E7A34", "dark": "#30DB5B"},
    INFO: {"light": "#6E6E73", "dark": "#8E8E93"},
}

_RANK: dict[str, int] = {level: i for i, level in enumerate(CVSS_ORDER)}

# One-line meaning per level for the appendix rubric.
_MEANING: dict[str, str] = {
    CRITICAL: "Users are affected now; address before other work.",
    HIGH: "A real fault with user impact; schedule promptly.",
    MEDIUM: "A confirmed problem without measured client impact yet.",
    LOW: "A minor or advisory item; fix when convenient.",
    INFO: "Environmental context, not an action item on its own.",
}

# Which netadmin severities each level can come from, for the rubric's provenance
# column (the reader sees how the engine's P-levels map to the ladder).
_NETADMIN_SOURCE: dict[str, str] = {
    CRITICAL: "P1 with measured SLE impact",
    HIGH: "P1 without measured impact, or P2 with impact",
    MEDIUM: "P2 without measured impact",
    LOW: "P3",
    INFO: "aggregated environmental context",
}


def cvss_rank(level: str) -> int:
    """Sort rank for a CVSS level: 0 = Critical (most severe), 4 = Info.

    An unknown level sorts after Info so a stray value never jumps the ranking.
    """
    return _RANK.get(level, len(CVSS_ORDER))


def to_cvss(
    netadmin_severity: Optional[str],
    *,
    fail_minutes: float = 0.0,
    environmental: bool = False,
) -> str:
    """Map a netadmin ``p1/p2/p3`` to the CVSS ladder, refined by measured impact.

    ``fail_minutes`` is the failed SLE client-minutes the finding accounts for
    over the window (from ``sle_minutes``); it is what separates Critical from
    High (P1) and High from Medium (P2). ``environmental`` marks the aggregated
    neighbour/channel-plan context finding, which is Info unless it wraps a graver
    issue. A ``None`` severity is Info.
    """
    sev = netadmin_severity
    if isinstance(sev, Severity):
        sev = sev.value
    has_impact = fail_minutes > 0
    if sev == Severity.P1.value:
        return CRITICAL if has_impact else HIGH
    if sev == Severity.P2.value:
        return HIGH if has_impact else MEDIUM
    if sev == Severity.P3.value:
        return LOW
    return INFO


def severity_rubric() -> list[dict[str, str]]:
    """The full ladder for the appendix: level, label, provenance, meaning, colour.

    The single source the UI legend and every finding chip read from, so nothing
    downstream reinvents a colour or a label.
    """
    return [
        {
            "level": level,
            "label": level.capitalize(),
            "netadmin_source": _NETADMIN_SOURCE[level],
            "meaning": _MEANING[level],
            "color_light": SEVERITY_COLORS[level]["light"],
            "color_dark": SEVERITY_COLORS[level]["dark"],
        }
        for level in CVSS_ORDER
    ]
