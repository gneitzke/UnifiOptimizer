"""The report's severity ladder: a direct rename of netadmin's P1/P2/P3.

Gitea #22: the report used to speak a five-level "CVSS-aligned" vocabulary
(Critical/High/Medium/Low/Info) derived from P1/P2/P3 *and* the finding's
measured SLE impact -- the same P1 could render as Critical in one report and
High in another, and the app's severity filter (P1/P2/P3, ``SeverityPill``)
never agreed with the report's words at all. That was two taxonomies wearing
one UI, and the mapping between them was lossy enough to mislead.

There is now ONE taxonomy, used by both surfaces:

    P1 -> Critical      P2 -> High      P3 -> Low

a fixed, static rename -- never conditioned on impact, never re-derived per
render. ``netadmin/report/models.py``'s ``Finding.impact`` still carries the
real fail-minutes/affected-clients numbers; they inform the reader, they no
longer change the severity word. ``Info`` is reserved for the one aggregated
environmental finding (neighbour density + channel-plan contention): it is
context, not a P-level fault, so it never becomes an "action item" regardless
of which underlying issues it summarises.

Colours match ``docs/DESIGN_FOUNDATION.md``'s severity ramp -- the SAME hexes
the app's ``SeverityPill`` uses for P1/P2/P3 (red/orange/amber), so a Critical
chip in the report and a P1 pill in the app are the identical colour, not two
palettes that happen to rhyme. Each level also carries a distinct shape glyph
(octagon/triangle/circle/bar), never colour alone.
"""

from __future__ import annotations

from typing import Optional

from netadmin.domain.types import Severity

__all__ = [
    "CRITICAL",
    "HIGH",
    "LOW",
    "INFO",
    "SEVERITY_ORDER",
    "SEVERITY_COLORS",
    "severity_rank",
    "to_severity_label",
    "severity_rubric",
]

CRITICAL = "critical"
HIGH = "high"
LOW = "low"
INFO = "info"

# Most severe first. Sort keys and rank lookups both derive from this one tuple.
SEVERITY_ORDER: tuple[str, ...] = (CRITICAL, HIGH, LOW, INFO)

# AA-verified text/glyph colours per level, light and dark -- identical to the
# app's SeverityPill (P1 red / P2 orange / P3 amber) plus a neutral grey for
# Info, so the report and the app never disagree on what a severity looks like.
SEVERITY_COLORS: dict[str, dict[str, str]] = {
    CRITICAL: {"light": "#D70015", "dark": "#FF6961"},
    HIGH: {"light": "#C93400", "dark": "#FFB340"},
    LOW: {"light": "#B25000", "dark": "#FFD426"},
    INFO: {"light": "#6E6E73", "dark": "#8E8E93"},
}

_RANK: dict[str, int] = {level: i for i, level in enumerate(SEVERITY_ORDER)}

# One-line meaning per level for the appendix rubric.
_MEANING: dict[str, str] = {
    CRITICAL: "The highest-urgency category (P1): address before other work.",
    HIGH: "A confirmed fault (P2): schedule promptly.",
    LOW: "A minor or advisory item (P3): fix when convenient.",
    INFO: "Environmental context, not an action item on its own.",
}

# Provenance for the appendix rubric's "where this comes from" column.
_NETADMIN_SOURCE: dict[str, str] = {
    CRITICAL: "P1",
    HIGH: "P2",
    LOW: "P3",
    INFO: "aggregated environmental context (no discrete P-level)",
}


def severity_rank(level: str) -> int:
    """Sort rank for a severity level: 0 = Critical (most severe), 3 = Info.

    An unknown level sorts after Info so a stray value never jumps the ranking.
    """
    return _RANK.get(level, len(SEVERITY_ORDER))


def to_severity_label(netadmin_severity: Optional[str], *, environmental: bool = False) -> str:
    """Rename a netadmin ``p1/p2/p3`` to its report word: a fixed 1:1 mapping.

    ``environmental`` marks the aggregated neighbour/channel-plan context
    finding, which is always Info -- it summarises environmental readings, not
    a single actionable fault, regardless of the P-level of the issues it rolls
    up (the report still records that provenance in ``Finding.netadmin_severity``
    for anyone who wants it).
    """
    if environmental:
        return INFO
    sev = netadmin_severity
    if isinstance(sev, Severity):
        sev = sev.value
    if sev == Severity.P1.value:
        return CRITICAL
    if sev == Severity.P2.value:
        return HIGH
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
        for level in SEVERITY_ORDER
    ]
