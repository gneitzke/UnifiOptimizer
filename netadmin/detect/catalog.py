"""The detector catalog — the registry the engine iterates (section 6).

Every detector is registered once here with its metadata: its ``key`` / ``scope``
/ ``cadence`` (carried on the detector itself, per the ``Detector`` protocol), a
**severity ceiling** the engine clamps every finding to, and a **title template**
for the UI/dossier. Registration is an explicit list, not import side-effects, so
the wiring is greppable and the import graph is acyclic: this module imports the
detector classes; the detectors import only the engine's ``UNKNOWN`` sentinel;
the engine imports this catalog lazily. Duplicate keys are a hard error —
:func:`build_catalog` raises rather than let two detectors share a fingerprint
namespace.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    runtime_checkable,
)

from netadmin.domain.types import Cadence, EntityType, Severity

if TYPE_CHECKING:  # pragma: no cover - typing only
    from netadmin.detect.context import DetectorContext
    from netadmin.detect.engine import EvalResult


@runtime_checkable
class Detector(Protocol):
    """A registered detector (section 6).

    ``key`` is the stable identity that anchors every issue fingerprint and every
    threshold-override section. ``scope`` is the primary entity type the detector
    concerns (a catalog/UI grouping hint — a detector may inspect related types
    internally). ``cadence`` selects the tier the engine runs it in. ``evaluate``
    returns a (possibly empty) ``list[Finding]`` — an empty list is a *clear*
    evaluation — or the engine's ``UNKNOWN`` sentinel when a coverage gap makes the
    verdict untrustworthy.
    """

    key: str
    scope: EntityType
    cadence: Cadence

    def evaluate(self, ctx: "DetectorContext") -> "EvalResult":
        """Return findings (empty list = clear) or the ``UNKNOWN`` sentinel."""


@dataclass(frozen=True)
class EvidenceField:
    """Presentation metadata for one key in a detector's ``evidence`` dict.

    An optional, per-detector override so the issue detail page (Gitea #18) can
    show "Latency (p50): 52 ms" instead of "Window P50 Ms: 52" -- a real label and
    a unit, not a title-cased snake_case key. Narrative order (headline
    measurement, then its comparison, then supporting facts) comes for free from
    the evidence dict's own insertion order once it survives storage un-sorted
    (see :meth:`~netadmin.store.repository.Repository.insert_issue`); this only
    supplies *how* to label and unit-format a key the UI already renders in that
    order. A key absent from a detector's ``evidence_fields`` still renders --
    generically humanized, unitless -- so an unauthored detector never breaks.

    ``percent`` marks a 0..1 fraction that should display as N.N% (multiplied by
    100), e.g. ``loss_fraction``; leave it False for a value already on a 0-100 or
    natural-unit scale. ``unit`` defaults to ``"%"`` when ``percent`` is set and
    no unit was given explicitly, so a percent field can never render its
    multiplied-out number with no sign to explain it. ``duration`` marks a
    seconds-valued field that should display compact ("10 min", "1 h") rather
    than a bare "N s" -- e.g. a detector's analysis window.
    """

    key: str
    label: str
    unit: str = ""
    percent: bool = False
    duration: bool = False

    def __post_init__(self) -> None:
        if self.percent and not self.unit:
            object.__setattr__(self, "unit", "%")


# A confounder's narrated sentence: "what was checked and what was measured"
# (Gitea #18 item 2), computed from the issue's own evidence dict at request
# time so the number is this issue's, not a generic template value. Returning
# None (e.g. a referenced evidence key is missing on an older/differently-shaped
# issue) falls back to the bare humanized confounder key -- never a crash, never
# a fabricated number.
ConfounderNote = Callable[[Mapping[str, Any]], Optional[str]]


@dataclass(frozen=True)
class Playbook:
    """The admin's field guide for a detector (ARCHITECTURE.md section 6 table).

    Carried on the catalog so the LLM-investigator dossier (section 10) can print
    the same signature/confounder/fix guidance a human admin would consult. This
    is metadata *about* the detector, distinct from a :class:`Finding`'s per-issue
    ``confounders_checked`` audit trail: the playbook names every trap the class
    of problem is known for, whether or not this instance tested it.

    ``evidence_fields`` and ``confounder_notes`` are the issue-detail-page
    presentation layer (Gitea #18): an ordered label/unit for the evidence keys
    worth calling out by name, and a one-sentence narration per confounder key.
    Both are additive and optional -- a detector without them still renders, just
    without the polish -- so partial authoring never breaks a detector.
    """

    signature: str
    confounders: str = ""
    fix_guidance: str = ""
    evidence_fields: tuple[EvidenceField, ...] = ()
    confounder_notes: Mapping[str, ConfounderNote] = field(default_factory=dict)


@dataclass(frozen=True)
class CatalogEntry:
    """A detector plus the catalog metadata the engine and UI need."""

    detector: Detector
    severity_ceiling: Severity
    title_template: str
    playbook: Optional[Playbook] = None

    @property
    def key(self) -> str:
        return self.detector.key

    @property
    def scope(self) -> EntityType:
        return self.detector.scope

    @property
    def cadence(self) -> Cadence:
        return self.detector.cadence


class Catalog:
    """An immutable, key-indexed set of :class:`CatalogEntry` records."""

    def __init__(self, entries: Sequence[CatalogEntry]) -> None:
        by_key: dict[str, CatalogEntry] = {}
        for entry in entries:
            if entry.key in by_key:
                raise ValueError(f"duplicate detector key in catalog: {entry.key!r}")
            by_key[entry.key] = entry
        self._by_key = by_key
        self._entries = tuple(entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self._entries)

    @property
    def entries(self) -> tuple[CatalogEntry, ...]:
        return self._entries

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(self._by_key)

    def get(self, key: str) -> CatalogEntry:
        """The entry for ``key`` (raises ``KeyError`` if unregistered)."""
        return self._by_key[key]

    def by_cadence(self, cadence: Cadence) -> list[CatalogEntry]:
        """Registered entries of ``cadence``, in registration order."""
        return [e for e in self._entries if e.cadence is cadence]

    def by_scope(self, scope: EntityType) -> list[CatalogEntry]:
        return [e for e in self._entries if e.scope is scope]


def build_catalog(entries: Sequence[CatalogEntry]) -> Catalog:
    """Build a :class:`Catalog`, raising on any duplicate detector key."""
    return Catalog(entries)


# --------------------------------------------------------------------------- #
# Confounder-note formatting helpers (Gitea #18 item 2)
#
# Small, dependency-free formatters shared by the ``confounder_notes`` closures
# below, so every narrated sentence renders a number the same way. They take
# raw evidence values (already-decoded JSON: int/float/str/bool/None) and never
# raise -- a malformed or missing value degrades to "unknown" text rather than
# crashing the confounder note into a bare KeyError fallback.
# --------------------------------------------------------------------------- #
def _n(value: Any, digits: int = 1) -> str:
    """A number as a short decimal string ("52", "2.89"), or "unknown"."""
    if value is None:
        return "unknown"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f):
        return str(int(f))
    return f"{f:.{digits}f}"


def _pct(fraction: Any, digits: int = 1) -> str:
    """A 0..1 fraction as a percent string ("0.4%"), or "unknown"."""
    if fraction is None:
        return "unknown"
    try:
        return f"{float(fraction) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "unknown"


def _dur(seconds: Any) -> str:
    """Seconds as a short duration ("10 min", "1 h"), or "unknown"."""
    if seconds is None:
        return "unknown"
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return str(seconds)
    if s < 60:
        return f"{int(s)} s"
    if s < 3600:
        return f"{int(round(s / 60.0))} min"
    return f"{int(round(s / 3600.0))} h"


def _joined(values: Any) -> str:
    """A list of strings joined for prose, or "" when empty/absent."""
    if not values or not isinstance(values, (list, tuple)):
        return ""
    return ", ".join(str(v) for v in values)


def _coverage_note(_ev: Mapping[str, Any]) -> str:
    """Shared text for the many ``*coverage_gated`` confounder spellings."""
    return (
        "Coverage confirmed: enough recent device polls landed in the window to "
        "trust this verdict, not a data gap."
    )


def _channel_plan_spread_note(per_channel: Any) -> str:
    """``wifi.channel_plan``'s ``unavoidable_reuse_excluded`` note.

    Cites the actual busiest/quietest candidate-channel load when the evidence
    carries it (real ``per_channel`` shape: ``{"6": 3, "11": 1, ...}``), else
    falls back to the plain claim.
    """
    if isinstance(per_channel, Mapping) and per_channel:
        try:
            loads = [int(v) for v in per_channel.values()]
        except (TypeError, ValueError):
            loads = []
        if loads:
            return (
                f"Not forced sharing: the busiest candidate channel carries {max(loads)} of "
                f"our radios against {min(loads)} on the quietest, room enough to spread "
                "further."
            )
    return (
        "Not forced sharing: this band has room to spread its radios further across the "
        "candidate channels."
    )


# Field-guide text for every catalog-v1 detector, lifted from the ARCHITECTURE.md
# section 6 signature table (signature + confounders) and the section 9 fix
# planner (fix guidance). Keyed by ``detector_key`` and stitched onto each
# CatalogEntry in :func:`_default_entries`. The dossier (section 10) prints the
# entry for whichever detector opened the issue.
_PLAYBOOKS: dict[str, Playbook] = {
    "infra.controller_down": Playbook(
        signature="Lost-contact events plus consecutive poll failures against the controller.",
        confounders="Transient network blip vs a genuine outage; local collector network path.",
        fix_guidance="Restore controller connectivity/power and verify the network path. "
        "While it is down, every other detector is inhibited (absence of evidence is not "
        "evidence of absence).",
    ),
    "infra.device_down": Playbook(
        signature="Lost-contact events plus poll failures for a specific AP/switch/gateway.",
        confounders="Planned reboot/firmware upgrade window; upstream device down (parent).",
        fix_guidance="Check power/PoE on the port, the uplink cable, then reboot the device. "
        "A downed switch inhibits its own ports' issues.",
    ),
    "wired.bad_cable": Playbook(
        signature="rx_errors delta rate > 10/min sustained or > 0.001% of packets; OR a "
        "gigabit-capable peer negotiated at 10/100; OR a port running below a speed it "
        "held itself in the last 7 days (broken-pair downshift, rated or observed).",
        confounders="Known 100 Mbps device classes (overruled only by an observed ceiling that "
        "outlasts the current speed, so a brief blip cannot condemn a 10/100 device); counter "
        "age (a stale cumulative counter); an unmanaged-switch hop hiding the real port; a "
        "wired peer newer than the speed it would be credited with (a faster device swapped "
        "out for a slower one).",
        fix_guidance="Reseat then replace the patch cable; re-test the run. On an uplink port "
        "this is P1: the whole segment rides it.",
        evidence_fields=(
            EvidenceField("errors_per_min", "Error rate", "/min"),
            EvidenceField("errors_per_min_threshold", "Error-rate threshold", "/min"),
            EvidenceField("error_packet_fraction", "Errors, share of packets", percent=True),
            EvidenceField("negotiated_speed", "Negotiated speed", "Mbps"),
            EvidenceField("port_capable_speed", "Port's rated speed", "Mbps"),
            EvidenceField("observed_speed_max", "Speed this link has held", "Mbps"),
        ),
        confounder_notes={
            "coverage_gated": _coverage_note,
            "counter_reset_handled": lambda ev: (
                "Counter resets handled: the rate comes from monotonic-counter deltas, so a "
                "device reboot resetting the counter isn't misread as an error burst."
            ),
            "packet_volume_normalized": lambda ev: (
                f"Volume-normalised: errors are also {_pct(ev.get('error_packet_fraction'), 2)} "
                "of packet volume, not just a raw per-minute count."
                if ev.get("error_packet_fraction") is not None
                else None
            ),
            "known_100mbps_device_class": lambda ev: (
                "Peer device class checked: the wired peer on this port was checked against "
                "a known 10/100-by-design list, and is not one — a device that only has a "
                "100 Mbps port would explain the speed, and this one does not."
            ),
            "port_gigabit_capable": lambda ev: (
                f"Port confirmed gigabit-capable: negotiated at {_n(ev.get('negotiated_speed'), 0)} "
                f"Mbps against a {_n(ev.get('port_capable_speed'), 0)} Mbps ceiling, and no known "
                "10/100 device explains it."
                if ev.get("negotiated_speed") is not None
                else None
            ),
            "peer_predates_observed_speed": lambda ev: (
                "Peer age checked: the wired device on this port was already here before the "
                f"link last held {_n(ev.get('observed_speed_max'), 0)} Mbps, so that speed is "
                "this device's own history — not a faster machine that used to sit here."
                if ev.get("observed_speed_max") is not None
                else None
            ),
            "observed_speed_regression": lambda ev: (
                f"Measured against this link's own history: it has held "
                f"{_n(ev.get('observed_speed_max'), 0)} Mbps recently and is now at "
                f"{_n(ev.get('negotiated_speed'), 0)} Mbps, so the peer is provably capable of "
                "the higher speed and something on the run is holding it back. A device that "
                "simply cannot go faster would never have linked faster."
                if ev.get("observed_speed_max") is not None
                else None
            ),
        },
    ),
    "wired.duplex_mismatch": Playbook(
        signature="full_duplex=false on a modern link that should negotiate full duplex.",
        confounders="Legacy half-duplex device that is correct as-is.",
        fix_guidance="Set both ends to auto-negotiate (or matching forced full-duplex). "
        "A one-sided forced setting is the usual cause.",
        evidence_fields=(
            EvidenceField("speed", "Negotiated speed", "Mbps"),
            EvidenceField("full_duplex", "Full duplex"),
            EvidenceField("modern_speed_min", "Modern-link floor", "Mbps"),
        ),
        confounder_notes={
            "coverage_gated": _coverage_note,
            "link_up_checked": lambda ev: (
                "Link state checked: the port is up, so this isn't a stale duplex reading "
                "from a down link."
            ),
            "modern_speed_link": lambda ev: (
                f"Modern link confirmed: negotiated at {_n(ev.get('speed'), 0)} Mbps, at or "
                f"above the {_n(ev.get('modern_speed_min'), 0)} Mbps floor where half duplex "
                "is no longer expected."
            ),
        },
    ),
    "wired.port_flapping": Playbook(
        signature="≥5 link transitions/10 min, ≥10/h, or ≥12/24 h; infra ports weighted higher; "
        "PoE draw dropping to 0 between flaps signals a reboot loop.",
        confounders="A laptop docking/undocking; scheduled device reboots.",
        fix_guidance="Reseat cable/SFP; on a PoE reboot loop check the PoE budget and power-"
        "cycle the port; replace the cable if errors persist. A port that only trips the 24 h "
        "tier — dropping steadily around the clock rather than in bursts — is more often the "
        "device end than the run: check NIC/adapter power management before re-cabling.",
        evidence_fields=(
            EvidenceField("transitions_short", "Transitions, short window"),
            EvidenceField("window_short_s", "Short window", duration=True),
            EvidenceField("transitions_long", "Transitions, long window"),
            EvidenceField("window_long_s", "Long window", duration=True),
            EvidenceField("transitions_sustained", "Transitions, sustained window"),
            EvidenceField("window_sustained_s", "Sustained window", duration=True),
            EvidenceField("poe_reboot_loop", "PoE reboot loop"),
            EvidenceField("poe_min_w", "PoE draw, min", "W"),
            EvidenceField("poe_max_w", "PoE draw, max", "W"),
        ),
        confounder_notes={
            "coverage_gated": _coverage_note,
            # The sustained clause is guarded because issues predating that tier
            # carry no such evidence: a resolved port_flapping issue never gets
            # its evidence refreshed, so an unguarded f-string renders the old
            # shape as "unknown in unknown" forever. Same contract as every other
            # note here -- a missing key falls back, never fabricates.
            "sustained_transition_count": lambda ev: (
                f"Sustained, not a blip: {_n(ev.get('transitions_short'), 0)} transitions in the "
                f"last {_dur(ev.get('window_short_s'))}, {_n(ev.get('transitions_long'), 0)} in "
                f"{_dur(ev.get('window_long_s'))}"
                + (
                    f", {_n(ev.get('transitions_sustained'), 0)} in "
                    f"{_dur(ev.get('window_sustained_s'))}. A link that drops steadily all day "
                    "trips the widest window even when no single burst is fast enough for the "
                    "others."
                    if ev.get("transitions_sustained") is not None
                    and ev.get("window_sustained_s") is not None
                    else "."
                )
            ),
            # The evidence, not the confounder key, carries the verdict: this key only
            # means PoE data existed to check, not that a reboot loop was confirmed
            # (Gitea #18's "PoE Reboot Correlated" vs "PoE Reboot Loop: Yes" ambiguity).
            "poe_reboot_correlated": lambda ev: (
                f"PoE draw checked: it dropped to {_n(ev.get('poe_min_w'))} W between flaps "
                f"(up to {_n(ev.get('poe_max_w'))} W). That is a powered-device reboot loop, not a "
                "bad link."
                if ev.get("poe_reboot_loop")
                else "PoE draw checked across the window: no drop-to-zero pattern, so a "
                "powered-device reboot loop is ruled out."
            ),
        },
    ),
    "wired.uplink_saturation": Playbook(
        signature="Uplink bps > 80%/95% of negotiated speed for 5 min+ with rising tx_dropped; "
        "compared against the hour-of-day baseline first.",
        confounders="A one-off backup/transfer; expected peak-hour load.",
        fix_guidance="Add capacity (LAG or a faster uplink), shape/prioritise traffic, or "
        "rebalance devices across uplinks.",
    ),
    "wired.poe_budget": Playbook(
        signature="Sum of poe_power > 80%/90% of the switch budget; EVT_SW_PoeOverload.",
        confounders="A momentary inrush at device power-on.",
        fix_guidance="Redistribute PoE devices across switches, disable non-essential PoE ports, "
        "or move to a higher-budget PSU/switch.",
    ),
    "wired.stp_loop": Playbook(
        signature="EVT_SW_StpPortBlocking and stp_state churn.",
        confounders="Expected STP reconvergence after a planned topology change.",
        fix_guidance="Find and remove the physical loop (a duplicate cable between switches); "
        "confirm STP is enabled fleet-wide. No safe auto-fix.",
    ),
    "wired.broadcast_storm": Playbook(
        signature="Broadcast/multicast pps > 10× the 24 h baseline on multiple ports at once.",
        confounders="A legitimate multicast burst (imaging, discovery sweep). A port whose "
        "link is down is skipped rather than counted: combo uplinks (USW Flex 2.5G "
        "10GE/SFP+) mirror one uplink's counters onto both port entries, and the dead "
        "half would double-count the live one into a false multi-port storm.",
        fix_guidance="Enable storm control, locate the offending port/device, and rule out a "
        "loop feeding the storm.",
    ),
    "wired.sfp_degraded": Playbook(
        signature="SFP DOM out of band: rx power at or below the sensitivity floor or drifting "
        "down from its baseline, tx power below its floor, module temperature over its limit, "
        "bias current risen well above its own baseline (the aging-laser signature), or an "
        "sfp_rxfault / sfp_txfault latch.",
        confounders="A cold-start reading before the optic stabilises; a hot host chassis, which "
        "explains a warm module without the optic itself failing; vendor-specific bias limits the "
        "controller never exposes, so bias is judged against the module's own history.",
        fix_guidance="Clean or replace the fiber, then replace the SFP module if rx power, tx "
        "power, or bias current stays out of band. Fix host cooling first when the chassis is hot.",
    ),
    "infra.device_overheating": Playbook(
        signature="The controller's own overheating flag, a chassis temperature held at or above "
        "the critical tier for the whole window, or a sustained rise above the device's own "
        "temperature baseline.",
        confounders="Warm ambient air (every temperature-capable device hot at once); a post-"
        "reboot thermal transient; hardware with no sensor at all, which UniFi APs report as "
        "has_temperature=false and which is skipped rather than read as cool.",
        fix_guidance="Clear the vents and restore airflow, then check the fan: a hot chassis "
        "reporting fan_level 0 points at a dead fan. Move the device out of an enclosed or "
        "sun-facing space. The controller's overheating flag precedes throttling or a thermal "
        "shutdown, so treat that arm as urgent.",
    ),
    "wifi.sticky_client": Playbook(
        signature="RSSI < −75 sustained ≥ 10 min while another AP measured materially better for "
        "this same client, over that AP's own attachment intervals; corroborated by low rates and "
        "high retries.",
        confounders="No better AP in the client's history, so that is a coverage hole, a different "
        "issue, not a sticky client. A candidate AP that is itself out of airtime or hanging off a "
        "weak mesh backhaul, which is not somewhere better. A signal high-water mark measured on "
        "some third AP, which says nothing about the one being recommended.",
        fix_guidance="Enable 802.11k/v (UniFi calls it Roaming Assistant) so the client is handed "
        "a neighbour list and asked to move; lower the far AP's TX power so it has a reason to. "
        "Review AP placement if it clusters on one AP. Do NOT reach for min-RSSI: it "
        "deauthenticates rather than steering, so the client drops and re-scans instead of "
        "transitioning, which is worse for anything on a call.",
        evidence_fields=(
            EvidenceField("median_rssi", "RSSI (median)", "dBm"),
            EvidenceField("rssi", "RSSI", "dBm"),
            EvidenceField("rssi_floor_dbm", "Sticky floor", "dBm"),
            EvidenceField("sustained_fraction_below", "Time below floor", percent=True),
            EvidenceField("current_ap", "Current AP"),
            EvidenceField("ap", "Current AP"),
            EvidenceField("better_ap", "Better AP"),
            EvidenceField("better_ap_name", "Better AP"),
            EvidenceField("better_ap_median_rssi", "Better AP's RSSI", "dBm"),
            EvidenceField("better_ap_samples", "Readings on that AP"),
            EvidenceField("median_tx_rate_mbps", "TX rate (median)", "Mbps"),
            EvidenceField("low_rate_corroborated", "Low rate corroborates it"),
            EvidenceField("clustered_on_ap", "Clustered with other stuck clients"),
        ),
        confounder_notes={
            "better_ap_exists": lambda ev: (
                f"A better AP exists: this client held "
                f"{_n(ev.get('better_ap_median_rssi'), 0)} dBm while attached to "
                f"{ev.get('better_ap_name') or ev.get('better_ap') or 'another AP'}, well above "
                f"the {_n(ev.get('median_rssi', ev.get('rssi')), 0)} dBm it gets where it is now."
                if ev.get("better_ap_median_rssi") is not None
                else "A better AP exists in this client's own roam history, not a coverage hole."
            ),
            "per_ap_rssi_attributed": lambda ev: (
                f"That reading is credited to one AP: it is the median of "
                f"{_n(ev.get('better_ap_samples'), 0)} samples taken while the client was "
                "attached there, not its best signal anywhere on the site."
                if ev.get("better_ap_samples") is not None
                else None
            ),
            "candidate_ap_health_screened": lambda _ev: (
                "The suggested AP was checked for its own problems first: a radio running out of "
                "airtime, or a weak mesh backhaul, disqualifies it — a stronger signal behind a "
                "full cell is not somewhere better."
            ),
            "sustained_not_transient": lambda ev: (
                "Sustained, not transient: weak signal held for "
                f"{_pct(ev.get('sustained_fraction_below'), 0)} of the window."
                if ev.get("sustained_fraction_below") is not None
                else "Sustained, not transient: the weak signal held for most of the analysis "
                "window."
            ),
            "low_rate_corroborated": lambda ev: (
                f"Low PHY rate corroborates it: {_n(ev.get('median_tx_rate_mbps'))} Mbps "
                "median tx rate."
                if ev.get("median_tx_rate_mbps") is not None
                else None
            ),
        },
    ),
    "wifi.pingpong_roamer": Playbook(
        signature="Two APs, ≥ 4 roams ≤ 10 s apart (Meraki definition); stationary devices flagged "
        "at > 4-6/h (suspicious) and > 10-15/h (definite).",
        confounders="A genuinely mobile device walking a boundary between cells.",
        fix_guidance="Reduce overlapping-cell tx-power so the two cells stop trading places within "
        "a few dB, and enable 802.11k/v (Roaming Assistant) so the transition is negotiated rather "
        "than guessed. Min-RSSI is the wrong lever here: it has no hysteresis, it deauthenticates, "
        "and a client that is already bouncing bounces harder when it is thrown off entirely.",
        evidence_fields=(
            EvidenceField("roams", "Roams in window"),
            EvidenceField("roams_per_hour", "Roam rate", "/h"),
            EvidenceField("burst_run", "Longest burst"),
            EvidenceField("burst_max_gap_s", "Burst gap ceiling", duration=True),
            EvidenceField("distinct_aps", "Distinct APs"),
            EvidenceField("reason", "Fired on"),
        ),
        confounder_notes={
            "sustained_rate_over_window": lambda ev: (
                f"Rate sustained over the window: {_n(ev.get('roams'), 0)} roams "
                f"({_n(ev.get('roams_per_hour'))}/h), not a one-off pair."
            ),
            "two_ap_bounce_not_walk": lambda ev: (
                f"Two-AP bounce, not a walk: {_n(ev.get('burst_run'), 0)} roams between exactly "
                f"{_n(ev.get('distinct_aps'), 0)} APs, each within "
                f"{_n(ev.get('burst_max_gap_s'), 0)} s of the last."
            ),
        },
    ),
    "wifi.roam_quality": Playbook(
        signature="Roam events where post-roam RSSI is > 10 dB worse, or roam latency crosses its tier.",
        confounders="A roam into a deliberately weaker overflow cell.",
        fix_guidance="Tune coverage/power overlap; enable 802.11r/k/v where the client fleet supports it.",
    ),
    "wifi.min_rssi_misconfig": Playbook(
        signature="min-RSSI enabled on a mesh-uplink AP (latent outage), on a single-AP site, or "
        "set stricter than −70.",
        confounders="A dense multi-AP site where a moderate min-RSSI is correct.",
        fix_guidance="Remove min-RSSI on mesh-uplink and single-AP sites; relax any value stricter "
        "than −70. Never raise it on a mesh AP.",
        evidence_fields=(
            EvidenceField("min_rssi_dbm", "min-RSSI setting", "dBm"),
            EvidenceField("strict_floor_dbm", "Aggressive-setting floor", "dBm"),
            EvidenceField("reason", "Fired because"),
            EvidenceField("ap_count", "APs on site"),
            EvidenceField("on_mesh_ap", "On a mesh-uplink AP"),
        ),
        confounder_notes={
            "mesh_uplink_checked": lambda ev: (
                "This is a mesh-uplink AP: kicking a client here can kick the AP's own "
                "backhaul, a latent outage."
                if ev.get("on_mesh_ap")
                else "Checked and ruled out: this AP is not on a mesh uplink."
            ),
            "single_ap_site_checked": lambda ev: (
                f"Site has {_n(ev.get('ap_count'), 0)} AP: a kicked client has nowhere else to "
                "roam to."
                if ev.get("ap_count") == 1
                else f"Checked and ruled out: {_n(ev.get('ap_count'), 0)} APs on site, so a "
                "kicked client has somewhere to roam."
                if ev.get("ap_count") is not None
                else None
            ),
        },
    ),
    "wifi.channel_plan": Playbook(
        signature="Two scopes. Per radio: a 2.4 GHz radio off the 1/6/11 grid (channel_off_grid) "
        "or running 40 MHz (wide_channel_24ghz). Per band, site-scoped on the rf_env "
        "pseudo-entity: avoidable co-channel reuse, meaning the busiest candidate channel carries "
        "at least two more of our radios than the quietest, and 80 MHz 5 GHz width once the site "
        "has 4+ APs.",
        confounders="Reuse that is unavoidable: a band with more radios than candidate channels "
        "must share some, and a balanced maximal spread is optimal, so it never fires. A single-AP "
        "site, where the 5 GHz width policy does not apply. Neighbour mutual-RSSI is not in the "
        "store, so that refinement is not claimed.",
        fix_guidance="Per radio: set 2.4 GHz to 1/6/11 at 20 MHz. Per band: re-plan the 2.4 GHz "
        "channels jointly. The fix planner assigns them together, one step per radio moved, so "
        "two ends of a conflict are never sent to the same new channel. Narrow 5 GHz width under "
        "density. A contended band is P3 config context; it is P2 only when one of the named "
        "radios is also materially congested, which is the case worth acting on first.",
        evidence_fields=(
            EvidenceField("subtype", "Issue type"),
            EvidenceField("band", "Band", "GHz"),
            EvidenceField("channel", "Channel"),
            EvidenceField("ht_mhz", "Channel width", "MHz"),
            EvidenceField("candidate_channels", "Candidate channels"),
            EvidenceField("per_channel", "Radios per channel"),
            EvidenceField("unused_candidates", "Unused candidates"),
            EvidenceField("ap_count", "APs on site"),
            EvidenceField("wide_channel_ap_min", "Density floor"),
            EvidenceField("congested_radios", "Congested radios"),
            EvidenceField("materially_congested", "Materially congested"),
        ),
        confounder_notes={
            "own_radio_config_read": lambda ev: (
                "Config read directly from the radio: channel and width came from the "
                "controller's own reported config, not inferred."
            ),
            "unavoidable_reuse_excluded": lambda ev: (
                _channel_plan_spread_note(ev.get("per_channel"))
            ),
            "conflicted_radio_congestion_checked": lambda ev: (
                f"Congestion checked: {_joined(ev.get('congested_radios'))} also running at or "
                "above the congestion threshold, which is why this is P2, not just a config note."
                if ev.get("congested_radios")
                else None
            ),
            "single_ap_site_checked": lambda ev: (
                f"Density checked: {_n(ev.get('ap_count'), 0)} APs meet the "
                f"{_n(ev.get('wide_channel_ap_min'), 0)}-AP floor where 80 MHz cells start "
                "colliding."
                if ev.get("ap_count") is not None
                else None
            ),
            "wide_radio_congestion_checked": lambda ev: (
                f"Congestion checked: {_joined(ev.get('congested_radios'))} also running at or "
                "above the congestion threshold, which is why this is P2, not just a width note."
                if ev.get("congested_radios")
                else None
            ),
        },
    ),
    "wifi.dfs_recurring": Playbook(
        signature="EVT_AP_RadarDetected ≥ 1/day, or a repeating same-hour radar pattern.",
        confounders="A one-off radar hit, not yet a pattern.",
        fix_guidance="Move that AP/radio to a non-DFS channel to stop the recurring CAC blackouts.",
    ),
    "wifi.airtime_saturation": Playbook(
        signature="cu_total > 50% sustained (degraded) / > 80% (critical); self vs non-self "
        "utilization split for the fix path.",
        confounders="A brief high-throughput burst; expected peak-hour airtime.",
        fix_guidance="If self-utilization: reduce channel width, add capacity, steer to 5/6 GHz. "
        "If non-self: mitigate the interferer or re-plan the channel.",
        evidence_fields=(
            EvidenceField("cu_total_median", "Channel utilization (median)", "%"),
            EvidenceField("cu_self", "Self-generated utilization", "%"),
            EvidenceField("cu_non_self", "Non-self utilization", "%"),
            EvidenceField("dominant_source", "Dominant source"),
            EvidenceField("level", "Severity tier"),
        ),
        confounder_notes={
            "sustained_not_burst": lambda ev: (
                "Sustained, not a burst: the median channel utilization over the window is "
                f"{_n(ev.get('cu_total_median'))}%, not a single spike."
            ),
            "self_vs_non_self_split": lambda ev: (
                f"Self vs. non-self split: {_n(ev.get('cu_self'))}% self-generated against "
                f"{_n(ev.get('cu_non_self'))}% from others, so the fix targets "
                + (
                    "load we can shed or steer."
                    if ev.get("dominant_source") == "self"
                    else "the interferer or a channel change."
                )
            ),
        },
    ),
    "wifi.tx_power_loud": Playbook(
        signature="Multi-AP site running High/auto-max power; corroborated by sticky-client "
        "concentration and 2.4 GHz not held ~6 dB below 5 GHz.",
        confounders="A large open space that genuinely needs the power.",
        fix_guidance="Step tx-power down and keep 2.4 GHz ~6 dB below 5 GHz so cells do not "
        "over-reach and create sticky clients.",
    ),
    "wifi.legacy_rates": Playbook(
        signature="802.11b clients present, or the minimum basic rate left at 1 Mbps.",
        confounders="A site that must support legacy IoT on 802.11b.",
        fix_guidance="Disable legacy 802.11b rates and raise the minimum basic rate to reclaim airtime.",
    ),
    "wifi.band_steering": Playbook(
        signature="Dual-band client parked on 2.4 GHz at strong RSSI with an idle 5 GHz on the "
        "same AP; inverse: held on 5 GHz at ≤ −80.",
        confounders="A 2.4-GHz-only client that cannot move.",
        fix_guidance="Enable/tune band steering so capable clients ride 5 GHz where the signal supports it.",
    ),
    "wifi.mesh_uplink": Playbook(
        signature="Wireless uplink RSSI worse than −65/−70, hop count ≥ 3, or reconnect cycles; "
        "also a wired AP with meshing still enabled.",
        confounders="A deliberately meshed edge AP with an acceptable link.",
        fix_guidance="Improve mesh-AP placement/backhaul or add a wired drop; disable meshing on "
        "APs that are actually wired.",
        evidence_fields=(
            EvidenceField("median_uplink_rssi", "Uplink RSSI (median)", "dBm"),
            EvidenceField("uplink_rssi_dbm", "Uplink RSSI", "dBm"),
            EvidenceField("bad_rssi_dbm", "Bad-uplink floor", "dBm"),
            EvidenceField("uplink_rssi_threshold_dbm", "Uplink RSSI floor", "dBm"),
            EvidenceField("warn_rssi_dbm", "Warn floor", "dBm"),
            EvidenceField("hops", "Mesh hops"),
            EvidenceField("reconnect_cycles", "Reconnects in window"),
            EvidenceField("reconnects_24h", "Reconnects/24h"),
            EvidenceField("corroborated", "Corroborated"),
            EvidenceField("uplink_type", "Uplink type"),
            EvidenceField("mesh_enabled", "Meshing enabled"),
        ),
        confounder_notes={
            "sustained_poor_rssi": lambda ev: (
                "Sustained, not a dip: uplink RSSI held below "
                f"{_n(ev.get('bad_rssi_dbm', ev.get('uplink_rssi_threshold_dbm')), 0)} dBm for "
                "most of the window."
            ),
            "hop_depth_checked": lambda ev: (
                f"Hop depth checked: {_n(ev.get('hops'), 0)} hops back to the gateway "
                "corroborates a marginal backhaul."
                if ev.get("hops") is not None
                else None
            ),
            "reconnect_corroboration_checked": lambda ev: (
                f"Reconnects checked: "
                f"{_n(ev.get('reconnect_cycles', ev.get('reconnects_24h')), 0)} uplink "
                "reconnects in the window."
                if ev.get("reconnect_cycles") is not None or ev.get("reconnects_24h") is not None
                else "Reconnect history checked: no repeated uplink reconnects found, so this "
                "rests on the RSSI level alone."
            ),
            "uplink_type_read": lambda ev: (
                f"Uplink type read from the controller: currently {ev.get('uplink_type') or '?'} "
                "with meshing left enabled, a latent risk if it ever falls back to wireless."
            ),
            # Alternate spellings the same finding can carry (site-specific
            # threshold naming); same claim as sustained_poor_rssi/uplink_type_read.
            "sustained_over_window": lambda ev: (
                "Sustained, not a dip: uplink RSSI held below "
                f"{_n(ev.get('bad_rssi_dbm', ev.get('uplink_rssi_threshold_dbm')), 0)} dBm for "
                "most of the window."
            ),
            "wireless_uplink_confirmed": lambda ev: (
                f"Wireless uplink confirmed: this AP's backhaul is "
                f"{ev.get('uplink_type') or 'wireless'}, not wired, so RSSI is the right signal "
                "to judge it on."
            ),
        },
    ),
    "wifi.neighbor_density": Playbook(
        signature="Three or more strong (> −75 dBm), persistent-across-scans neighbour BSSes "
        "overlapping our channels on one band (co-channel on any band, adjacent-overlapping within "
        "4 channels on 2.4 GHz). One issue per band, site-scoped, not one per neighbour.",
        confounders="Our own hardware (known-BSSID allowlist plus the automatic own-Ubiquiti-prefix "
        "cross-reference); transient one-scan sightings; distant weak neighbours, which are counted "
        "in the scan total but never in the qualifying count.",
        fix_guidance="Re-plan the band: move our radios to the least-occupied channels in the "
        "per-channel breakdown, narrow 2.4 GHz to 20 MHz, and cut tx-power so cells stop reaching "
        "into the neighbours' air. A crowded band is P3 context; it is P2 only when an overlapped "
        "radio is also materially congested, which is the case worth acting on first.",
        evidence_fields=(
            EvidenceField("band", "Band", "GHz"),
            EvidenceField("qualifying_count", "Qualifying neighbours"),
            EvidenceField("total_seen", "Total BSSes seen"),
            EvidenceField("per_channel", "Neighbours per channel"),
            EvidenceField("overlapping_radios", "Our overlapped radios"),
            EvidenceField("congested_overlap_radios", "Congested overlapped radios"),
            EvidenceField("materially_congested", "Materially congested"),
        ),
        confounder_notes={
            "known_bssid_allowlist_checked": lambda ev: (
                "Allowlist checked: BSSIDs on the configured known-hardware allowlist are "
                "excluded before counting."
            ),
            "own_ubnt_hardware_excluded": lambda ev: (
                "Own hardware excluded: BSSIDs matching our own Ubiquiti MAC prefixes are "
                "cross-referenced out automatically."
            ),
            "transient_single_scan_excluded": lambda ev: (
                "Transient sightings excluded: a BSS seen in only one scan doesn't count "
                "toward the density."
            ),
            "persistence_over_distinct_recent_scans": lambda ev: (
                "Persistence required: a neighbour has to reappear across multiple distinct "
                "recent scans, not just linger in one stale reading."
            ),
            "weak_neighbor_excluded": lambda ev: (
                f"Weak neighbours excluded: {_n(ev.get('total_seen'), 0)} BSSes were seen in "
                f"total, but only the {_n(ev.get('qualifying_count'), 0)} strong enough to "
                "matter count toward the total."
                if ev.get("total_seen") is not None
                else None
            ),
            "own_radio_channel_overlap": lambda ev: (
                "Overlap confirmed: each qualifying neighbour was checked against our own "
                "radios' channels (co-channel on any band, adjacent on 2.4 GHz), not just "
                '"nearby".'
            ),
            "density_floor_applied": lambda ev: (
                f"Density floor applied: {_n(ev.get('qualifying_count'), 0)} qualifying "
                "neighbours cleared the minimum count before this fired."
                if ev.get("qualifying_count") is not None
                else None
            ),
            "overlapped_radio_congestion_checked": lambda ev: (
                f"Congestion checked: {_joined(ev.get('congested_overlap_radios'))} of our "
                "overlapped radios are also running hot, which is why this is P2, not just a "
                "density note."
                if ev.get("congested_overlap_radios")
                else None
            ),
        },
    ),
    "wifi.rogue_ap": Playbook(
        signature="Two security claims, per BSSID. ssid_spoof: a foreign BSS broadcasting one of "
        "our own SSIDs (from rest/wlanconf, else our clients' ESSIDs), P1 on first sighting. "
        "controller_flagged: the controller's own is_rogue attestation, P2, lifted to P1 when the "
        "BSSID's vendor+device MAC prefix matches a wired client on our LAN.",
        confounders="Our own hardware (allowlist plus own-Ubiquiti-prefix exclusion); a stale "
        "sighting outside the recency window; an unresolvable SSID set, which makes the spoof "
        "subtype UNKNOWN rather than a guess. Crowded air is not a rogue; that is "
        "wifi.neighbor_density.",
        fix_guidance="Treat a spoof as an incident: locate the BSSID (the reporting AP names the "
        "area), disconnect it, and rotate credentials if the twin was open while our SSID is "
        "secured. For a controller-flagged rogue with a wired-MAC prefix match, find the switch "
        "port and unplug it. If the BSS is ours or a known benign neighbour, add its BSSID to the "
        "allowlist. Note the limit: this cannot see an on-wire rogue the controller does not flag "
        "and whose wired MAC is unrelated to its BSSID.",
    ),
    "client.flaky": Playbook(
        signature="Reason-code-weighted disconnects (codes 1/2/3/7/15 pathological, 8 benign) "
        "above tier, then the attribution matrix (one client+one AP = device-or-deadspot; "
        "one client+many APs = device; many clients+one AP = AP fault; many bad-RSSI clients on "
        "one AP = coverage hole).",
        confounders="Benign reason code 8 (normal deauth); a single dead spot mistaken for a bad device.",
        fix_guidance="Follow the attribution: patch the device driver/firmware, service the AP, or "
        "remediate coverage; the matrix says which.",
        evidence_fields=(
            EvidenceField("weighted_disconnects", "Weighted disconnects"),
            EvidenceField("disconnects_24h", "Disconnects/24h"),
            EvidenceField("window_s", "Window", duration=True),
            EvidenceField("attribution", "Attribution"),
            EvidenceField("ap_count", "APs involved"),
            EvidenceField("attributed_ap", "Attributed to"),
            EvidenceField("ap", "AP"),
            EvidenceField("flaky_clients_on_attributed_ap", "Other flaky clients on that AP"),
            EvidenceField("reason_codes", "Disconnect reason codes"),
        ),
        confounder_notes={
            "benign_leave_downweighted": lambda ev: (
                "Reason codes weighted: benign deauths count for less, so "
                f"{_n(ev.get('weighted_disconnects', ev.get('disconnects_24h')))} weighted "
                "disconnects reflect the pathological ones, not routine leaves."
            ),
            "reason_code_weighted": lambda ev: (
                "Reason codes weighted: benign deauths count for less, so "
                f"{_n(ev.get('weighted_disconnects', ev.get('disconnects_24h')))} weighted "
                "disconnects reflect the pathological ones, not routine leaves."
            ),
            "poll_coverage_gated": _coverage_note,
            "many_aps_rules_out_single_ap_fault": lambda ev: (
                f"Ruled out a single AP: this client hit {_n(ev.get('ap_count'), 0)} different "
                "APs, so it's the device, not one AP."
            ),
            "low_rssi_distinguishes_coverage_from_ap_fault": lambda ev: (
                "RSSI checked: the client's signal was weak at the time, which points at a "
                "coverage hole, not a faulty AP."
            ),
            "many_clients_one_ap_rules_out_client_fault": lambda ev: (
                f"Other clients checked: {_n(ev.get('flaky_clients_on_attributed_ap'), 0)} "
                "other clients are also flaky on the same AP, which rules out one bad device."
            ),
            "single_client_single_ap_ambiguous": lambda ev: (
                "One client on one AP: the attribution matrix can't separate a bad device from "
                "a local dead spot from this alone."
            ),
            "attribution_matrix_applied": lambda ev: (
                "Attribution matrix applied: classified as "
                f"{str(ev.get('attribution') or '').replace('_', ' ')} from the AP/client "
                "fan-out."
            ),
        },
    ),
    "client.dhcp": Playbook(
        signature="169.254.x self-assigned addresses, association-without-IP > 30 s, or pool "
        "utilization > 85% on a UniFi gateway.",
        confounders="A single misconfigured static-IP client vs a network-wide pool exhaustion.",
        fix_guidance="Check the DHCP server/relay and VLAN; enlarge the pool or shorten leases if "
        "utilization is the cause. Network-wide is P1.",
    ),
    "client.known_pathology": Playbook(
        signature="Device-class knowledge base (ESP32 vs PMF/11r, iOS −70 roam scan, Sonos vs "
        "IGMPv3) matched against observed symptoms and the WLAN config.",
        confounders="A symptom that merely resembles a known pathology without the device class.",
        fix_guidance="Apply the device-class-specific WLAN setting (e.g. relax PMF/11r for ESP32, "
        "enable IGMP snooping for Sonos).",
    ),
    "wan.isp_degraded": Playbook(
        signature="Windowed probe-latency p50 sustained above ratio×(7-day baseline p50) across "
        "several 15-min windows, and/or the per-window failed-probe fraction sustained above the "
        "baseline fraction. Robust to Starlink's ~15 s handoff spikes (a brief minority of samples "
        "barely move a 15-min median); single-window blips never fire.",
        confounders="Starlink handoff jitter and brief obstruction dips (excluded by the sustained-"
        "multi-window rule); a local uplink-saturation event inflating latency; prober-down gaps "
        "(a coverage gap is UNKNOWN, failed probes are loss, not a gap).",
        fix_guidance="Confirm it is upstream (check the dish for obstructions/alignment on Starlink), "
        "then open an ISP ticket; fail over to a secondary WAN if one exists. No local fix. Sustained "
        "heavy loss escalates to P1.",
        evidence_fields=(
            EvidenceField("window_p50_ms", "Latency (p50)", "ms"),
            EvidenceField("baseline_p50_ms", "Baseline (p50, 7d)", "ms"),
            EvidenceField("ratio", "Ratio to baseline", "×"),
            EvidenceField("loss_fraction", "Probe loss", percent=True),
            EvidenceField("baseline_loss_fraction", "Baseline loss", percent=True),
            EvidenceField("sustained_windows_required", "Windows required to fire"),
            EvidenceField("latency_metric", "Latency source"),
            EvidenceField("latency_fired", "Fired on latency"),
            EvidenceField("loss_fired", "Fired on loss"),
        ),
        confounder_notes={
            "rolling_window_p50_robust_to_handoff_spikes": lambda ev: (
                "Robust to handoff spikes: judged on a 15-minute rolling p50, so Starlink's "
                "~15 s dish-handoff blips don't move it."
            ),
            "trend_vs_own_7d_baseline_not_absolute": lambda ev: (
                f"Judged against its own baseline: {_n(ev.get('window_p50_ms'), 0)} ms against "
                f"a {_n(ev.get('baseline_p50_ms'), 0)} ms 7-day baseline for this site, not a "
                "fixed number."
                if ev.get("baseline_p50_ms") is not None
                else "Judged against this site's own 7-day baseline, not a fixed number."
            ),
            "absolute_hold_floor_prevents_baseline_drift_autoresolve": lambda ev: (
                "Drift-proofed: latency has to hold below the degraded floor to auto-resolve, "
                "so a baseline that drifted up to match the fault can't quietly absorb it."
            ),
            "sustained_multi_window_required": lambda ev: (
                "Sustained, not a blip: elevated latency held for at least "
                f"{_n(ev.get('sustained_windows_required'), 0)} separate 15-minute windows."
            ),
            "per_window_min_probe_count_and_lost_probe_floor": lambda ev: (
                "Enough probes to trust: each window needed a minimum probe count and lost-"
                "probe floor before loss was judged, so one dropped probe in a small sample "
                "can't fire it."
            ),
            "loss_from_probe_run_accounting_not_gap": lambda ev: (
                f"Loss counted from failed probes: {_pct(ev.get('loss_fraction'))} of probes "
                "failed outright, not from a data gap."
                if ev.get("loss_fraction") is not None
                else None
            ),
            "starlink_jitter_profile": lambda ev: (
                "Tuned for Starlink: ordinary dish-handoff jitter is absorbed by the window "
                "median, so only a genuine, sustained shift fires."
            ),
        },
    ),
    "wan.bufferbloat": Playbook(
        signature="Probe RTT loaded-minus-idle > 200 ms while WAN throughput sits near the plan rate.",
        confounders="A momentary saturation spike, not sustained load.",
        fix_guidance="Enable Smart Queue Management (SQM/fq_codel) with the shaper set to ~90% of "
        "the measured plan rate.",
    ),
    "wan.flapping": Playbook(
        signature="EVT_GW_WANTransition ≥ 3 in 24 h.",
        confounders="A one-off ISP maintenance window.",
        fix_guidance="Check the WAN cable/modem and PPPoE session; escalate a repeating line to the ISP.",
    ),
    "wan.dns_slow": Playbook(
        signature="Probe: the gateway resolver > 150 ms / > 1 s sustained; compared against a "
        "public anchor to separate local from upstream.",
        confounders="A single slow lookup vs a sustained pattern; the anchor being slow too "
        "(then it is upstream, not local).",
        fix_guidance="If local: fix/replace the resolver. If upstream: switch the forwarders to a "
        "faster upstream DNS.",
        evidence_fields=(
            EvidenceField("resolver_p50_ms", "Resolver latency (p50)", "ms"),
            EvidenceField("anchor_p50_ms", "Public anchor latency (p50)", "ms"),
            EvidenceField("localised", "Localised to"),
            EvidenceField("severity_tier", "Severity tier"),
        ),
        confounder_notes={
            "anchor_comparison_localises_fault": lambda ev: (
                f"Localised with a public anchor: the anchor resolved in "
                f"{_n(ev.get('anchor_p50_ms'))} ms while the gateway resolver took "
                f"{_n(ev.get('resolver_p50_ms'))} ms, pointing at the "
                f"{ev.get('localised') or 'local'} resolver."
                if ev.get("anchor_p50_ms") is not None
                else "Localised against a public DNS anchor to separate a local resolver "
                "problem from an upstream one."
            ),
            "probe_coverage_gated": _coverage_note,
        },
    ),
    "net.coverage_hole": Playbook(
        signature="Cisco CHD adapted per-AP client-RSSI histogram: p25 < −75 or > 20% of "
        "client-hours < −80, and no better AP in those clients' history.",
        confounders="A transient cluster of far clients; a room nobody normally uses.",
        fix_guidance="Add or relocate an AP to fill the hole; raise power only as a stopgap, "
        "placement is the real fix.",
        evidence_fields=(
            EvidenceField("client_rssi_p25", "Client RSSI (p25)", "dBm"),
            EvidenceField("p25_rssi_dbm", "Client RSSI (p25)", "dBm"),
            EvidenceField("weak_line_dbm", "Weak-signal line", "dBm"),
            EvidenceField("very_weak_share", "Share very weak", percent=True),
            EvidenceField("very_weak_line_dbm", "Very-weak line", "dBm"),
            EvidenceField("client_hours_below_80_pct", "Client-hours below floor", "%"),
            EvidenceField("stuck_clients", "Stuck clients"),
            EvidenceField("sample_count", "RSSI samples"),
            EvidenceField("no_better_ap_in_history", "No better AP available"),
        ),
        confounder_notes={
            "no_better_ap_in_history_gate": lambda ev: (
                f"No better AP available: the {_n(ev.get('stuck_clients'), 0)} stuck clients "
                "have no history of a stronger AP, which rules out a sticky-client explanation."
                if ev.get("stuck_clients") is not None
                else "No better AP available in these clients' own history, which rules out a "
                "sticky-client explanation."
            ),
            "no_better_ap_available": lambda ev: (
                "No better AP available in these clients' own history, which rules out a "
                "sticky-client explanation."
            ),
            "sticky_client_excluded": lambda ev: (
                "Sticky client excluded: a client that has reached a better AP before is a "
                "sticky client, not a coverage hole, and isn't counted here."
            ),
            "min_samples_required": lambda ev: (
                f"Enough samples to trust it: {_n(ev.get('sample_count'), 0)} RSSI readings "
                "behind this p25."
                if ev.get("sample_count") is not None
                else "A minimum RSSI sample count was required before judging this AP's coverage."
            ),
            "sta_coverage_gated": _coverage_note,
            "sustained_over_window": lambda ev: (
                "Sustained over the analysis window, not a momentary dip."
            ),
        },
    ),
    "net.firmware_regression": Playbook(
        signature="Change-point on upgrade events: 7 d pre/post per device on disconnects/client-"
        "hour, port errors, radio resets; escalates when the same model+version degrades the "
        "fleet. First 2 h post-upgrade excluded.",
        confounders="Normal post-upgrade settling in the first 2 hours; an unrelated coincident change.",
        fix_guidance="Roll the affected device(s) back to the prior known-good firmware and hold "
        "fleet-wide upgrades on that build.",
        evidence_fields=(
            EvidenceField("post_disconnects_per_hour", "Disconnects/h, after upgrade", "/h"),
            EvidenceField("pre_disconnects_per_hour", "Disconnects/h, before upgrade", "/h"),
            EvidenceField("post_port_errors", "Port errors, after upgrade"),
            EvidenceField("pre_port_errors", "Port errors, before upgrade"),
            EvidenceField("version", "New firmware"),
            EvidenceField("model", "Model"),
            EvidenceField("fleet_devices_regressed", "Devices regressed on this build"),
            EvidenceField("fleet_wide", "Fleet-wide"),
        ),
        confounder_notes={
            "settle_window_excluded_2h": lambda ev: (
                "Settle window excluded: the first 2 hours after the upgrade are skipped, so "
                "normal post-reboot churn doesn't count as a regression."
            ),
            "pre_post_same_device_baseline": lambda ev: (
                "Same-device baseline: this device's own pre-upgrade rate "
                f"({_n(ev.get('pre_disconnects_per_hour'))}/h) is the comparison, not a fleet "
                "average."
                if ev.get("pre_disconnects_per_hour") is not None
                else "This device's own pre-upgrade rate is the baseline, not a fleet average."
            ),
            "device_coverage_gated": _coverage_note,
            "same_model_version_fleet_correlated": lambda ev: (
                f"Fleet-correlated: {_n(ev.get('fleet_devices_regressed'), 0)} devices on the "
                f"same {ev.get('model') or '?'} / {ev.get('version') or '?'} build regressed "
                "together, which is why this reads as a bad build, not one unlucky device."
            ),
        },
    ),
    "wan.latency_shift": Playbook(
        signature="A sustained CUSUM change-point in WAN latency: the post-shift regime sits well "
        "above the prior baseline median, holding rather than spiking.",
        confounders="A one-off latency spike (not a sustained regime); a local uplink-saturation "
        "event; an expected routing change.",
        fix_guidance="Correlate the shift date with ISP/routing or config changes; if it is "
        "upstream and sustained, open an ISP ticket referencing the change date.",
    ),
}


def _default_entries() -> list[CatalogEntry]:
    """The shipped catalog-v1 registrations.

    Imported inside the function so this module can be imported for its types
    (``Detector`` / ``CatalogEntry``) without eagerly pulling in every detector
    family. The detector families land here as their agents complete them.
    """
    from netadmin.detect.detectors.client import (
        DhcpClientDetector,
        FlakyClientDetector,
        KnownPathologyDetector,
    )
    from netadmin.detect.detectors.infra import (
        ControllerDownDetector,
        DeviceDownDetector,
        DeviceOverheatingDetector,
    )
    from netadmin.detect.detectors.net import CoverageHoleDetector, FirmwareRegressionDetector
    from netadmin.detect.detectors.wan import (
        BufferbloatDetector,
        DnsSlowDetector,
        IspDegradedDetector,
        LatencyShiftDetector,
        WanFlappingDetector,
    )
    from netadmin.detect.detectors.wifi import (
        AirtimeSaturationDetector,
        BandSteeringDetector,
        ChannelPlanDetector,
        DfsRecurringDetector,
        LegacyRatesDetector,
        MeshUplinkDetector,
        MinRssiMisconfigDetector,
        NeighborDensityDetector,
        PingpongRoamerDetector,
        RoamQualityDetector,
        RogueApDetector,
        StickyClientDetector,
        TxPowerLoudDetector,
    )
    from netadmin.detect.detectors.wired import (
        BadCableDetector,
        BroadcastStormDetector,
        DuplexMismatchDetector,
        PoeBudgetDetector,
        PortFlappingDetector,
        SfpDegradedDetector,
        StpLoopDetector,
        UplinkSaturationDetector,
    )

    entries = [
        CatalogEntry(
            detector=ControllerDownDetector(),
            severity_ceiling=Severity.P1,
            title_template="Controller unreachable",
        ),
        CatalogEntry(
            detector=DeviceDownDetector(),
            severity_ceiling=Severity.P1,
            title_template="{entity} is down",
        ),
        CatalogEntry(
            detector=DeviceOverheatingDetector(),
            severity_ceiling=Severity.P1,  # P1 controller flag, P2 sustained critical, P3 drift
            title_template="{entity} overheating",
        ),
        # --- wired.* (section 6) ---
        CatalogEntry(
            detector=BadCableDetector(),
            severity_ceiling=Severity.P1,  # P2 access, P1 on an uplink port
            title_template="Cable/link fault on {entity}",
        ),
        CatalogEntry(
            detector=DuplexMismatchDetector(),
            severity_ceiling=Severity.P2,
            title_template="Duplex mismatch on {entity}",
        ),
        CatalogEntry(
            detector=PortFlappingDetector(),
            severity_ceiling=Severity.P1,  # P2 access, P1 on an infra/uplink port
            title_template="Port flapping: {entity}",
        ),
        CatalogEntry(
            detector=UplinkSaturationDetector(),
            severity_ceiling=Severity.P2,
            title_template="Uplink saturation on {entity}",
        ),
        CatalogEntry(
            detector=PoeBudgetDetector(),
            severity_ceiling=Severity.P1,  # P2 warn, P1 critical / overload
            title_template="PoE budget pressure on {entity}",
        ),
        CatalogEntry(
            detector=StpLoopDetector(),
            severity_ceiling=Severity.P1,
            title_template="STP loop / blocking on {entity}",
        ),
        CatalogEntry(
            detector=BroadcastStormDetector(),
            severity_ceiling=Severity.P1,
            title_template="Broadcast storm on {entity}",
        ),
        CatalogEntry(
            detector=SfpDegradedDetector(),
            severity_ceiling=Severity.P2,
            title_template="SFP degraded on {entity}",
        ),
        # --- client.* (section 6) ---
        CatalogEntry(
            detector=FlakyClientDetector(),
            severity_ceiling=Severity.P2,  # P3 device, P2 by AP-fault attribution
            title_template="Client {entity} flaky",
        ),
        CatalogEntry(
            detector=DhcpClientDetector(),
            severity_ceiling=Severity.P1,  # P3 single, P1 site-wide DHCP failure
            title_template="Client {entity} DHCP failure",
        ),
        CatalogEntry(
            detector=KnownPathologyDetector(),
            severity_ceiling=Severity.P3,
            title_template="Client {entity} known pathology",
        ),
        # --- wan.* (section 6) ---
        CatalogEntry(
            detector=IspDegradedDetector(),
            severity_ceiling=Severity.P1,  # P2 latency, P1 with sustained loss
            title_template="WAN degraded",
        ),
        CatalogEntry(
            detector=LatencyShiftDetector(),
            severity_ceiling=Severity.P3,
            title_template="WAN latency regime changed",
        ),
        CatalogEntry(
            detector=DnsSlowDetector(),
            severity_ceiling=Severity.P2,
            title_template="DNS resolution slow",
        ),
        CatalogEntry(
            detector=BufferbloatDetector(),
            severity_ceiling=Severity.P2,
            title_template="Bufferbloat under load",
        ),
        CatalogEntry(
            detector=WanFlappingDetector(),
            severity_ceiling=Severity.P1,
            title_template="WAN flapping",
        ),
        # --- net.* (section 6) ---
        CatalogEntry(
            detector=CoverageHoleDetector(),
            severity_ceiling=Severity.P2,
            title_template="Coverage hole at {entity}",
        ),
        CatalogEntry(
            detector=FirmwareRegressionDetector(),
            severity_ceiling=Severity.P1,  # P2 single device, P1 fleet-wide bad build
            title_template="Firmware regression on {entity}",
        ),
        # --- wifi.* (section 6) ---
        CatalogEntry(
            detector=StickyClientDetector(),
            severity_ceiling=Severity.P2,  # P3, P2 when clustered on one AP
            title_template="Sticky client {entity}",
        ),
        CatalogEntry(
            detector=PingpongRoamerDetector(),
            severity_ceiling=Severity.P2,  # P3 suspicious, P2 definite ping-pong
            title_template="Ping-pong roamer {entity}",
        ),
        CatalogEntry(
            detector=RoamQualityDetector(),
            severity_ceiling=Severity.P3,
            title_template="Poor roam quality for {entity}",
        ),
        CatalogEntry(
            detector=MinRssiMisconfigDetector(),
            severity_ceiling=Severity.P2,  # P2 mesh/single-AP, P3 stricter-than-floor
            title_template="min-RSSI misconfigured on {entity}",
        ),
        CatalogEntry(
            detector=ChannelPlanDetector(),
            severity_ceiling=Severity.P2,  # P3 config audit, P2 when a named radio is congested
            title_template="Channel-plan issue on {entity}",
        ),
        CatalogEntry(
            detector=DfsRecurringDetector(),
            severity_ceiling=Severity.P2,  # P3 recurring, P2 same-hour clustering
            title_template="Recurring DFS radar on {entity}",
        ),
        CatalogEntry(
            detector=AirtimeSaturationDetector(),
            severity_ceiling=Severity.P1,  # P2 degraded, P1 critical
            title_template="Airtime saturation on {entity}",
        ),
        CatalogEntry(
            detector=TxPowerLoudDetector(),
            severity_ceiling=Severity.P2,  # P3, P2 with sticky concentration
            title_template="Loud tx-power on {entity}",
        ),
        CatalogEntry(
            detector=LegacyRatesDetector(),
            severity_ceiling=Severity.P3,
            title_template="Legacy-rate client {entity}",
        ),
        CatalogEntry(
            detector=BandSteeringDetector(),
            severity_ceiling=Severity.P3,
            title_template="Band-steering opportunity for {entity}",
        ),
        CatalogEntry(
            detector=MeshUplinkDetector(),
            severity_ceiling=Severity.P2,  # P2 weak uplink, P3 latent wired-mesh
            title_template="Weak mesh uplink on {entity}",
        ),
        CatalogEntry(
            detector=NeighborDensityDetector(),
            severity_ceiling=Severity.P2,  # P3 context, P2 when an overlapped radio is congested
            title_template="Crowded RF neighbourhood on {entity}",
        ),
        CatalogEntry(
            detector=RogueApDetector(),
            severity_ceiling=Severity.P1,  # P1 SSID spoof; P2 controller-flagged, P1 corroborated
            title_template="Rogue AP {entity}",
        ),
    ]

    # Stitch each detector's field-guide entry on by key; a detector without a
    # registered playbook simply carries ``None`` (the dossier degrades to a note).
    return [replace(e, playbook=_PLAYBOOKS.get(e.key)) for e in entries]


DEFAULT_CATALOG: Catalog = build_catalog(_default_entries())


__all__ = [
    "Detector",
    "CatalogEntry",
    "Playbook",
    "Catalog",
    "build_catalog",
    "DEFAULT_CATALOG",
]
