"""The report model: typed, JSON-native dataclasses for the whole document.

The assembler (:mod:`netadmin.report.assembler`) builds one :class:`ReportModel`
from real repository queries and the router serialises it verbatim. Every field
here is a JSON-native scalar, list, dict, or nested dataclass, so the whole tree
round-trips through :func:`dataclasses.asdict` with no custom encoder -- the
router does exactly that. Nothing in this module computes a value; it is the
shape the assembler fills.

Section order and field names follow ``docs/REPORT_SPEC.md`` sections 1-11.
Optional numbers are ``None`` when the underlying data is absent (an honest
empty), never zero-filled -- the "no false data" gate lives in the *assembler*,
but the model makes the empty representable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

__all__ = [
    "DataWindow",
    "CoverMeta",
    "Scorecard",
    "TopFinding",
    "ExecutiveSummary",
    "SamplingSource",
    "CoverageEntry",
    "ScopeMethodology",
    "InventoryDevice",
    "Inventory",
    "TopologyNode",
    "Topology",
    "SleScoreView",
    "HealthSection",
    "RadioUtilization",
    "RfSection",
    "HistogramBin",
    "ClientsSection",
    "FindingImpact",
    "AffectedAsset",
    "SymptomRef",
    "Finding",
    "Recommendation",
    "RoadmapSection",
    "Appendix",
    "ReportModel",
    "report_to_dict",
]


# --------------------------------------------------------------------------- #
# Cover + shared window
# --------------------------------------------------------------------------- #
@dataclass
class DataWindow:
    """The window the report covers: ``[start_ts, end_ts)`` plus a human label."""

    start_ts: int
    end_ts: int
    duration_s: int
    label: str


@dataclass
class CoverMeta:
    """Cover page: site, when generated, the window, and the device/client counts."""

    site: str
    generated_ts: int
    tool: str
    version: str
    window: DataWindow
    counts: dict[str, int]
    confidentiality: str


# --------------------------------------------------------------------------- #
# Executive summary
# --------------------------------------------------------------------------- #
@dataclass
class Scorecard:
    """Overall health score plus findings-by-severity counts (CVSS levels).

    ``findings_by_severity`` counts every level in the ladder (0 where none), and
    those counts sum to ``total_findings`` -- the scorecard-math invariant.
    """

    health_score: Optional[int]
    posture: str
    findings_by_severity: dict[str, int]
    total_findings: int
    # Representative live-poll coverage over the window (%), and whether it is low
    # enough (<50%) that the headline number is under-observed. The caveat travels
    # with the score so a prominent 82/100 is never read as settled over a partial
    # window (docs/REPORT_SPEC.md honesty conventions).
    coverage_pct: Optional[int] = None
    low_confidence: bool = False
    confidence_note: Optional[str] = None


@dataclass
class TopFinding:
    """One headline finding in plain language for the executive summary."""

    id: str
    title: str
    severity: str
    plain: str


@dataclass
class ExecutiveSummary:
    """The one-page summary: verdict, scorecard, top findings, and next step."""

    verdict: str
    scorecard: Scorecard
    top_findings: list[TopFinding]
    recommendation_summary: str


# --------------------------------------------------------------------------- #
# Scope & methodology
# --------------------------------------------------------------------------- #
@dataclass
class SamplingSource:
    """One data source and how often it was sampled (cadence from config)."""

    source: str
    cadence_s: Optional[int]
    note: str


@dataclass
class CoverageEntry:
    """Measured poll coverage for one job over the window (honesty, not a guess)."""

    job: str
    interval_s: int
    fraction: float
    note: str


@dataclass
class ScopeMethodology:
    """What was assessed, from where, how densely, and the honest limitations."""

    data_sources: list[str]
    sampling: list[SamplingSource]
    window: DataWindow
    coverage: list[CoverageEntry]
    limitations: list[str]


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #
@dataclass
class InventoryDevice:
    """One infrastructure device row: name, model, role, uplink."""

    entity_id: int
    name: str
    model: Optional[str]
    role: str
    uplink: Optional[str]


@dataclass
class Inventory:
    """Count tiles + the device table."""

    counts: dict[str, int]
    devices: list[InventoryDevice]


# --------------------------------------------------------------------------- #
# Topology
# --------------------------------------------------------------------------- #
@dataclass
class TopologyNode:
    """A node in the layered diagram, with enough for the UI to draw the link.

    ``uplink`` is ``wire`` / ``wireless`` / ``None``; ``mesh_uplink_rssi`` and
    ``backhaul_status`` are present only for a wireless (mesh) uplink;
    ``client_count`` is how many clients ride this node; ``parent_id`` gives the
    edge to draw upward.
    """

    entity_id: int
    name: str
    model: Optional[str]
    role: str
    uplink: Optional[str]
    parent_id: Optional[int]
    mesh_uplink_rssi: Optional[float]
    backhaul_status: Optional[str]
    client_count: int


@dataclass
class Topology:
    """Gateway, switches, and APs as drawable nodes, plus the mesh legend thresholds."""

    gateway: Optional[TopologyNode]
    switches: list[TopologyNode]
    aps: list[TopologyNode]
    backhaul_thresholds: dict[str, float]


# --------------------------------------------------------------------------- #
# Health & performance
# --------------------------------------------------------------------------- #
@dataclass
class SleScoreView:
    """One SLE's score (0-100 or None) with its worst offenders, resolved to names."""

    sle: str
    score: Optional[int]
    total_minutes: float
    fail_minutes: float
    top_offenders: list[dict[str, Any]]
    # The window was under-observed (<50% live poll coverage): a perfect 100/100
    # here is under-sampled, not proven, so the UI flags it rather than presenting
    # it as settled (docs/REPORT_SPEC.md honesty conventions).
    low_confidence: bool = False


