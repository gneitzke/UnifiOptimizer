"""Playbook-backed root-cause and recommendation text for a finding.

The report's findings template needs a ``root_cause`` and a specific
``recommendation`` for every entry (``docs/REPORT_SPEC.md``: "A finding without a
concrete, specific recommendation is not done"). netadmin already carries that
knowledge on the detector catalog: each :class:`~netadmin.detect.catalog.Playbook`
holds the detector's ``signature`` (the measured pattern that identifies the
cause), its ``confounders`` (the false-positive traps), and its ``fix_guidance``
(the ordered remediation). This module reads that catalog and returns the two
strings the template needs, plus the diagnostic signature and confounders for the
appendix, so nothing here is authored prose the report invents.

When a detector has no registered playbook the helper returns an **honest
advisory** that names the detector and says no automated guidance exists, never a
vague filler recommendation. That is the spec's "honest advisory" branch, and it
is specific by construction (it names the exact detector key).
"""

from __future__ import annotations

from dataclasses import dataclass

from netadmin.detect.catalog import DEFAULT_CATALOG, Catalog

__all__ = ["PlaybookGuidance", "finding_guidance"]


@dataclass(frozen=True)
class PlaybookGuidance:
    """The catalog-backed guidance for one detector, ready for a finding entry.

    ``has_playbook`` is False when the detector carries no field guide, in which
    case ``root_cause`` / ``recommendation`` are the honest advisory (they still
    name the detector) rather than fabricated text.
    """

    detector_key: str
    root_cause: str
    recommendation: str
    signature: str
    confounders: str
    has_playbook: bool


def finding_guidance(
    detector_key: str,
    *,
    catalog: Catalog = DEFAULT_CATALOG,
    correlated_symptoms: int = 0,
) -> PlaybookGuidance:
    """Resolve a detector key to its report guidance from the catalog playbook.

    ``correlated_symptoms`` (>0 for a grouped incident) prefixes the root cause
    with the correlation context so the reader sees this is the one thing to fix
    for N symptoms, not N separate problems. An unregistered detector key yields
    the honest no-playbook advisory.
    """
    entry = None
    try:
        entry = catalog.get(detector_key)
    except KeyError:
        entry = None

    playbook = entry.playbook if entry is not None else None
    if playbook is None:
        advisory = (
            f"No detector playbook is registered for {detector_key}. "
            "Review the observation and evidence below and remediate manually."
        )
        return PlaybookGuidance(
            detector_key=detector_key,
            root_cause=advisory,
            recommendation=advisory,
            signature="",
            confounders="",
            has_playbook=False,
        )

    root_cause = playbook.signature
    if correlated_symptoms > 0:
        root_cause = (
            f"Root cause of {correlated_symptoms} correlated "
            f"symptom{'s' if correlated_symptoms != 1 else ''}. {root_cause}"
        )

    recommendation = playbook.fix_guidance or (
        f"No fix guidance is registered for {detector_key}; remediate per the " "observed evidence."
    )

    return PlaybookGuidance(
        detector_key=detector_key,
        root_cause=root_cause,
        recommendation=recommendation,
        signature=playbook.signature,
        confounders=playbook.confounders,
        has_playbook=True,
    )
