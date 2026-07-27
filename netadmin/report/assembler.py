"""The report assembler: the single source of truth for the report model.

``GET /api/report`` returns exactly what :func:`build_report` produces; the UI
renders it and computes nothing (``docs/ARCHITECTURE.md`` 19). Every number here
traces to a repository query -- SLE scores from ``sle_minutes``, offenders from
the composite ranking, inventory/topology from ``entities`` + ``state_changes`` +
``samples``, RF from radio ``cu_total`` and the aggregated ``rogue_bss`` table,
findings from confirmed issues grouped by incident. When the data for a field is
absent the field is an honest empty (``None`` / ``[]`` / a stated "no data"), never
a fabricated value: that is the "no false data" gate, enforced at the seam that
builds the model.

The assembler is read-only. It opens no transaction, calls only ``Repository``
read methods and the pure analytics/SLE/chart helpers, and mutates nothing -- safe
to run against a read-only snapshot.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from netadmin import __version__
from netadmin.analytics.offenders import CLIENT_ENTITY_TYPES, rank_offenders
from netadmin.config import PollIntervals
from netadmin.domain.types import EntityType
from netadmin.report import charts
from netadmin.report.models import (
    AffectedAsset,
    Appendix,
    ClientsSection,
    CoverageEntry,
    CoverMeta,
    DataWindow,
    ExecutiveSummary,
    Finding,
    FindingImpact,
    HealthSection,
    Inventory,
    InventoryDevice,
    RadioUtilization,
    Recommendation,
    ReportModel,
    RfSection,
    RoadmapSection,
    SamplingSource,
    ScopeMethodology,
    Scorecard,
    SleScoreView,
    SymptomRef,
    TopFinding,
    Topology,
    TopologyNode,
)
from netadmin.report.playbook import finding_guidance
from netadmin.report.severity import (
    CRITICAL,
    HIGH,
    INFO,
    LOW,
    SEVERITY_ORDER,
    severity_rank,
    severity_rubric,
    to_severity_label,
)
from netadmin.sle.classifiers import ALL_SLES, OK, SleConfig
from netadmin.sle.scores import load_weights, sle_scores
from netadmin.store.repository import SLE_CLIENT_AXIS_SLES, Repository

__all__ = ["build_report", "DEFAULT_WINDOW_S"]

DEFAULT_WINDOW_S = 7 * 86_400
MIN_WINDOW_S = 3_600
MAX_WINDOW_S = 400 * 86_400  # ~13 months (daily rollups are kept forever)
HEALTH_TREND_BUCKETS = 96
WORST_DEVICES_TOP_N = 5
TOP_FINDINGS_N = 5

# The two detectors whose findings are RF-environmental context: neighbour
# density (already one issue per band, never per BSSID) and channel-plan
# contention. They are pulled out of the incident grouping and summarised as ONE
# environmental finding (docs/REPORT_SPEC.md anti-patterns). wifi.rogue_ap is
# deliberately NOT here: since the taxonomy split it carries only security claims
# (an SSID spoof, a controller-flagged rogue), which must surface as their own
# ranked findings rather than be buried in an environmental summary.
ENVIRONMENTAL_KEYS = frozenset({"wifi.neighbor_density", "wifi.channel_plan"})

# Only confirmed issues become findings. A ``pending`` issue is unconfirmed and may
# still clear; reporting it would be a fabricated problem (correlation excludes it
# too, section 17). ``resolved`` is closed.
CONFIRMED_STATES = frozenset({"active", "resolving"})

# entities.entity_type for neighbour/rogue BSS rows (mirrors
# netadmin.detect.detectors.wifi.ROGUE_BSS_TYPE; a plain string, not a managed
# EntityType, kept here so the report package does not import the detector stack).
ROGUE_BSS_TYPE = "rogue_bss"

# entities.native_id prefix for the synthetic probe-target GATEWAY the probe
# factory upserts on a gateway-less site (mirrors netadmin.ingest.factory
# _PROBE_TARGET_PREFIX). It is not a managed UniFi device, so it is kept out of
# the device inventory and relabelled honestly in the topology, never surfaced as
# a raw "probe target" gateway (docs/REPORT_SPEC.md deliverable-identity rule).
_PROBE_TARGET_PREFIX = "probe_target:"

# poll_runs.job names (the stable job-name contract mirrored from
# netadmin.ingest.collector / netadmin.ingest.probes -- strings, not code) used to
# measure real coverage for the scope section.
_JOB_DEVICE = "fast_device"
_JOB_STA = "fast_sta"
_JOB_HEALTH = "fast_health"
_JOB_GW_RTT = "probe.gw_rtt"

# Mesh backhaul health bands (dBm), from the wifi.mesh_uplink playbook (-65/-70).
BACKHAUL_GOOD_DBM = -65.0
BACKHAUL_WARN_DBM = -70.0

# Channel-utilisation reference line (docs/REPORT_SPEC.md: "70% reference line").
UTILIZATION_REFERENCE_PCT = 70.0

_FAMILY_PREFIX: dict[str, str] = {
    "wifi": "WLAN",
    "wired": "LAN",
    "client": "CLI",
    "wan": "WAN",
    "net": "NET",
    "infra": "INF",
}

_SEV_RANK: dict[str, int] = {"p1": 0, "p2": 1, "p3": 2}

_PHASE_BY_SEVERITY: dict[str, str] = {
    CRITICAL: "now",
    HIGH: "soon",
    LOW: "strategic",
    INFO: "strategic",
}


# --------------------------------------------------------------------------- #
# Small pure helpers
# --------------------------------------------------------------------------- #
def _now() -> int:
    return int(time.time())


def _decode_json(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _entity_ref(row: Any) -> Optional[dict[str, Any]]:
    """Compact ``{entity_id, name, type, native_id, model}`` ref (name falls back to MAC)."""
    if row is None:
        return None
    name = row["name"]
    return {
        "entity_id": int(row["entity_id"]),
        "name": name if name else row["native_id"],
        "type": row["entity_type"],
        "native_id": row["native_id"],
        "model": row["model"],
    }


def _latest_metric(samples: list[dict[str, Any]], metric: str) -> Optional[float]:
    for s in samples:
        if s.get("metric") == metric and s.get("value") is not None:
            return float(s["value"])
    return None


def _norm_band(raw: Any, channel: Optional[int]) -> Optional[str]:
    """Normalised band label (``2.4`` / ``5`` / ``6``) from a code + channel fallback."""
    if isinstance(raw, str):
        r = raw.lower()
        if r in ("ng", "2.4", "2g", "2.4ghz"):
            return "2.4"
        if r in ("na", "5", "5g", "5ghz"):
            return "5"
        if r in ("6", "6e", "6g", "6ghz"):
            return "6"
    if channel is not None:
        if 1 <= channel <= 14:
            return "2.4"
        if 32 <= channel <= 177:
            return "5"
    return None


def _window_label(window_s: int) -> str:
    days = window_s // 86_400
    if days >= 1:
        return f"{days} day{'s' if days != 1 else ''}"
    hours = max(1, window_s // 3_600)
    return f"{hours} hour{'s' if hours != 1 else ''}"


def _backhaul_status(rssi: Optional[float]) -> str:
    if rssi is None:
        return "unknown"
    if rssi >= BACKHAUL_GOOD_DBM:
        return "good"
    if rssi >= BACKHAUL_WARN_DBM:
        return "warn"
    return "bad"


def _plural(n: int, singular: str, plural: Optional[str] = None) -> str:
    """``1 finding`` / ``3 findings`` -- count-aware, so the deliverable never
    prints a robotic ``finding(s)`` (docs/REPORT_SPEC.md anti-patterns)."""
    word = singular if n == 1 else (plural if plural is not None else f"{singular}s")
    return f"{n} {word}"


def _is_synthetic_gateway(row: Any) -> bool:
    """True for the probe-target GATEWAY the probe factory synthesises on a
    gateway-less site (not a managed device; kept out of the inventory)."""
    native = row["native_id"] or ""
    if native.startswith(_PROBE_TARGET_PREFIX):
        return True
    meta = _decode_json(row["meta"])
    return meta.get("synthetic") is True or meta.get("role") == "probe_target"


# --------------------------------------------------------------------------- #
# Observation composer -- the measured fact, not a title echo
# --------------------------------------------------------------------------- #
# Evidence keys that are configured thresholds/constants, not measurements. They
# are excluded from the generic observation fallback so a threshold is never read
# as a reading (docs/REPORT_SPEC.md findings template: Observation = the measured
# fact + the threshold it is judged against, composed into a sentence).
_THRESHOLD_EVIDENCE_KEYS = frozenset(
    {
        "bad_rssi_dbm",
        "warn_rssi_dbm",
        "good_rssi_dbm",
        "rssi_floor_dbm",
        "multiplier",
        "baseline_p50",
    }
)


def _dbm(value: Any) -> Optional[str]:
    iv = _as_int(value)
    return f"{iv} dBm" if iv is not None else None


def _obs_mesh_uplink(ev: dict[str, Any]) -> Optional[str]:
    median = _dbm(ev.get("median_uplink_rssi"))
    m = _as_int(ev.get("median_uplink_rssi"))
    if median is None or m is None:
        return None
    bad = _as_int(ev.get("bad_rssi_dbm"))
    warn = _as_int(ev.get("warn_rssi_dbm"))
    if bad is not None and m < bad:
        band = f", below the {bad} dBm floor where the backhaul can no longer hold a fast rate"
    elif warn is not None and m < warn:
        band = f", in the warn band under the {warn} dBm target"
    else:
        band = ""
    return f"The mesh backhaul held a median {median} over the window{band}."


def _obs_sticky_client(ev: dict[str, Any]) -> Optional[str]:
    median = _dbm(ev.get("median_rssi"))
    if median is None:
        return None
    parts = [f"The client held its current access point at a median {median}"]
    floor = _as_int(ev.get("rssi_floor_dbm"))
    frac = ev.get("sustained_fraction_below")
    if isinstance(frac, (int, float)) and floor is not None:
        parts.append(f", {round(frac * 100)}% of samples below the {floor} dBm floor")
    better = _dbm(ev.get("better_ap_median_rssi"))
    if better is not None:
        parts.append(f", while a nearer AP measured {better}")
    return "".join(parts) + "."


def _obs_broadcast_storm(ev: dict[str, Any]) -> Optional[str]:
    ports = _as_int(ev.get("ports_storming"))
    detail = ev.get("detail")
    ratios: list[float] = []
    if isinstance(detail, list):
        for d in detail:
            if isinstance(d, dict) and isinstance(d.get("ratio"), (int, float)):
                ratios.append(float(d["ratio"]))
    count = ports if ports is not None else (len(ratios) if ratios else None)
    if count is None:
        return None
    sentence = f"{_plural(count, 'port')} broadcast above the storm threshold"
    if ratios:
        sentence += f", peaking at {max(ratios):.0f}x the port's normal baseline"
    mult = _as_int(ev.get("multiplier"))
    if mult is not None:
        sentence += f" ({mult}x trigger)"
    return sentence + "."


_OBSERVATION_BUILDERS = {
    "wifi.mesh_uplink": _obs_mesh_uplink,
    "wifi.sticky_client": _obs_sticky_client,
    "wired.broadcast_storm": _obs_broadcast_storm,
}


def _generic_observation(ev: dict[str, Any]) -> Optional[str]:
    """Fallback: the salient *measured* scalars as a sentence (thresholds, flags,
    MAC-like strings and nested values excluded), never a raw dump or title echo."""
    frags: list[str] = []
    for k, v in ev.items():
        if k == "confounders_checked" or k in _THRESHOLD_EVIDENCE_KEYS:
            continue
        if v is None or isinstance(v, (bool, list, dict)):
            continue
        if isinstance(v, str) and (":" in v or len(v) > 24):
            continue  # skip MAC/id-like strings
        frags.append(f"{k.replace('_', ' ')} {v}")
        if len(frags) >= 3:
            break
    if not frags:
        return None
    return "Measured over the window: " + ", ".join(frags) + "."


def _observation(detector_key: str, title: str, evidence: dict[str, Any]) -> str:
    """The finding's Observation: the detector's measured values composed into a
    sentence with the threshold they are judged against. Falls back to the salient
    measured scalar, and only an evidence-less issue keeps the bare title."""
    builder = _OBSERVATION_BUILDERS.get(detector_key or "")
    clause = builder(evidence) if builder else None
    if clause is None:
        clause = _generic_observation(evidence)
    return clause or title


# --------------------------------------------------------------------------- #
# Impact index (failed SLE minutes + affected clients, per attributed entity)
# --------------------------------------------------------------------------- #
def _impact_index(store: Repository, start: int, end: int) -> dict[int, dict[str, Any]]:
    """``{attributed_entity_id: {"minutes": float, "clients": set[int]}}`` over the window.

    One ``sle_minutes`` GROUP BY (attributed / client / classifier), summing the
    failed (non-``ok``) minutes and collecting the distinct client ids per
    attributed infrastructure entity. This is the impact term for every finding:
    real minutes real clients spent degraded, pinned by the SLE engine -- never a
    guess (unattributed minutes are excluded).

    **Client-axis SLEs only** (Gitea #36, #37). The ``infra`` SLE writes the
    *device* into both ``entity_id`` and ``attributed_entity_id``, because what it
    measures is a box being down rather than a client having a bad time. Including
    it here did two wrong things at once: it added device down-minutes to a total
    described as client-minutes, and it put the device itself into ``clients``, so
    a report about an access point said "1 client affected" -- counting the AP as
    one of its own victims. Filtering to the client axis fixes both, and makes
    ``entity_id`` a client by construction rather than by assumption.

    Nothing is lost by excluding it. A down AP has no clients associated, so the
    harm it causes surfaces on this axis anyway, as the coverage and roaming
    minutes those clients then burn on whatever AP they land on next.
    """
    rows = store.query_sle_minutes(
        start, end, group_by=("attributed_entity_id", "entity_id", "classifier", "sle")
    )
    idx: dict[int, dict[str, Any]] = {}
    for r in rows:
        if r["classifier"] == OK:
            continue
        if r["sle"] not in SLE_CLIENT_AXIS_SLES:
            continue
        attr = r["attributed_entity_id"]
        if attr is None:
            continue
        cell = idx.setdefault(int(attr), {"minutes": 0.0, "clients": set()})
        cell["minutes"] += float(r["minutes"] or 0.0)
        ent = r["entity_id"]
        if ent is not None:
            cell["clients"].add(int(ent))
    return idx


def _display_hours(hours: float) -> str:
    """Client-hours rounded to a precision the window's minute-granularity data can
    actually support: whole hours once a tenth of an hour (6 minutes) is noise
    against an aggregate this size, one decimal place below that so a small-but-
    real impact does not round away to zero."""
    return str(round(hours)) if hours >= 10 else str(round(hours, 1))


def _impact_prose(affected: int, fail_client_hours: float) -> str:
    """The shared "N clients affected, about H client-hours degraded" sentence --
    one source for both a finding's own Impact field and the executive summary's
    plain-language recap, so the two can never drift into different roundings or
    different units (Gitea #27: one used to say "client-hours of failed SLE
    minutes", mashing two units in one phrase)."""
    hours_text = _display_hours(fail_client_hours)
    return (
        f"{_plural(affected, 'client')} affected, about {hours_text} "
        "client-hours degraded over the window."
    )


def _finding_impact(
    entity_ids: list[int], impact_index: dict[int, dict[str, Any]]
) -> FindingImpact:
    minutes = 0.0
    clients: set[int] = set()
    # Dedupe: two correlated issues may name the same entity (a root and its
    # coverage symptom both on one AP); its attributed minutes count once, not twice.
    for eid in dict.fromkeys(entity_ids):
        cell = impact_index.get(eid)
        if cell is not None:
            minutes += cell["minutes"]
            clients |= cell["clients"]
    fail_client_hours = round(minutes / 60.0, 1)
    affected = len(clients)
    if minutes > 0:
        summary = _impact_prose(affected, fail_client_hours)
    else:
        summary = "No failed SLE client-minutes are attributed to this finding over the window."
    return FindingImpact(
        fail_minutes=round(minutes, 1),
        fail_client_hours=fail_client_hours,
        affected_clients=affected,
        summary=summary,
    )


# --------------------------------------------------------------------------- #
# Section builders
# --------------------------------------------------------------------------- #
def _build_inventory(device_rows: list[Any], device_states: dict[int, dict[str, Any]]) -> Inventory:
    devices: list[InventoryDevice] = []
    for row in device_rows:
        eid = int(row["entity_id"])
        devices.append(
            InventoryDevice(
                entity_id=eid,
                name=row["name"] if row["name"] else row["native_id"],
                model=row["model"],
                role=row["entity_type"],
                uplink=device_states.get(eid, {}).get("uplink_type"),
            )
        )
    counts = {
        "ap": sum(1 for d in devices if d.role == EntityType.AP.value),
        "switch": sum(1 for d in devices if d.role == EntityType.SWITCH.value),
        "gateway": sum(1 for d in devices if d.role == EntityType.GATEWAY.value),
    }
    return Inventory(counts=counts, devices=devices)


def _topology_node(
    row: Any,
    device_states: dict[int, dict[str, Any]],
    parent_counts: dict[int, int],
    mesh_rssi: Optional[float] = None,
) -> TopologyNode:
    eid = int(row["entity_id"])
    uplink = device_states.get(eid, {}).get("uplink_type")
    is_wireless = uplink == "wireless"
    return TopologyNode(
        entity_id=eid,
        name=row["name"] if row["name"] else row["native_id"],
        model=row["model"],
        role=row["entity_type"],
        uplink=uplink,
        parent_id=row["parent_id"],
        mesh_uplink_rssi=mesh_rssi if is_wireless else None,
        backhaul_status=_backhaul_status(mesh_rssi) if is_wireless else None,
        client_count=int(parent_counts.get(eid, 0)),
    )


def _build_topology(
    ap_rows: list[Any],
    sw_rows: list[Any],
    gw_rows: list[Any],
    device_states: dict[int, dict[str, Any]],
    ap_samples: dict[int, list[dict[str, Any]]],
    parent_counts: dict[int, int],
    synthetic_gw: Optional[Any] = None,
) -> Topology:
    if gw_rows:
        gateway: Optional[TopologyNode] = _topology_node(gw_rows[0], device_states, parent_counts)
    elif synthetic_gw is not None:
        # No managed UniFi gateway: the WAN edge is the site's own router, which the
        # probes target. Show it as an honest gateway node, never the raw synthetic
        # "probe target" entity name.
        gateway = _topology_node(synthetic_gw, device_states, parent_counts)
        gateway.name = "Internet gateway"
        gateway.model = "external router (probe target)"
    else:
        gateway = None
    switches = [_topology_node(r, device_states, parent_counts) for r in sw_rows]
    aps = [
        _topology_node(
            r,
            device_states,
            parent_counts,
            mesh_rssi=_latest_metric(ap_samples.get(int(r["entity_id"]), []), "uplink_rssi"),
        )
        for r in ap_rows
    ]
    return Topology(
        gateway=gateway,
        switches=switches,
        aps=aps,
        backhaul_thresholds={"good_dbm": BACKHAUL_GOOD_DBM, "warn_dbm": BACKHAUL_WARN_DBM},
    )


def _build_health(
    store: Repository,
    settings: Any,
    start: int,
    end: int,
    weights: dict[str, float],
    low_confidence: bool = False,
) -> HealthSection:
    report = sle_scores(store, start, end, top_n=5, settings=settings)
    off_ids = [
        off["attributed_entity_id"]
        for s in report.sles.values()
        for off in s.top_offenders
        if off.get("attributed_entity_id") is not None
    ]
    names = store.entities_by_ids(off_ids)

    sles: list[SleScoreView] = []
    for sle in ALL_SLES:
        s = report.sles.get(sle)
        if s is None:
            continue
        top = [
            {
                "attributed_entity_id": off.get("attributed_entity_id"),
                "fail_minutes": round(float(off["fail_minutes"]), 1),
                "entity": (
                    _entity_ref(names.get(int(off["attributed_entity_id"])))
                    if off.get("attributed_entity_id") is not None
                    else None
                ),
            }
            for off in s.top_offenders
        ]
        sles.append(
            SleScoreView(
                sle=sle,
                score=(int(round(s.score * 100)) if s.score is not None else None),
                total_minutes=round(s.total_minutes, 1),
                fail_minutes=round(s.fail_minutes, 1),
                top_offenders=top,
                low_confidence=low_confidence,
            )
        )

    trend_rows = store.query_sle_minutes(start, end, group_by=("sle", "classifier", "bucket_ts"))
    trend = charts.health_trend(trend_rows, start, end, HEALTH_TREND_BUCKETS, weights)
    headline = int(round(report.headline * 100)) if report.headline is not None else None
    return HealthSection(headline_score=headline, sles=sles, trend=trend)


def _build_rf(
    radio_rows: list[Any],
    radio_samples: dict[int, list[dict[str, Any]]],
    radio_states: dict[int, dict[str, Any]],
    ap_name_by_id: dict[int, str],
    neighbor_density: dict[str, Any],
) -> RfSection:
    utilization: list[RadioUtilization] = []
    for row in radio_rows:
        rid = int(row["entity_id"])
        meta = _decode_json(row["meta"])
        channel = radio_states.get(rid, {}).get("channel")
        cu_total = _latest_metric(radio_samples.get(rid, []), "cu_total")
        cu_self_rx = _latest_metric(radio_samples.get(rid, []), "cu_self_rx")
        cu_self_tx = _latest_metric(radio_samples.get(rid, []), "cu_self_tx")
        cu_self = None
        if cu_self_rx is not None or cu_self_tx is not None:
            cu_self = (cu_self_rx or 0.0) + (cu_self_tx or 0.0)
        cu_non_self = None
        if cu_total is not None and cu_self is not None:
            cu_non_self = round(max(0.0, cu_total - cu_self), 2)
        utilization.append(
            RadioUtilization(
                entity_id=rid,
                ap_name=(
                    ap_name_by_id.get(int(row["parent_id"]), row["native_id"])
                    if row["parent_id"] is not None
                    else row["native_id"]
                ),
                band=_norm_band(meta.get("band"), _as_int(channel)),
                channel=channel,
                cu_total=cu_total,
                cu_self=round(cu_self, 2) if cu_self is not None else None,
                cu_non_self=cu_non_self,
            )
        )

    total = neighbor_density["total"]
    channels = len(neighbor_density["by_channel"])
    if total > 0:
        summary = (
            f"{_plural(total, 'neighbouring/rogue BSS', 'neighbouring/rogue BSSes')} seen across "
            f"{_plural(channels, 'channel')} in the scan window. A dense RF neighbourhood is "
            "environmental context, not a per-AP alarm."
        )
    else:
        summary = "No neighbouring APs were seen in the scan window."

    return RfSection(
        utilization=utilization,
        utilization_reference_pct=UTILIZATION_REFERENCE_PCT,
        neighbor_density=neighbor_density,
        neighbor_summary=summary,
    )


def _build_clients(
    store: Repository,
    client_rows: list[Any],
    client_samples: dict[int, list[dict[str, Any]]],
    ap_rows: list[Any],
    parent_counts: dict[int, int],
    weak_threshold: float,
    settings: Any,
    start: int,
    end: int,
) -> ClientsSection:
    rssi_values: list[float] = []
    without = 0
    for c in client_rows:
        v = _latest_metric(client_samples.get(int(c["entity_id"]), []), "rssi")
        # A client RSSI of 0 (or any non-negative value) is the controller's
        # "no reading" sentinel, not a real signal — counting it as the strongest
        # client would overstate the strong end of the coverage histogram.
        if v is None or v >= 0:
            without += 1
        else:
            rssi_values.append(v)
    histogram = charts.rssi_histogram(rssi_values, weak_threshold)

    aps = [
        {"entity_id": int(r["entity_id"]), "name": r["name"] if r["name"] else r["native_id"]}
        for r in ap_rows
    ]
    cpa = charts.clients_per_ap(aps, parent_counts)

    # Section 8 ranks the worst-experiencing CLIENTS (not infrastructure): a
    # client's burden is the disconnect/roam churn and open issues it carries.
    # Failed SLE minutes attribute to infrastructure, so a client's composite is
    # driven by its own event/issue channels -- distinct from the fail-minutes
    # column, so the two are no longer degenerate duplicates.
    scores = rank_offenders(
        store, CLIENT_ENTITY_TYPES, start, end, top_n=WORST_DEVICES_TOP_N, settings=settings
    )
    names = store.entities_by_ids([s.entity_id for s in scores])
    worst = [
        {
            "entity": _entity_ref(names.get(s.entity_id)),
            "score": round(s.score, 1),
            "fail_minutes": round(s.fail_minutes, 1),
            "issue_counts": dict(s.issue_counts),
            "event_count": s.event_count,
        }
        for s in scores
    ]
    return ClientsSection(
        rssi_histogram=histogram,
        clients_per_ap=cpa,
        worst_devices=worst,
        clients_without_rssi=without,
    )


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #
def _affected_asset(row: Any) -> Optional[AffectedAsset]:
    if row is None:
        return None
    return AffectedAsset(
        entity_id=int(row["entity_id"]),
        name=row["name"] if row["name"] else row["native_id"],
        type=row["entity_type"],
        role=row["entity_type"],
    )


def _incident_finding(
    store: Repository,
    issues: list[dict[str, Any]],
    incident_id: Optional[int],
    briefs: dict[int, Any],
    ent_map: dict[int, Any],
    impact_index: dict[int, dict[str, Any]],
) -> tuple[tuple[Any, ...], Finding, str]:
    """Build one finding from a group of correlated issues (or a single issue)."""
    members_meta: dict[int, dict[str, Any]] = {}
    if incident_id is not None:
        for m in store.list_incident_members(incident_id):
            members_meta[int(m["issue_id"])] = {
                "role": m["role"],
                "rule": m["rule"],
                "rationale": m["rationale"],
            }

    root = None
    for i in issues:
        meta = members_meta.get(int(i["id"]))
        if meta and meta["role"] == "root":
            root = i
            break
    if root is None:
        root = min(
            issues,
            key=lambda i: (
                _SEV_RANK.get(i["severity"], 9),
                int(i["first_seen_ts"] or 0),
                int(i["id"]),
            ),
        )

    symptom_issues = [i for i in issues if int(i["id"]) != int(root["id"])]
    aff_ids = [int(i["entity_id"]) for i in issues if i["entity_id"] is not None]
    impact = _finding_impact(aff_ids, impact_index)

    root_brief = briefs.get(int(root["id"]))
    if incident_id is not None and root_brief is not None:
        netadmin_sev = root_brief["incident_severity"]
    else:
        netadmin_sev = root["severity"]
    sev_label = to_severity_label(netadmin_sev)

    guidance = finding_guidance(root["detector_key"], correlated_symptoms=len(symptom_issues))
    evidence = _decode_json(root["evidence"])
    confounders = evidence.get("confounders_checked")
    confounders = confounders if isinstance(confounders, list) else []

    affected = [
        a
        for a in (_affected_asset(ent_map.get(eid)) for eid in dict.fromkeys(aff_ids))
        if a is not None
    ]
    symptoms = [
        SymptomRef(
            issue_id=int(si["id"]),
            detector_key=si["detector_key"],
            title=si["title"],
            entity=(
                _entity_ref(ent_map.get(int(si["entity_id"])))
                if si["entity_id"] is not None
                else None
            ),
            rule=members_meta.get(int(si["id"]), {}).get("rule"),
            rationale=members_meta.get(int(si["id"]), {}).get("rationale"),
        )
        for si in symptom_issues
    ]

    observation = _observation(root["detector_key"], root["title"], evidence)
    if symptom_issues:
        observation = (
            f"{observation} {_plural(len(symptom_issues), 'correlated symptom')} "
            "share this root cause."
        )

    finding = Finding(
        id="",
        title=root["title"],
        severity=sev_label,
        netadmin_severity=netadmin_sev,
        detector_key=root["detector_key"],
        affected_assets=affected,
        observation=observation,
        evidence=evidence,
        impact=impact,
        root_cause=guidance.root_cause,
        recommendation=guidance.recommendation,
        confounders_checked=confounders,
        signature=guidance.signature,
        incident_id=incident_id,
        symptoms=symptoms,
        source_issue_ids=[int(i["id"]) for i in issues],
    )
    sort_key = (
        severity_rank(sev_label),
        -impact.fail_minutes,
        int(root["first_seen_ts"] or 0),
        int(root["id"]),
    )
    prefix = _FAMILY_PREFIX.get(str(root["detector_key"]).split(".")[0], "GEN")
    return sort_key, finding, prefix


def _environmental_finding(
    env_issues: list[dict[str, Any]],
    ent_map: dict[int, Any],
    impact_index: dict[int, dict[str, Any]],
    neighbor_density: dict[str, Any],
) -> tuple[tuple[Any, ...], Finding, str]:
    """Collapse neighbour-density/channel-plan issues + the BSS scan into ONE finding."""
    density = [i for i in env_issues if i["detector_key"] == "wifi.neighbor_density"]
    chan = [i for i in env_issues if i["detector_key"] == "wifi.channel_plan"]
    aff_ids = [int(i["entity_id"]) for i in env_issues if i["entity_id"] is not None]
    impact = _finding_impact(aff_ids, impact_index)

    # Provenance only (recorded on the finding as netadmin_severity): the
    # environmental finding is always Info regardless of the worst underlying
    # P-level, since it summarises RF readings, not one actionable fault.
    most_severe = min((i["severity"] for i in env_issues), key=lambda s: _SEV_RANK.get(s, 9))
    sev_label = to_severity_label(most_severe, environmental=True)

    rec_parts = []
    if chan:
        rec_parts.append(finding_guidance("wifi.channel_plan").recommendation)
    if density:
        rec_parts.append(finding_guidance("wifi.neighbor_density").recommendation)
    recommendation = " ".join(p for p in rec_parts if p)

    affected = [
        a
        for a in (_affected_asset(ent_map.get(eid)) for eid in dict.fromkeys(aff_ids))
        if a is not None
    ]
    channels = len(neighbor_density["by_channel"])
    observation = (
        f"{_plural(len(density), 'crowded band')} and "
        f"{_plural(len(chan), 'channel-plan contention point')}; "
        f"{_plural(neighbor_density['total'], 'neighbour BSS', 'neighbour BSSes')} seen "
        f"across {_plural(channels, 'channel')} over the window."
    )
    root_cause = (
        "The site shares its RF neighbourhood with other networks and reuses "
        "channels across cells. Neighbour APs are counted from periodic scans, so "
        "this is environmental context, not a per-device fault."
    )
    earliest = min(int(i["first_seen_ts"] or 0) for i in env_issues)
    finding = Finding(
        id="",
        title="RF neighbourhood and channel-plan contention",
        severity=sev_label,
        netadmin_severity=most_severe,
        detector_key="wifi.rf_environment",
        affected_assets=affected,
        observation=observation,
        evidence={
            "neighbor_density_issue_count": len(density),
            "channel_plan_issue_count": len(chan),
            "neighbor_bss_total": neighbor_density["total"],
            "neighbor_by_channel": neighbor_density["by_channel"],
        },
        impact=impact,
        root_cause=root_cause,
        recommendation=recommendation,
        confounders_checked=[],
        signature="",
        incident_id=None,
        symptoms=[],
        source_issue_ids=[int(i["id"]) for i in env_issues],
    )
    sort_key = (severity_rank(sev_label), -impact.fail_minutes, earliest, 0)
    return sort_key, finding, "ENV"


def _build_findings(
    store: Repository,
    start: int,
    end: int,
    impact_index: dict[int, dict[str, Any]],
    neighbor_density: dict[str, Any],
) -> list[Finding]:
    confirmed = [
        dict(i) for i in store.list_issues(open_only=True) if i["state"] in CONFIRMED_STATES
    ]
    if not confirmed:
        return []

    env_issues = [i for i in confirmed if i["detector_key"] in ENVIRONMENTAL_KEYS]
    core_issues = [i for i in confirmed if i["detector_key"] not in ENVIRONMENTAL_KEYS]

    briefs = (
        store.incident_brief_for_issues([int(i["id"]) for i in core_issues]) if core_issues else {}
    )

    entity_ids = {int(i["entity_id"]) for i in confirmed if i["entity_id"] is not None}
    ent_map = store.entities_by_ids(entity_ids)

    # Group core issues by incident; a standalone issue is its own group.
    groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
    group_incident: dict[tuple[str, int], Optional[int]] = {}
    for i in core_issues:
        brief = briefs.get(int(i["id"]))
        if brief is not None:
            key = ("incident", int(brief["incident_id"]))
            group_incident[key] = int(brief["incident_id"])
        else:
            key = ("solo", int(i["id"]))
            group_incident[key] = None
        groups.setdefault(key, []).append(i)

    drafts: list[tuple[tuple[Any, ...], Finding, str]] = []
    for key, issues in groups.items():
        drafts.append(
            _incident_finding(store, issues, group_incident[key], briefs, ent_map, impact_index)
        )
    if env_issues:
        drafts.append(_environmental_finding(env_issues, ent_map, impact_index, neighbor_density))

    drafts.sort(key=lambda d: d[0])
    prefix_counter: dict[str, int] = {}
    findings: list[Finding] = []
    for _, finding, prefix in drafts:
        n = prefix_counter.get(prefix, 0) + 1
        prefix_counter[prefix] = n
        finding.id = f"{prefix}-{n:02d}"
        findings.append(finding)
    return findings


# --------------------------------------------------------------------------- #
# Roadmap + executive summary
# --------------------------------------------------------------------------- #
def _build_roadmap(findings: list[Finding]) -> RoadmapSection:
    phases: dict[str, list[Recommendation]] = {"now": [], "soon": [], "strategic": []}
    for f in findings:
        phase = _PHASE_BY_SEVERITY.get(f.severity, "strategic")
        phases[phase].append(
            Recommendation(
                finding_id=f.id,
                title=f.title,
                severity=f.severity,
                phase=phase,
                text=f.recommendation,
            )
        )
    for bucket in phases.values():
        bucket.sort(key=lambda r: (severity_rank(r.severity), r.finding_id))
    return RoadmapSection(now=phases["now"], soon=phases["soon"], strategic=phases["strategic"])


def _posture(health_score: Optional[int], counts: dict[str, int], total: int) -> tuple[str, str]:
    """(short posture token, one-sentence verdict) derived from score + severities."""
    if health_score is None and total == 0:
        return (
            "insufficient data",
            "Not enough SLE data over this window to score network health.",
        )
    if counts.get(CRITICAL, 0) > 0:
        n = counts[CRITICAL]
        verb = "is" if n == 1 else "are"
        return (
            "action needed",
            f"Action needed: {_plural(n, 'critical finding')} {verb} degrading "
            "user experience now.",
        )
    if counts.get(HIGH, 0) > 0:
        n = counts[HIGH]
        return (
            "attention advised",
            f"Attention advised: {_plural(n, 'high-severity finding')} to address.",
        )
    if total == 0:
        score_text = f", health score {health_score}" if health_score is not None else ""
        return ("healthy", f"Healthy: no confirmed issues over the window{score_text}.")
    score_text = f", health score {health_score}" if health_score is not None else ""
    return ("stable", f"Stable: {_plural(total, 'lower-severity item')} open{score_text}.")


def _top_plain(f: Finding) -> str:
    if f.impact.fail_minutes > 0 or f.impact.affected_clients > 0:
        return _impact_prose(f.impact.affected_clients, f.impact.fail_client_hours)
    return "Confirmed issue with no measured client-minute impact over the window."


def _build_exec(
    findings: list[Finding],
    health_score: Optional[int],
    roadmap: RoadmapSection,
    coverage_pct: Optional[int] = None,
    low_confidence: bool = False,
) -> ExecutiveSummary:
    counts = {level: 0 for level in SEVERITY_ORDER}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    total = len(findings)
    posture, verdict = _posture(health_score, counts, total)

    if low_confidence:
        pct = f"about {coverage_pct}% of" if coverage_pct is not None else "under half of"
        confidence_note = (
            f"Under-observed window: live polling reached {pct} the period, so scores are "
            "indicative, not settled. See Scope for per-job coverage."
        )
    else:
        confidence_note = None

    scorecard = Scorecard(
        health_score=health_score,
        posture=posture,
        findings_by_severity=counts,
        total_findings=total,
        coverage_pct=coverage_pct,
        low_confidence=low_confidence,
        confidence_note=confidence_note,
    )
    top = [
        TopFinding(id=f.id, title=f.title, severity=f.severity, plain=_top_plain(f))
        for f in findings[:TOP_FINDINGS_N]
    ]

    n_now, n_soon, n_strat = len(roadmap.now), len(roadmap.soon), len(roadmap.strategic)
    if total == 0:
        rec_summary = "No action is required over this window."
    else:
        rec_summary = (
            f"{_plural(n_now, 'item')} to address now, {n_soon} soon, {n_strat} strategic."
        )

    return ExecutiveSummary(
        verdict=verdict,
        scorecard=scorecard,
        top_findings=top,
        recommendation_summary=rec_summary,
    )


# --------------------------------------------------------------------------- #
# Scope, appendix, cover
# --------------------------------------------------------------------------- #
def _build_scope(
    store: Repository, poll: PollIntervals, minutes_s: int, window: DataWindow, start: int, end: int
) -> ScopeMethodology:
    data_sources = [
        "UniFi controller telemetry (stat/device, stat/sta, stat/health)",
        "Active local probes (DNS timing, gateway ICMP RTT)",
        "Neighbour scan (stat/rogueap)",
    ]
    sampling = [
        SamplingSource(
            "stat/device (ports, radios, PoE, uplink)", poll.device_s, "per-device poll"
        ),
        SamplingSource("stat/sta (per-client RSSI, retries, roam)", poll.sta_s, "per-client poll"),
        SamplingSource("stat/health (WAN/www latency, drops)", poll.health_s, "gateway poll"),
        SamplingSource("active probes (DNS, gateway RTT)", poll.probe_s, "local probe"),
        SamplingSource("stat/rogueap (neighbour BSS)", poll.rogueap_s, "daily scan"),
        SamplingSource("SLE minute accounting", minutes_s, "5-minute buckets"),
    ]

    coverage: list[CoverageEntry] = []
    for job, interval in (
        (_JOB_DEVICE, poll.device_s),
        (_JOB_STA, poll.sta_s),
        (_JOB_HEALTH, poll.health_s),
        (_JOB_GW_RTT, poll.probe_s),
    ):
        frac = round(store.expected_coverage(job, start, end, max(1, int(interval))), 4)
        note = (
            "under 50% live coverage; treat this window as partial"
            if frac < 0.5
            else "coverage adequate"
        )
        coverage.append(CoverageEntry(job=job, interval_s=int(interval), fraction=frac, note=note))

    limitations = [
        "No spectrum analyzer: non-Wi-Fi interference is inferred from airtime, not measured.",
        "No client-side RSSI: coverage is judged from the AP's view of the client.",
        "Neighbour APs are counted from a daily scan, so density is a sample, not continuous.",
        "WAN is measured by active probe and controller health, not a dedicated circuit monitor.",
        "A window under 50% poll coverage is reported as partial, never smoothed to look complete.",
    ]
    return ScopeMethodology(
        data_sources=data_sources,
        sampling=sampling,
        window=window,
        coverage=coverage,
        limitations=limitations,
    )


def _window_confidence(coverage: list[CoverageEntry]) -> tuple[Optional[int], bool]:
    """Representative live-poll coverage (%) and whether the window is under-observed.

    The three core fast jobs (device/sta/health) drive whether the window was
    observed enough to trust a headline score; the gateway-RTT probe is excluded
    because it is legitimately absent on a gateway-less site and would understate
    real coverage. ``(None, False)`` when no core job was measured.
    """
    core = [c.fraction for c in coverage if c.job in (_JOB_DEVICE, _JOB_STA, _JOB_HEALTH)]
    if not core:
        return None, False
    representative = min(core)
    return round(representative * 100), representative < 0.5


def _build_appendix(sle_cfg: SleConfig, weights: dict[str, float]) -> Appendix:
    thresholds = {
        "coverage_weak_dbm": sle_cfg.coverage_weak_dbm,
        "sticky_rssi_dbm": sle_cfg.sticky_rssi_dbm,
        "capacity_degraded_pct": sle_cfg.capacity_degraded_pct,
        "wan_latency_abs_ms": sle_cfg.wan_latency_abs_ms,
        "utilization_reference_pct": UTILIZATION_REFERENCE_PCT,
        "backhaul_good_dbm": BACKHAUL_GOOD_DBM,
        "backhaul_warn_dbm": BACKHAUL_WARN_DBM,
        "sle_weights": weights,
        "health_trend_buckets": HEALTH_TREND_BUCKETS,
    }
    methodology = {
        "scoring": (
            "The health score and its breakdown are one GROUP BY over sle_minutes: "
            "a per-SLE score is ok-minutes over total-minutes, blended by weight."
        ),
        "attribution": (
            "Failed client-minutes are pinned on an infrastructure entity only when "
            "the SLE engine attributed them by rule; unattributed minutes are excluded."
        ),
        "findings": (
            "Correlated issues are grouped into one finding by the incident engine; "
            "neighbour and channel-plan noise is aggregated into one environmental finding."
        ),
    }
    glossary = [
        {
            "term": "SLE",
            "definition": "Service-Level Expectation: a pass/fail judgement of a client-minute.",
        },
        {
            "term": "RSSI",
            "definition": "Received signal strength in dBm; closer to zero is stronger.",
        },
        {"term": "cu_total", "definition": "Channel airtime utilisation percentage on a radio."},
        {
            "term": "Mesh backhaul",
            "definition": "The wireless uplink an AP uses instead of a wired drop.",
        },
        {
            "term": "Offender",
            "definition": "An entity ranked by the failed-minutes/issues/events it accounts for.",
        },
        {
            "term": "fail-min",
            "definition": (
                "One SLE fail-minute: a real client's minute that missed a service level's "
                "pass/fail target. The raw unit behind SLE scores and the offender burden score."
            ),
        },
        {
            "term": "Severity ladder",
            "definition": (
                "Critical/High/Low, a direct rename of the app's P1/P2/P3; Info is "
                "reserved for aggregated environmental context."
            ),
        },
    ]
    return Appendix(
        severity_rubric=severity_rubric(),
        thresholds=thresholds,
        methodology=methodology,
        glossary=glossary,
    )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def build_report(
    store: Repository,
    settings: Any = None,
    *,
    now: Optional[int] = None,
    window_s: Optional[int] = None,
) -> ReportModel:
    """Assemble the full report model over ``[now - window_s, now)`` from the store.

    Read-only. ``window_s`` defaults to 7 days and is clamped to
    ``[1 h, ~13 months]``. Every section is built from repository queries; absent
    data yields honest empties, never fabricated numbers.
    """
    now = _now() if now is None else int(now)
    window_s = DEFAULT_WINDOW_S if window_s is None else int(window_s)
    window_s = max(MIN_WINDOW_S, min(window_s, MAX_WINDOW_S))
    start, end = now - window_s, now
    window = DataWindow(
        start_ts=start, end_ts=end, duration_s=window_s, label=_window_label(window_s)
    )

    sle_cfg = SleConfig.from_settings(settings)
    weights = load_weights(settings)
    poll = getattr(settings, "poll", None) or PollIntervals()
    minutes_s = int(getattr(getattr(settings, "sle", None), "minutes_s", 300))
    # A human network name for the cover + running headers. The internal "default"
    # site-id sentinel (or an empty value) is a placeholder, not a name, so it is
    # shown as a neutral label rather than surfaced raw as an unconfigured token.
    configured_site = str(
        getattr(settings, "site_name", None) or getattr(settings, "site_id", None) or ""
    ).strip()
    site = (
        configured_site
        if configured_site and configured_site.lower() != "default"
        else ("UniFi network")
    )

    # --- inventory fetch (shared across cover / inventory / topology / rf / clients)
    ap_rows = store.list_entities(EntityType.AP)
    sw_rows = store.list_entities(EntityType.SWITCH)
    gw_rows_all = store.list_entities(EntityType.GATEWAY)
    # Separate managed gateways from the synthetic probe-target edge: only managed
    # devices belong in the inventory counts/table; the synthetic edge is relabelled
    # in the topology (never counted as a UniFi device).
    gw_rows = [r for r in gw_rows_all if not _is_synthetic_gateway(r)]
    synthetic_gw = next((r for r in gw_rows_all if _is_synthetic_gateway(r)), None)
    client_rows = store.list_entities(EntityType.CLIENT)
    radio_rows = store.list_entities(EntityType.RADIO)

    device_rows = [*ap_rows, *sw_rows, *gw_rows]
    device_ids = [int(r["entity_id"]) for r in device_rows]
    device_states = store.current_states_bulk(device_ids)
    ap_ids = [int(r["entity_id"]) for r in ap_rows]
    ap_samples = store.latest_samples_bulk(ap_ids)
    radio_ids = [int(r["entity_id"]) for r in radio_rows]
    radio_samples = store.latest_samples_bulk(radio_ids)
    radio_states = store.current_states_bulk(radio_ids)
    client_ids = [int(r["entity_id"]) for r in client_rows]
    client_samples = store.latest_samples_bulk(client_ids)

    ap_name_by_id = {int(r["entity_id"]): (r["name"] or r["native_id"]) for r in ap_rows}

    # Clients seen in the window, counted per parent device (AP for wireless,
    # switch for wired) -- the load bars and topology client counts.
    parent_counts: dict[int, int] = {}
    for c in client_rows:
        if int(c["last_seen_ts"] or 0) < start:
            continue
        pid = c["parent_id"]
        if pid is not None:
            parent_counts[int(pid)] = parent_counts.get(int(pid), 0) + 1

    counts = {
        "aps": len(ap_rows),
        "switches": len(sw_rows),
        "gateways": len(gw_rows),
        "clients": len(client_rows),
    }

    # --- neighbour density (RF + the environmental finding both read it) ---
    neighbor_rows: list[dict[str, Any]] = []
    for r in store.list_entities(ROGUE_BSS_TYPE):
        if int(r["last_seen_ts"] or 0) < start:
            continue
        meta = _decode_json(r["meta"])
        channel = _as_int(meta.get("channel"))
        neighbor_rows.append({"band": _norm_band(meta.get("band"), channel), "channel": channel})
    neighbor_density = charts.neighbor_density(neighbor_rows)

    impact_index = _impact_index(store, start, end)

    cover = CoverMeta(
        site=site,
        generated_ts=now,
        tool="UnifiOptimizer",
        version=__version__,
        window=window,
        counts=counts,
        confidentiality=(
            "Confidential: network assessment for the site operator. Contains device "
            "and client identifiers."
        ),
    )
    # Scope (with measured per-job poll coverage) is built first so the window's
    # confidence travels with the headline numbers: an under-observed window flags
    # the scorecard and any 100/100 SLE, rather than reading as settled fact.
    scope = _build_scope(store, poll, minutes_s, window, start, end)
    coverage_pct, low_confidence = _window_confidence(scope.coverage)

    inventory = _build_inventory(device_rows, device_states)
    topology = _build_topology(
        ap_rows, sw_rows, gw_rows, device_states, ap_samples, parent_counts, synthetic_gw
    )
    health = _build_health(store, settings, start, end, weights, low_confidence)
    rf = _build_rf(radio_rows, radio_samples, radio_states, ap_name_by_id, neighbor_density)
    clients = _build_clients(
        store,
        client_rows,
        client_samples,
        ap_rows,
        parent_counts,
        sle_cfg.coverage_weak_dbm,
        settings,
        start,
        end,
    )
    findings = _build_findings(store, start, end, impact_index, neighbor_density)
    roadmap = _build_roadmap(findings)
    executive = _build_exec(findings, health.headline_score, roadmap, coverage_pct, low_confidence)
    appendix = _build_appendix(sle_cfg, weights)

    return ReportModel(
        cover=cover,
        executive_summary=executive,
        scope=scope,
        inventory=inventory,
        topology=topology,
        health=health,
        rf=rf,
        clients=clients,
        findings=findings,
        roadmap=roadmap,
        appendix=appendix,
        generated_ts=now,
    )