@dataclass
class HealthSection:
    """Headline score, per-SLE scores, and the health-trend series over the window."""

    headline_score: Optional[int]
    sles: list[SleScoreView]
    trend: list[dict[str, Any]]


# --------------------------------------------------------------------------- #
# RF environment
# --------------------------------------------------------------------------- #
@dataclass
class RadioUtilization:
    """One radio's channel utilisation, split self vs non-self where known."""

    entity_id: int
    ap_name: str
    band: Optional[str]
    channel: Optional[str]
    cu_total: Optional[float]
    cu_self: Optional[float]
    cu_non_self: Optional[float]


@dataclass
class RfSection:
    """Channel utilisation per radio + aggregated neighbour density, framed honestly."""

    utilization: list[RadioUtilization]
    utilization_reference_pct: float
    neighbor_density: dict[str, Any]
    neighbor_summary: str


# --------------------------------------------------------------------------- #
# Client analysis
# --------------------------------------------------------------------------- #
@dataclass
class HistogramBin:
    """One RSSI bin: half-open ``[floor, ceil)``, count, and the weak-tail flag."""

    floor: Optional[int]
    ceil: Optional[int]
    count: int
    weak: bool


@dataclass
class ClientsSection:
    """RSSI distribution, clients-per-AP load, and the worst-devices table."""

    rssi_histogram: dict[str, Any]
    clients_per_ap: list[dict[str, Any]]
    worst_devices: list[dict[str, Any]]
    clients_without_rssi: int


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #
@dataclass
class FindingImpact:
    """A finding's impact in SLE-fail-minute / affected-client terms."""

    fail_minutes: float
    fail_client_hours: float
    affected_clients: int
    summary: str


@dataclass
class AffectedAsset:
    """An entity a finding is about (the fix target or an affected device/radio)."""

    entity_id: int
    name: str
    type: str
    role: str


@dataclass
class SymptomRef:
    """A correlated symptom rolled under a finding's root (section 17 grouping)."""

    issue_id: int
    detector_key: str
    title: str
    entity: Optional[dict[str, Any]]
    rule: Optional[str]
    rationale: Optional[str]


@dataclass
class Finding:
    """One finding in the fixed template (``docs/REPORT_SPEC.md`` section 42-53)."""

    id: str
    title: str
    severity: str
    netadmin_severity: Optional[str]
    detector_key: str
    affected_assets: list[AffectedAsset]
    observation: str
    evidence: dict[str, Any]
    impact: FindingImpact
    root_cause: str
    recommendation: str
    confounders_checked: list[str]
    signature: str
    incident_id: Optional[int]
    symptoms: list[SymptomRef]
    source_issue_ids: list[int]


# --------------------------------------------------------------------------- #
# Recommendations / roadmap
# --------------------------------------------------------------------------- #
@dataclass
class Recommendation:
    """A ranked, phased, finding-traceable recommendation."""

    finding_id: str
    title: str
    severity: str
    phase: str
    text: str


@dataclass
class RoadmapSection:
    """Recommendations grouped into now / soon / strategic phases."""

    now: list[Recommendation]
    soon: list[Recommendation]
    strategic: list[Recommendation]


# --------------------------------------------------------------------------- #
# Appendix
# --------------------------------------------------------------------------- #
@dataclass
class Appendix:
    """Severity rubric, thresholds used, methodology detail, and a glossary."""

    severity_rubric: list[dict[str, str]]
    thresholds: dict[str, Any]
    methodology: dict[str, Any]
    glossary: list[dict[str, str]]


# --------------------------------------------------------------------------- #
# The whole report
# --------------------------------------------------------------------------- #
@dataclass
class ReportModel:
    """The full report model returned by ``GET /api/report``."""

    cover: CoverMeta
    executive_summary: ExecutiveSummary
    scope: ScopeMethodology
    inventory: Inventory
    topology: Topology
    health: HealthSection
    rf: RfSection
    clients: ClientsSection
    findings: list[Finding]
    roadmap: RoadmapSection
    appendix: Appendix
    generated_ts: int = field(default=0)


def report_to_dict(model: ReportModel) -> dict[str, Any]:
    """Serialise a :class:`ReportModel` to a JSON-native dict for the router.

    Every field is already JSON-native or a nested dataclass, so a single
    :func:`dataclasses.asdict` recursion is the whole encoder -- no enum or custom
    type slips through.
    """
    return asdict(model)
