"""The correlation engine -- the "seasoned expert" layer (section 17).

Pure logic over the current open-issue set + entity topology; the only I/O is the
:class:`~netadmin.correlate.models.CorrelationStore`. The engine groups open
issues into **incidents**: one *root* (the thing to fix) plus the *symptoms* that
clear when it clears, each attributed on a concrete topological + causal-rule
basis with a recorded rationale. Anything it cannot confidently attribute is left
as a standalone **incident-of-one**. Rules only, never statistical guessing;
conservatism is the whole design (a wrong grouping is worse than none).

One pass, :meth:`CorrelationEngine.run`, is idempotent: it recomputes groupings
from open issues every time and preserves each incident's identity by its root
fingerprint, so re-running with the same inputs yields the same incident ids.

Pipeline (section 17, steps 1-6):

1. Load correlatable issues (``active``/``resolving`` -- ``pending`` excluded) +
   the topology snapshot.
2. For every ordered ``(root, symptom)`` pair, ask the rule table whether the
   root could explain the symptom (rule match + concrete topological relation +
   the rank guard).
3. Apply the **temporal guard**: drop a candidate link whose symptom began
   materially before its root -- a symptom cannot predate its cause.
4. **Root selection**: each symptom takes its highest-priority candidate root;
   chains roll up to the ultimate (most-upstream) root.
5. Emit one incident per ultimate root with a plain-language title/summary,
   per-member rule + rationale, and severity = max member severity. Every issue
   attributed to no root is its own incident-of-one.
6. Reconcile: any previously-open incident no longer produced (its root cleared
   or was absorbed) is resolved.

``now`` is injected into :meth:`run`; the engine never reads the wall clock.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

from netadmin.correlate.models import (
    CorrelationConfig,
    CorrelationStore,
    Incident,
    IncidentMember,
    IncidentRole,
    IncidentState,
    max_severity,
)
from netadmin.correlate.rules import match_link, root_rank
from netadmin.correlate.topology import TopologyIndex, TopoNode
from netadmin.domain.entities import Timestamp
from netadmin.issues.models import Issue
from netadmin.logging import get_logger

_log = get_logger("correlate.engine")

__all__ = ["CorrelationEngine", "incident_fingerprint"]


def incident_fingerprint(root_issue_fingerprint: str) -> str:
    """``sha1(root issue fingerprint)`` -- the incident's stable identity."""
    return hashlib.sha1(root_issue_fingerprint.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Presentation vocabulary (plain-language title / summary generation)
# --------------------------------------------------------------------------- #
_ROOT_PHRASE: dict[str, str] = {
    "wifi.mesh_uplink": "Weak mesh backhaul on {name}",
    "wifi.tx_power_loud": "Excessive TX power on {name}",
    "wifi.airtime_saturation": "Airtime saturation on {name}",
    "wan.isp_degraded": "Degraded ISP uplink on {name}",
    "wired.port_flapping": "Flapping uplink port {name}",
    "wired.bad_cable": "Bad cable on {name}",
    "wired.stp_loop": "Spanning-tree loop on {name}",
    "wired.broadcast_storm": "Broadcast storm on {name}",
    "net.firmware_regression": "Firmware regression on {name}",
    "infra.device_down": "{name} is down",
}

# Root detectors whose blast radius is a single AP cell get the "in that cell"
# locale; WAN / wired / infra roots do not (their reach is the segment or site).
_CELL_LOCAL_ROOTS: frozenset[str] = frozenset(
    {"wifi.mesh_uplink", "wifi.tx_power_loud", "wifi.airtime_saturation"}
)

# symptom detector -> (singular, plural) countable noun.
_SYMPTOM_NOUN: dict[str, tuple[str, str]] = {
    "net.coverage_hole": ("coverage hole", "coverage holes"),
    "client.flaky": ("client dropout", "client dropouts"),
    "wifi.airtime_saturation": ("saturated radio", "saturated radios"),
    "wifi.sticky_client": ("sticky client", "sticky clients"),
    "wan.dns_slow": ("slow-DNS symptom", "slow-DNS symptoms"),
    "wan.bufferbloat": ("bufferbloat symptom", "bufferbloat symptoms"),
}


# Detector-attribution guard (section 17: "only link on a concrete causal basis";
# a wrong attribution is worse than none). Some symptom detectors run their *own*
# root-attribution and can affirmatively rule an infrastructure cause out. The
# ``client.flaky`` matrix labels a client ``device`` when it dropped across *many*
# APs -- its own radio is the common factor, so the detector leaves
# ``attributed_ap`` empty and records the confounder
# ``many_aps_rules_out_single_ap_fault``. Pinning such a client on any one AP's
# backhaul / downtime / firmware (whichever AP it happened to be associated with
# at snapshot time) is exactly the mis-attribution this layer must not commit, so
# a symptom whose evidence self-attributes away from infrastructure never attaches
# to a root; it stands alone as an incident-of-one.
_SELF_ATTRIBUTED_AWAY: dict[str, frozenset[str]] = {
    "client.flaky": frozenset({"device"}),
}


# A per-client symptom detector can pin the fault on a *specific* AP (by name or
# MAC) that is not necessarily the client's current parent: ``client.flaky``
# records the AP its drops actually happened on as ``attributed_ap``;
# ``wifi.sticky_client`` records the far AP it is glued to as ``current_ap``.
# Clients roam, so the topology snapshot's ``parent_id`` may already point at a
# *different* AP by the time correlation runs. Attributing the symptom to that
# current-parent AP's problem — when the detector said the fault was on another
# AP — is exactly the mis-attribution §17 forbids. When such a hint is present,
# the candidate root must actually concern that recorded AP (be it, sit above it,
# or feed it) or the link is refused.
_ATTRIBUTED_AP_FIELD: dict[str, str] = {
    "client.flaky": "attributed_ap",
    "wifi.sticky_client": "current_ap",
}


def _symptom_noun(detector_key: str, count: int) -> str:
    singular, plural = _SYMPTOM_NOUN.get(
        detector_key,
        (detector_key.split(".", 1)[-1].replace("_", " "),) * 2,
    )
    return f"{count} {singular if count == 1 else plural}"


def _join_english(parts: list[str]) -> str:
    """``["a", "b", "c"] -> "a, b and c"``."""
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


# --------------------------------------------------------------------------- #
# Internal computation records
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Candidate:
    """A viable root for a symptom, with its audit line and priority rank."""

    root: Issue
    rule_id: str
    rationale: str
    rank: int


@dataclass(frozen=True)
class _Member:
    issue: Issue
    role: str
    rule: str
    rationale: str


@dataclass(frozen=True)
class _ComputedIncident:
    root: Issue
    members: tuple[_Member, ...]


class CorrelationEngine:
    """Owns the correlation pass. Construct once, call :meth:`run` per cycle."""

    def __init__(
        self,
        store: CorrelationStore,
        *,
        config: Optional[CorrelationConfig] = None,
    ) -> None:
        self.store = store
        self.cfg = config or CorrelationConfig()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def run(self, now: Timestamp) -> list[Incident]:
        """Run one idempotent correlation pass; return the live incidents.

        Upserts the computed incidents (preserving identity by root fingerprint)
        and resolves any previously-open incident no longer produced.
        """
        issues = sorted(
            (i for i in self.store.correlatable_issues() if i.id is not None),
            key=lambda i: i.id or 0,
        )
        topo = self.store.topology()
        open_by_id: dict[int, Issue] = {i.id: i for i in issues if i.id is not None}

        existing = {inc.fingerprint: inc for inc in self.store.list_open_incidents()}

        # §17: "an incident resolves when all its members resolve." An incident's
        # identity is its root fingerprint, but the root issue can resolve while a
        # symptom is still open (the operator fixed the backhaul; a client is still
        # recovering). Such an incident must stay open under its own identity —
        # keeping its age, its acks, its story — with the root shown resolved,
        # rather than closing and re-emitting the lingering symptom as a brand-new
        # incident-of-one. We therefore find these "retained" incidents first and
        # withhold their still-open members from this pass's fresh grouping, so the
        # symptom is not double-counted.
        retained: list[tuple[Incident, list[IncidentMember], set[int]]] = []
        retained_issue_ids: set[int] = set()
        for inc in existing.values():
            if inc.root_issue_id in open_by_id:
                continue  # root still open -> recomputed fresh on the normal path
            members = self.store.get_incident_members(inc.id)
            surviving = {m.issue_id for m in members if m.issue_id in open_by_id}
            if surviving:
                retained.append((inc, members, surviving))
                retained_issue_ids |= surviving

        computable = [i for i in issues if i.id not in retained_issue_ids]
        computed = self._compute(computable, topo)

        live: list[Incident] = []
        produced_fingerprints: set[str] = set()

        for comp in computed:
            fp = incident_fingerprint(comp.root.fingerprint)
            produced_fingerprints.add(fp)
            severity = max_severity([m.issue.severity for m in comp.members])
            title, summary = self._render(comp, topo)

            prev = existing.get(fp)
            if prev is None:
                incident = self.store.insert_incident(
                    Incident(
                        fingerprint=fp,
                        root_issue_id=comp.root.id,  # type: ignore[arg-type]
                        severity=severity,
                        state=IncidentState.OPEN,
                        first_seen_ts=now,
                        last_seen_ts=now,
                        title=title,
                        summary=summary,
                    )
                )
            else:
                # Preserve id + first_seen_ts (age keeps counting); refresh the rest.
                prev.root_issue_id = comp.root.id  # type: ignore[assignment]
                prev.severity = severity
                prev.state = IncidentState.OPEN
                prev.last_seen_ts = now
                prev.resolved_ts = None
                prev.title = title
                prev.summary = summary
                self.store.update_incident(prev)
                incident = prev

            assert incident.id is not None
            self.store.replace_incident_members(
                incident.id,
                [
                    IncidentMember(
                        issue_id=m.issue.id,  # type: ignore[arg-type]
                        role=m.role,
                        rule=m.rule,
                        rationale=m.rationale,
                        incident_id=incident.id,
                    )
                    for m in comp.members
                ],
            )
            live.append(incident)

        # Keep retained incidents open: root resolved, ≥1 symptom still active.
        # The incident keeps its identity and age; membership shrinks to the root
        # (shown resolved) plus the surviving open symptoms; severity drops to the
        # worst still-open member.
        for inc, members, surviving in retained:
            produced_fingerprints.add(inc.fingerprint)
            inc.state = IncidentState.OPEN
            inc.last_seen_ts = now
            inc.resolved_ts = None
            inc.severity = max_severity([open_by_id[i].severity for i in surviving])
            self.store.update_incident(inc)
            kept = [m for m in members if m.role == IncidentRole.ROOT or m.issue_id in surviving]
            self.store.replace_incident_members(
                inc.id,  # type: ignore[arg-type]
                [
                    IncidentMember(
                        issue_id=m.issue_id,
                        role=m.role,
                        rule=m.rule,
                        rationale=m.rationale,
                        incident_id=inc.id,
                    )
                    for m in kept
                ],
            )
            live.append(inc)

        # Reconcile: an open incident we neither produced nor retained has lost its
        # root and has no surviving symptom -> every member has resolved, so the
        # incident resolves (§17).
        for fp, inc in existing.items():
            if fp in produced_fingerprints:
                continue
            inc.state = IncidentState.RESOLVED
            inc.resolved_ts = now
            self.store.update_incident(inc)

        return live

    # ------------------------------------------------------------------ #
    # Grouping
    # ------------------------------------------------------------------ #
    def _compute(self, issues: list[Issue], topo: TopologyIndex) -> list[_ComputedIncident]:
        by_id: dict[int, Issue] = {i.id: i for i in issues if i.id is not None}

        # Step 2-3: candidate roots per symptom (rule + relation + temporal guard).
        candidates: dict[int, list[_Candidate]] = {}
        for symptom in issues:
            sym_id = symptom.id
            assert sym_id is not None
            # A symptom the detector has self-attributed away from infrastructure
            # (e.g. a client flaky across many APs = its own radio) has no concrete
            # basis to sit under any root; leave it standalone.
            if not self._attribution_admits_root(symptom):
                continue
            sym_node = topo.get(symptom.entity_id)
            sym_name = self._label(symptom, topo)
            for root in issues:
                root_id = root.id
                assert root_id is not None
                if root_id == sym_id:
                    continue
                if not self._temporal_ok(root, symptom):
                    continue
                if not self._attributed_entity_ok(root, symptom, topo):
                    continue
                link = match_link(
                    root_detector=root.detector_key,
                    symptom_detector=symptom.detector_key,
                    root_node=topo.get(root.entity_id),
                    sym_node=sym_node,
                    topo=topo,
                    root_name=self._label(root, topo),
                    sym_name=sym_name,
                    root_title=root.title,
                    sym_title=symptom.title,
                    sym_evidence=symptom.evidence,
                )
                if link is None:
                    continue
                candidates.setdefault(sym_id, []).append(
                    _Candidate(
                        root=root,
                        rule_id=link.rule_id,
                        rationale=link.rationale,
                        rank=root_rank(root.detector_key),
                    )
                )

        # Step 4: each symptom takes its single best candidate root.
        chosen: dict[int, _Candidate] = {}
        for sym_id, cands in candidates.items():
            chosen[sym_id] = min(
                cands, key=lambda c: (c.rank, c.root.first_seen_ts, c.root.id or 0)
            )

        # Roll each issue up to its ultimate (most-upstream) root.
        groups: dict[int, list[Issue]] = {}
        for issue in issues:
            issue_id = issue.id
            assert issue_id is not None
            root_id = self._ultimate_root(issue_id, chosen, by_id)
            groups.setdefault(root_id, []).append(issue)

        # Step 5: build the computed incidents.
        incidents: list[_ComputedIncident] = []
        for root_id, members in groups.items():
            root_issue = by_id[root_id]
            symptom_issues = [i for i in members if i.id != root_id]
            member_records: list[_Member] = [
                _Member(
                    issue=root_issue,
                    role=IncidentRole.ROOT,
                    rule="root",
                    rationale=(
                        "Standalone issue; no related symptoms found."
                        if not symptom_issues
                        else "Identified as the root cause of this incident."
                    ),
                )
            ]
            for sym in sorted(symptom_issues, key=lambda i: i.id or 0):
                link = chosen[sym.id]  # type: ignore[index]
                member_records.append(
                    _Member(
                        issue=sym,
                        role=IncidentRole.SYMPTOM,
                        rule=link.rule_id,
                        rationale=link.rationale,
                    )
                )
            incidents.append(_ComputedIncident(root=root_issue, members=tuple(member_records)))

        # Deterministic order: most-severe root first, then by id.
        incidents.sort(key=lambda c: (root_rank(c.root.detector_key), c.root.id or 0))
        return incidents

    def _ultimate_root(
        self,
        issue_id: int,
        chosen: dict[int, _Candidate],
        by_id: dict[int, Issue],
    ) -> int:
        """Follow the chosen-root chain to the node that is nobody's symptom.

        Cycle-guarded (the rank guard makes cycles impossible, but a defensive
        break picks the highest-priority node in any cycle so grouping stays
        deterministic regardless of the starting issue).
        """
        path: list[int] = []
        seen: set[int] = set()
        cur = issue_id
        while cur in chosen and cur not in seen:
            seen.add(cur)
            path.append(cur)
            nxt = chosen[cur].root.id
            assert nxt is not None
            cur = nxt
        if cur in seen:
            # Cycle: the repeated node closes it; break at the best-priority member.
            cycle = path[path.index(cur) :]
            return min(
                cycle,
                key=lambda nid: (
                    root_rank(by_id[nid].detector_key),
                    by_id[nid].first_seen_ts,
                    nid,
                ),
            )
        return cur

    # ------------------------------------------------------------------ #
    # Guards + labels
    # ------------------------------------------------------------------ #
    def _temporal_ok(self, root: Issue, symptom: Issue) -> bool:
        """A symptom must not predate its root by more than the slack window.

        With a configured ``temporal_forward_window_s`` it also may not *postdate*
        the root by more than that window — a chronic root then cannot absorb a
        much-later, independently-caused symptom (opt-in; see the config).
        """
        if symptom.first_seen_ts < root.first_seen_ts - self.cfg.temporal_slack_s:
            return False
        fwd = self.cfg.temporal_forward_window_s
        if fwd is not None and symptom.first_seen_ts > root.first_seen_ts + fwd:
            return False
        return True

    @staticmethod
    def _attributed_entity_ok(root: Issue, symptom: Issue, topo: TopologyIndex) -> bool:
        """Reject a link whose root contradicts the symptom's recorded AP.

        When a client symptom names the AP its fault occurred on (``client.flaky``
        ``attributed_ap`` / ``wifi.sticky_client`` ``current_ap``), the candidate
        root must actually concern that AP — be it, be an ancestor of it, or feed
        it. A root that merely sits above the client's *current* parent AP (a
        different AP the client has since roamed to) is refused, so a fault on
        AP-Y is never pinned to AP-X's problem. No hint recorded → no constraint.
        """
        field = _ATTRIBUTED_AP_FIELD.get(symptom.detector_key)
        if field is None:
            return True
        hint = symptom.evidence.get(field)
        if not hint:
            return True  # detector recorded no specific AP -> topology decides alone
        ap_node = topo.find_entity(str(hint))
        if ap_node is None:
            # The named AP is not in this snapshot; we cannot confirm the root
            # concerns it, so we do not attach (conservative -> incident-of-one).
            return False
        ap_id = ap_node.entity_id
        root_id = root.entity_id
        if root_id is None:
            return False
        return root_id == ap_id or topo.is_ancestor(root_id, ap_id) or topo.feeds(root_id, ap_id)

    @staticmethod
    def _attribution_admits_root(symptom: Issue) -> bool:
        """False when the symptom's own detector attribution rules out any root.

        The correlation rules give a *topological* basis for a link; a symptom
        detector's recorded attribution can *contradict* it. A ``client.flaky``
        finding attributed to ``device`` means the detector saw the client drop
        across many APs and left ``attributed_ap`` empty -- attaching it to the AP
        it momentarily sat on would be a wrong attribution, so it stands alone.
        """
        blocked = _SELF_ATTRIBUTED_AWAY.get(symptom.detector_key)
        if not blocked:
            return True
        return str(symptom.evidence.get("attribution", "")) not in blocked

    @staticmethod
    def _label(issue: Issue, topo: TopologyIndex) -> str:
        """Human name for an issue's entity (topology name, else a fallback)."""
        node: Optional[TopoNode] = topo.get(issue.entity_id)
        if node is not None and node.name:
            return node.name
        if issue.entity_id is not None:
            return f"entity {issue.entity_id}"
        return issue.title

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #
    def _render(self, comp: _ComputedIncident, topo: TopologyIndex) -> tuple[str, str]:
        """Plain-language ``(title, summary)`` for a computed incident."""
        root = comp.root
        symptoms = [m for m in comp.members if m.role == IncidentRole.SYMPTOM]
        root_name = self._label(root, topo)

        if not symptoms:
            # Incident-of-one: it *is* the issue; no causal story to tell.
            return root.title, ""

        phrase_tmpl = _ROOT_PHRASE.get(root.detector_key)
        title = phrase_tmpl.format(name=root_name) if phrase_tmpl else root.title

        counts: dict[str, int] = {}
        for member in symptoms:
            counts[member.issue.detector_key] = counts.get(member.issue.detector_key, 0) + 1
        # Order symptom clauses by priority for a stable, sensible reading.
        ordered_keys = sorted(counts, key=lambda k: (root_rank(k), k))
        clauses = [_symptom_noun(k, counts[k]) for k in ordered_keys]
        locale = " in that cell" if root.detector_key in _CELL_LOCAL_ROOTS else ""
        summary = f"{title} is causing {_join_english(clauses)}{locale}."
        return title, summary
