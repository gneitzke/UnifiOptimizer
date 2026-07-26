"""Client-centric detectors (section 6, ``client.*``).

Three WINDOW-tier detectors that watch individual clients rather than
infrastructure:

* :class:`FlakyClientDetector` (``client.flaky``) — reason-code-weighted
  disconnect storms, then the **attribution matrix** (one client/one AP vs one
  client/many APs vs many clients/one AP vs many clients/bad-RSSI-one-AP). The
  attribution lands in ``evidence`` and steers severity: a client thrashing
  across many APs is a device fault (P3); many clients thrashing on one AP is an
  AP fault (P2).
* :class:`DhcpClientDetector` (``client.dhcp``) — a client with a ``169.254.x``
  self-assigned (APIPA) address, or associated to an AP but holding no IP past a
  grace period. Severity scales with breadth: a single client is P3, a
  site-wide DHCP failure (many clients at once) is P1.
* :class:`KnownPathologyDetector` (``client.known_pathology``) — a device-class
  knowledge base (``data/wifi_device_capabilities.json``) plus a small built-in
  symptom table: a 2.4-GHz-only IoT chip that disconnects (PMF / 802.11r
  intolerance), or an Apple client roam-scanning near its −70 dBm trigger.

All three gate on ``fast_sta`` coverage: below 0.5 they return ``UNKNOWN`` rather
than mistake a collection gap for a healthy (or unhealthy) client. Attribution
never enters the fingerprint ``dims`` — it is volatile and belongs in evidence —
so a client keeps one stable issue identity as its attribution is refined.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional

from netadmin.detect.engine import COVERAGE_MIN, UNKNOWN, EvalResult
from netadmin.domain.entities import Entity, Finding
from netadmin.domain.types import Cadence, EntityType, Severity
from netadmin.logging import get_logger

_log = get_logger("detect.client")

KEY_FLAKY = "client.flaky"
KEY_DHCP = "client.dhcp"
KEY_KNOWN_PATHOLOGY = "client.known_pathology"

# 802.11 disconnect/deauth reason codes. The pathological set (unspecified,
# prior-auth-invalid, deauth-leaving, class-3-frame-from-nonassoc, 4-way-handshake
# timeout) points at a real association fault; code 8 (STA leaving BSS) is the
# benign signature of a normal roam/leave and is weighted down so ordinary
# mobility never reads as flakiness (ARCHITECTURE.md section 6 catalog).
PATHOLOGICAL_REASON_CODES: frozenset[int] = frozenset({1, 2, 3, 7, 15})
BENIGN_REASON_CODES: frozenset[int] = frozenset({8})

# Disconnect event keys the detector counts (wireless-user + wired-user).
DEFAULT_DISCONNECT_KEYS: tuple[str, ...] = ("EVT_WU_Disconnected", "EVT_LU_Disconnected")

# APIPA / link-local self-assigned prefix: a client showing this got no DHCP lease.
_APIPA_PREFIX = "169.254."

# Default location of the device-capability KB, resolved relative to the repo root
# (netadmin/detect/detectors/client.py -> parents[3] == repo root). Overridable via
# ``settings.thresholds['client.known_pathology']['kb_path']``.
_DEFAULT_KB_PATH = Path(__file__).resolve().parents[3] / "data" / "wifi_device_capabilities.json"


# --------------------------------------------------------------------------- #
# small numeric helpers (local: detectors may not import a shared util module)
# --------------------------------------------------------------------------- #
def _percentile(values_sorted: list[float], q: float) -> float:
    """Linear-interpolated percentile of ascending, non-empty ``values_sorted``."""
    n = len(values_sorted)
    if n == 1:
        return values_sorted[0]
    idx = q * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return values_sorted[lo] + (values_sorted[hi] - values_sorted[lo]) * frac


def _window_values(ctx: Any, entity_id: int, metric: str, seconds: int) -> list[float]:
    """Numeric sample values for ``(entity_id, metric)`` over the window, or ``[]``."""
    wr = ctx.window(entity_id, metric, seconds)
    if wr is None:
        return []
    out: list[float] = []
    for row in wr.rows:
        val = row.get("value")
        if val is not None:
            out.append(float(val))
    return out


def _reason_code(row: Any) -> Optional[int]:
    """Pull an 802.11 reason code out of a stored event row's ``data`` JSON.

    Controllers are inconsistent: some carry a numeric ``reason`` / ``reason_code``
    in the event payload, many carry nothing. A missing code is honestly ``None``
    (weighted as the neutral default), never guessed.
    """
    raw = None
    try:
        raw = row["data"]
    except (KeyError, IndexError, TypeError):
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    for field in ("reason", "reason_code", "reasonCode"):
        val = data.get(field)
        if isinstance(val, bool):
            continue
        if isinstance(val, int):
            return val
        if isinstance(val, str):
            try:
                return int(val)
            except ValueError:
                continue
    return None


# ====================================================================== #
# client.flaky
# ====================================================================== #
class FlakyClientDetector:
    """``client.flaky`` — weighted disconnect storms + the attribution matrix.

    Per client, disconnect events in the window are weighted by 802.11 reason code
    (pathological codes count full, a benign "leaving BSS" barely counts). A client
    whose weighted total crosses the tier is *flaky*; its finding then carries an
    ``attribution`` derived from the full site picture:

    * ``device`` — the client disconnected across **many APs** (its own radio is
      the common factor) → P3;
    * ``device_or_deadspot`` — **one client, one AP**, and no one else is flaky
      there → P3 (could be the client or a single bad spot);
    * ``ap_fault`` — **many clients** flaky on the **same AP** → P2 (the AP is the
      common factor);
    * ``coverage_hole`` — many clients flaky on one AP **and** those clients read
      poor RSSI → P2, and the finding points at the AP as the coverage suspect.
    """

    key = KEY_FLAKY
    scope = EntityType.CLIENT
    cadence = Cadence.WINDOW

    def evaluate(self, ctx: Any) -> EvalResult:
        window_s = int(ctx.threshold(self.key, "window_s", 3600))
        if ctx.coverage(window_s, "fast_sta") < COVERAGE_MIN:
            return UNKNOWN  # a poll gap hides both disconnects and attribution data

        threshold = float(ctx.threshold(self.key, "weighted_threshold", 5.0))
        default_weight = float(ctx.threshold(self.key, "default_weight", 0.5))
        benign_weight = float(ctx.threshold(self.key, "benign_weight", 0.1))
        many_aps = int(ctx.threshold(self.key, "many_aps_min", 2))
        many_clients = int(ctx.threshold(self.key, "many_clients_min", 3))
        bad_rssi = float(ctx.threshold(self.key, "bad_rssi_dbm", -75.0))
        keys = tuple(ctx.threshold(self.key, "disconnect_keys", DEFAULT_DISCONNECT_KEYS))
        since = ctx.now_ts - window_s

        clients = [c for c in ctx.entities(EntityType.CLIENT) if c.entity_id is not None]
        ap_by_id = {a.entity_id: a for a in ctx.entities(EntityType.AP)}

        # First pass: which clients are flaky, and on which AP(s).
        flaky: dict[int, dict[str, Any]] = {}
        for client in clients:
            weighted, ap_ids = self._weighted_disconnects(
                ctx, client, keys, since, default_weight, benign_weight
            )
            if weighted >= threshold:
                flaky[client.entity_id] = {
                    "client": client,
                    "weighted": weighted,
                    "ap_ids": ap_ids,
                }

        if not flaky:
            return []

        # How many *distinct flaky clients* hit each AP -> the many-clients axis.
        flaky_per_ap: dict[int, int] = {}
        for info in flaky.values():
            for ap_id in info["ap_ids"]:
                flaky_per_ap[ap_id] = flaky_per_ap.get(ap_id, 0) + 1

        findings: list[Finding] = []
        for info in flaky.values():
            findings.append(
                self._finding(
                    ctx,
                    info,
                    ap_by_id=ap_by_id,
                    flaky_per_ap=flaky_per_ap,
                    window_s=window_s,
                    many_aps=many_aps,
                    many_clients=many_clients,
                    bad_rssi=bad_rssi,
                )
            )
        return findings

    def _weighted_disconnects(
        self,
        ctx: Any,
        client: Entity,
        keys: Iterable[str],
        since: int,
        default_weight: float,
        benign_weight: float,
    ) -> tuple[float, set[int]]:
        """Sum reason-code-weighted disconnects for a client; collect the APs hit."""
        rows = ctx.events(entity_id=client.entity_id, keys=set(keys), since_ts=since)
        weighted = 0.0
        ap_ids: set[int] = set()
        for row in rows:
            code = _reason_code(row)
            if code in BENIGN_REASON_CODES:
                weighted += benign_weight
            elif code in PATHOLOGICAL_REASON_CODES:
                weighted += 1.0
            else:
                weighted += default_weight
            related = _row_val(row, "related_entity_id")
            if related is not None:
                ap_ids.add(int(related))
        # Fall back to the client's current attachment when events carry no AP link.
        if not ap_ids and client.parent_id is not None:
            ap_ids.add(int(client.parent_id))
        return weighted, ap_ids

    def _finding(
        self,
        ctx: Any,
        info: dict[str, Any],
        *,
        ap_by_id: dict[Any, Entity],
        flaky_per_ap: dict[int, int],
        window_s: int,
        many_aps: int,
        many_clients: int,
        bad_rssi: float,
    ) -> Finding:
        client: Entity = info["client"]
        ap_ids: set[int] = info["ap_ids"]
        confounders = ["benign_leave_downweighted", "poll_coverage_gated"]

        # --- attribution matrix ---
        if len(ap_ids) >= many_aps:
            attribution = "device"
            severity = Severity.P3
            attributed_ap: Optional[Entity] = None
            confounders.append("many_aps_rules_out_single_ap_fault")
        else:
            ap_id = next(iter(ap_ids)) if ap_ids else None
            attributed_ap = ap_by_id.get(ap_id)
            peers = flaky_per_ap.get(ap_id, 0) if ap_id is not None else 0
            if peers >= many_clients:
                rssi_vals = _window_values(ctx, client.entity_id, "rssi", window_s)
                poor = bool(rssi_vals) and _percentile(sorted(rssi_vals), 0.5) < bad_rssi
                if poor:
                    attribution = "coverage_hole"
                    confounders.append("low_rssi_distinguishes_coverage_from_ap_fault")
                else:
                    attribution = "ap_fault"
                    confounders.append("many_clients_one_ap_rules_out_client_fault")
                severity = Severity.P2
            else:
                attribution = "device_or_deadspot"
                severity = Severity.P3
                confounders.append("single_client_single_ap_ambiguous")

        label = client.name or client.native_id
        evidence: dict[str, Any] = {
            "weighted_disconnects": round(info["weighted"], 2),
            "window_s": window_s,
            "attribution": attribution,
            "ap_count": len(ap_ids),
            "flaky_clients_on_attributed_ap": (
                flaky_per_ap.get(next(iter(ap_ids)), 0) if len(ap_ids) == 1 else None
            ),
        }
        if attributed_ap is not None:
            evidence["attributed_ap"] = attributed_ap.name or attributed_ap.native_id
        return Finding(
            detector_key=self.key,
            entity=client,
            severity=severity,
            title=f"Client {label} flaky ({attribution.replace('_', ' ')})",
            dims={},  # attribution is volatile -> evidence only, never the fingerprint
            evidence=evidence,
            confounders_checked=confounders,
        )


# ====================================================================== #
# client.dhcp
# ====================================================================== #
class DhcpClientDetector:
    """``client.dhcp`` — APIPA self-assignment or association-without-IP.

    A client is failing DHCP when its recorded ``ip`` is a ``169.254.x`` APIPA
    address, or it is associated to an AP yet holds no IP at all past a grace
    period (long enough that a healthy client would have leased). Breadth sets
    severity: one client is a P3 device/port quirk; several clients failing at
    once is a P1 site-wide DHCP/scope outage.

    The ``association_without_ip`` arm is only trustworthy when a UniFi gateway is
    present: only then is the controller the DHCP/L3 authority with an authoritative
    lease table, so "no reported IP" genuinely means "no lease". On a gateway-less
    site the controller runs no DHCP and learns IPs only best-effort (ARP/traffic),
    so a missing IP is absent telemetry — a wired/static device it has not learned
    an address for — not a failure. That arm therefore no-ops without a gateway
    (the APIPA self-assignment signal stays unambiguous on any site), mirroring the
    gateway-less no-op the ``wan.*`` detectors and the pool-exhaustion axis honour.

    Pool-exhaustion (>85%) detection needs a UniFi gateway to report the scope;
    this site has none, so that axis is recorded as unavailable in evidence rather
    than silently dropped.
    """

    key = KEY_DHCP
    scope = EntityType.CLIENT
    cadence = Cadence.WINDOW

    def evaluate(self, ctx: Any) -> EvalResult:
        window_s = int(ctx.threshold(self.key, "coverage_window_s", 600))
        if ctx.coverage(window_s, "fast_sta") < COVERAGE_MIN:
            return UNKNOWN

        grace_s = int(ctx.threshold(self.key, "assoc_grace_s", 120))
        network_wide_min = int(ctx.threshold(self.key, "network_wide_min", 3))
        has_gateway = self._has_unifi_gateway(ctx)

        affected: list[tuple[Entity, dict[str, Any]]] = []
        for client in ctx.entities(EntityType.CLIENT):
            if client.entity_id is None:
                continue
            reason = self._dhcp_fault(ctx, client, grace_s, has_gateway)
            if reason is not None:
                affected.append((client, reason))

        if not affected:
            return []

        total = len(affected)
        severity = Severity.P1 if total >= network_wide_min else Severity.P3
        confounders = ["poll_coverage_gated", "assoc_grace_applied"]
        if not has_gateway:
            confounders.append("pool_exhaustion_unavailable_no_gateway")

        findings: list[Finding] = []
        for client, reason in affected:
            label = client.name or client.native_id
            evidence = {
                "fault": reason["fault"],
                "ip": reason["ip"],
                "affected_clients": total,
                "network_wide": severity is Severity.P1,
                "pool_utilization": "unavailable_no_unifi_gateway" if not has_gateway else None,
            }
            findings.append(
                Finding(
                    detector_key=self.key,
                    entity=client,
                    severity=severity,
                    title=f"Client {label} DHCP failure ({reason['fault']})",
                    dims={},
                    evidence=evidence,
                    confounders_checked=confounders,
                )
            )
        return findings

    def _dhcp_fault(
        self, ctx: Any, client: Entity, grace_s: int, has_gateway: bool
    ) -> Optional[dict[str, Any]]:
        ip = ctx.repo.current_state(client.entity_id, "ip")
        ap_mac = ctx.repo.current_state(client.entity_id, "ap_mac")
        if ip and str(ip).startswith(_APIPA_PREFIX):
            return {"fault": "apipa_self_assigned", "ip": str(ip)}
        # association_without_ip needs an authoritative lease table to mean anything;
        # without a UniFi gateway the controller has none, so a missing IP is absent
        # telemetry, not a DHCP failure. No-op this arm (APIPA above is unambiguous).
        if not has_gateway:
            return None
        associated = bool(ap_mac) or client.parent_id is not None
        no_ip = not ip
        seen_long_enough = (
            client.first_seen_ts is not None and (ctx.now_ts - client.first_seen_ts) > grace_s
        )
        if associated and no_ip and seen_long_enough:
            return {"fault": "association_without_ip", "ip": None}
        return None

    @staticmethod
    def _has_unifi_gateway(ctx: Any) -> bool:
        """A UniFi gateway that reports WAN health (probe-only gateways don't count)."""
        for gw in ctx.entities(EntityType.GATEWAY):
            if gw.entity_id is None:
                continue
            if ctx.repo.get_series(gw.entity_id, "wan_latency") is not None:
                return True
        return False


# ====================================================================== #
# client.known_pathology
# ====================================================================== #
class KnownPathologyDetector:
    """``client.known_pathology`` — device-class KB + symptom matching.

    Loads the WiFi device-capability KB and classifies each client by name/OUI,
    then matches a small built-in table of well-known device pathologies against
    observed symptoms:

    * ``iot_pmf_11r`` — a 2.4-GHz-only IoT chip (ESP32/ESP8266-class) that is
      disconnecting: cheap IoT radios frequently choke on Protected Management
      Frames / 802.11r fast-transition;
    * ``ios_aggressive_roam`` — an Apple client racking up roams: iOS roam-scans
      hard once RSSI passes ≈ −70 dBm, so an over-dense cell makes it thrash.

    WLAN-level config (PMF/11r flags per SSID) is not exposed to this layer, so a
    matched pathology records ``wlan_config: not_verified`` in evidence rather than
    asserting the SSID setting — the symptom is real, the exact SSID knob is a
    hypothesis the investigator confirms.
    """

    key = KEY_KNOWN_PATHOLOGY
    scope = EntityType.CLIENT
    cadence = Cadence.WINDOW

    def __init__(self) -> None:
        self._kb: Optional[dict[str, Any]] = None
        self._kb_path_loaded: Optional[str] = None

    def evaluate(self, ctx: Any) -> EvalResult:
        window_s = int(ctx.threshold(self.key, "window_s", 3600))
        if ctx.coverage(window_s, "fast_sta") < COVERAGE_MIN:
            return UNKNOWN

        roam_min = int(ctx.threshold(self.key, "ios_roam_min", 5))
        disc_min = int(ctx.threshold(self.key, "iot_disconnect_min", 3))
        kb = self._load_kb(ctx)
        since = ctx.now_ts - window_s

        findings: list[Finding] = []
        for client in ctx.entities(EntityType.CLIENT):
            if client.entity_id is None:
                continue
            finding = self._match(ctx, client, kb, since, window_s, roam_min, disc_min)
            if finding is not None:
                findings.append(finding)
        return findings

    def _match(
        self,
        ctx: Any,
        client: Entity,
        kb: dict[str, Any],
        since: int,
        window_s: int,
        roam_min: int,
        disc_min: int,
    ) -> Optional[Finding]:
        name = (client.name or "").lower()
        oui = str((client.meta or {}).get("oui") or "").lower()
        haystack = f"{name} {oui}"

        # --- IoT 2.4-only + disconnects -> PMF/11r intolerance ---
        if _matches_patterns(haystack, kb.get("known_2.4ghz_only", {}).get("patterns", [])):
            disconnects = len(
                ctx.events(
                    entity_id=client.entity_id,
                    keys=set(DEFAULT_DISCONNECT_KEYS),
                    since_ts=since,
                )
            )
            if disconnects >= disc_min:
                label = client.name or client.native_id
                return Finding(
                    detector_key=self.key,
                    entity=client,
                    severity=Severity.P3,
                    title=f"{label}: 2.4GHz IoT device likely PMF/802.11r intolerant",
                    dims={"pathology": "iot_pmf_11r"},
                    evidence={
                        "pathology": "iot_pmf_11r",
                        "device_class": "known_2.4ghz_only",
                        "disconnects": disconnects,
                        "wlan_config": "not_verified",
                    },
                    confounders_checked=[
                        "symptom_required_not_inventory_only",
                        "poll_coverage_gated",
                        "wlan_pmf_11r_flag_not_exposed",
                    ],
                )

        # --- Apple client + aggressive roam-scan ---
        if _matches_patterns(haystack, ["iphone", "ipad", "ipod", "macbook", " mac "]):
            roams = _window_values(ctx, client.entity_id, "roam_count", window_s)
            roam_total = sum(roams)  # roam_count is a counter -> stored deltas
            if roam_total >= roam_min:
                label = client.name or client.native_id
                return Finding(
                    detector_key=self.key,
                    entity=client,
                    severity=Severity.P3,
                    title=f"{label}: Apple client roam-scanning (−70 dBm trigger)",
                    dims={"pathology": "ios_aggressive_roam"},
                    evidence={
                        "pathology": "ios_aggressive_roam",
                        "device_class": "apple",
                        "roams_in_window": round(roam_total, 1),
                        "wlan_config": "not_verified",
                    },
                    confounders_checked=[
                        "symptom_required_not_inventory_only",
                        "poll_coverage_gated",
                    ],
                )
        return None

    def _load_kb(self, ctx: Any) -> dict[str, Any]:
        path = str(ctx.threshold(self.key, "kb_path", str(_DEFAULT_KB_PATH)))
        if self._kb is not None and self._kb_path_loaded == path:
            return self._kb
        kb: dict[str, Any] = {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                kb = loaded
        except (OSError, ValueError):
            _log.warning("known_pathology: could not load device KB at %s; running KB-empty", path)
        self._kb = kb
        self._kb_path_loaded = path
        return kb


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _row_val(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def _matches_patterns(haystack: str, patterns: Iterable[str]) -> bool:
    """True when any (lowercased) pattern is a substring of ``haystack``."""
    for pat in patterns:
        if pat and str(pat).lower() in haystack:
            return True
    return False


__all__ = [
    "KEY_FLAKY",
    "KEY_DHCP",
    "KEY_KNOWN_PATHOLOGY",
    "PATHOLOGICAL_REASON_CODES",
    "BENIGN_REASON_CODES",
    "FlakyClientDetector",
    "DhcpClientDetector",
    "KnownPathologyDetector",
]
