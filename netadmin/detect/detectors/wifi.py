"""Wi-Fi detectors (``docs/ARCHITECTURE.md`` section 6, ``wifi.*``).

The thirteen RF/roaming detectors, salvaged and re-thresholded from the July 2026
playbook research. Each reads only through the :class:`DetectorContext`, never
touches SQL, never constructs an issue, and lists in ``confounders_checked`` only
the false-positive traps it *actually* tested this cycle.

Data contract (what these read; the ingest/inventory layer populates it)
------------------------------------------------------------------------
These detectors read the store through documented keys. Fields the current
inventory sync already writes are used directly; config fields that a fuller
``stat/device`` capture will attach to entity ``meta`` are read **defensively**
(``meta.get`` / ``current_state``), so a detector no-ops or returns UNKNOWN when a
field is absent rather than raising — exactly the graceful-degradation the WAN
detectors use at this gateway-less site.

``RADIO`` entity (``native_id = "<ap_mac>:<band>"``, ``parent_id`` -> its AP):
  * ``meta["band"]``  — ``"ng"`` (2.4 GHz) | ``"na"`` (5 GHz) | ``"6e"`` (6 GHz)
  * ``meta["ht"]``    — channel width MHz (20/40/80/160)
  * state ``channel`` — current channel (``meta["channel"]`` fallback)
  * ``meta["tx_power_mode"]`` — ``"auto"|"high"|"medium"|"low"|"custom"``
  * ``meta["tx_power"]``      — dBm
  * ``meta["min_rssi_enabled"]`` / ``meta["min_rssi"]`` — min-RSSI config
  * metrics ``cu_total`` / ``cu_self_rx`` / ``cu_self_tx`` / ``num_sta``
``AP`` entity:
  * state ``uplink_type`` — ``"wire"`` | ``"wireless"``
  * ``meta["mesh_enabled"]`` — meshing configured (even while wired)
  * ``meta["uplink_hops"]`` (or state) — mesh hop count
  * metric ``uplink_rssi`` — wireless-uplink signal (dBm gauge)
  * events ``EVT_AP_RadarDetected`` (DFS), ``*_Lost_Contact`` (reconnect cycles)
``CLIENT`` entity:
  * ``meta["is_wired"]`` — wired clients are out of scope for every wifi detector
  * state ``ap_mac`` — current attachment (``state_history`` = the roam trail)
  * state ``band`` — client's current band (``"ng"``/``"na"``), for band steering
  * metrics ``rssi`` (dBm), ``tx_rate`` / ``rx_rate`` (Mbps), ``tx_retries`` /
    ``wifi_tx_attempts`` (counters), ``roam_count``
  * events ``EVT_WU_Roam`` — client roam transitions
``WLAN`` entity (``native_id`` = the controller's wlanconf id, ``name`` = SSID):
  * ``meta["enabled"]`` — whether the SSID is on the air
  * ``meta["security"]`` — configured security mode (``open``/``wpapsk``/…)
  Read only by ``wifi.rogue_ap``, to know which SSIDs are ours.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from netadmin.detect.engine import COVERAGE_MIN, UNKNOWN, DetectorResult, EvalResult
from netadmin.domain.entities import Entity, Finding
from netadmin.domain.types import Cadence, EntityType, Severity
from netadmin.logging import get_logger

_log = get_logger("detect.wifi")

KEY_STICKY_CLIENT = "wifi.sticky_client"
KEY_PINGPONG_ROAMER = "wifi.pingpong_roamer"
KEY_ROAM_QUALITY = "wifi.roam_quality"
KEY_MIN_RSSI_MISCONFIG = "wifi.min_rssi_misconfig"
KEY_CHANNEL_PLAN = "wifi.channel_plan"
KEY_DFS_RECURRING = "wifi.dfs_recurring"
KEY_AIRTIME_SATURATION = "wifi.airtime_saturation"
KEY_TX_POWER_LOUD = "wifi.tx_power_loud"
KEY_LEGACY_RATES = "wifi.legacy_rates"
KEY_BAND_STEERING = "wifi.band_steering"
KEY_MESH_UPLINK = "wifi.mesh_uplink"
KEY_NEIGHBOR_DENSITY = "wifi.neighbor_density"
KEY_ROGUE_AP = "wifi.rogue_ap"

# entities.entity_type for neighbor/rogue BSS rows, written by the daily
# ``stat/rogueap`` poll (``netadmin.ingest.collector.ROGUE_BSS_TYPE``). Kept as a
# plain string literal here, *not* imported from ``ingest``: ``detect`` never
# depends on ``ingest`` (the same rule ``context.py`` follows for job ids). It is
# also deliberately outside :class:`~netadmin.domain.types.EntityType` — a rogue
# BSS is foreign hardware, not a managed entity — so these rows are read straight
# off the repository (``repo.list_entities``), never through ``ctx.entities``
# (which would raise decoding an unknown ``EntityType``).
ROGUE_BSS_TYPE = "rogue_bss"

# entities.entity_type for the site-scoped RF-environment pseudo-entity
# ``wifi.neighbor_density`` and the plan-level half of ``wifi.channel_plan`` pin
# their per-band issues on (``native_id`` = ``rf:2.4`` / ``rf:5`` / ``rf:6``).
# Like ``rogue_bss`` it is deliberately outside
# :class:`~netadmin.domain.types.EntityType`: the air on a band is not a managed
# device. Nothing writes these rows -- the entity exists only to give the issue a
# stable fingerprint anchor, so the issue carries a NULL ``entity_id``. The fix
# engine mirrors both literals (it never imports ``detect``) to rebuild the
# anchor when it plans a fix for such an issue.
RF_ENV_TYPE = "rf_env"
RF_ENV_PREFIX = "rf:"

# Band classification. UniFi radio-table ``radio`` codes plus friendly aliases.
_BAND_24 = frozenset({"ng", "2.4", "2g", "2ghz"})
_BAND_5 = frozenset({"na", "5", "5g", "5ghz"})
_BAND_6 = frozenset({"6e", "6", "6g", "6ghz"})
_VALID_24_CHANNELS = frozenset({1, 6, 11})

# The channels ``wifi.channel_plan`` re-plans across when it judges whether
# co-channel reuse is avoidable. 2.4 GHz is the non-overlapping grid. 5 GHz is the
# non-DFS set that is safe at 40/80 MHz everywhere we ship; DFS channels are
# deliberately out (a plan that parks a cell on a radar channel is not a fix), and
# both are site-tunable through ``candidate_channels_24`` / ``candidate_channels_5``.
_CANDIDATES_24 = (1, 6, 11)
_CANDIDATES_5 = (36, 44, 149, 157)

# 802.11b data rates (Mbps): a client capped at these is a legacy-rate anchor
# dragging the whole cell's airtime (a 1 Mbps frame occupies the medium ~50x
# longer than a modern MCS frame).
_B_RATES = frozenset({1.0, 2.0, 5.5, 11.0})

_ROAM_EVENT_KEYS = frozenset({"EVT_WU_Roam", "EVT_WU_RoamRadio"})
_RADAR_EVENT_KEY = "EVT_AP_RadarDetected"
_LOST_CONTACT_SUFFIX = "_Lost_Contact"


# ---------------------------------------------------------------------- #
# Shared read/threshold helpers
# ---------------------------------------------------------------------- #
def _values(window: Any) -> list[float]:
    """The numeric ``value`` column of a :class:`WindowResult`, or ``[]``."""
    if window is None:
        return []
    out: list[float] = []
    for row in window.rows:
        v = row.get("value")
        if v is not None:
            out.append(float(v))
    return out


def _fraction_below(values: list[float], threshold: float) -> float:
    if not values:
        return 0.0
    return sum(1 for v in values if v < threshold) / len(values)


def _fraction_atleast(values: list[float], threshold: float) -> float:
    if not values:
        return 0.0
    return sum(1 for v in values if v >= threshold) / len(values)


def _median(values: list[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def _band_of(radio: Entity) -> Optional[str]:
    """Normalized band (``"2.4"`` | ``"5"`` | ``"6"``) for a RADIO entity, or None."""
    raw = radio.meta.get("band")
    if raw is None and radio.native_id and ":" in radio.native_id:
        raw = radio.native_id.rsplit(":", 1)[-1]
    if raw is None:
        return None
    code = str(raw).lower()
    if code in _BAND_24:
        return "2.4"
    if code in _BAND_5:
        return "5"
    if code in _BAND_6:
        return "6"
    return None


def _radio_channel(ctx: Any, radio: Entity) -> Optional[int]:
    """Current channel of a radio, from tracked state then ``meta`` fallback."""
    raw = None
    if radio.entity_id is not None:
        raw = ctx.repo.current_state(radio.entity_id, "channel")
    if raw is None:
        raw = radio.meta.get("channel")
    try:
        return None if raw is None else int(raw)
    except (TypeError, ValueError):
        return None


def _rf_entity(site_id: str, band: str) -> Entity:
    """The site-scoped ``rf_env`` pseudo-entity a per-band issue is anchored on.

    Carries no ``entity_id`` (nothing writes these rows); its ``native_id`` is the
    whole of the fingerprint's entity component, which is why it must stay
    ``rf:<band>`` forever.
    """
    return Entity(
        entity_type=RF_ENV_TYPE,  # type: ignore[arg-type]
        native_id=f"{RF_ENV_PREFIX}{band}",
        site_id=site_id,
        name=f"{band} GHz RF environment",
    )


def _channels(raw: Any) -> tuple[int, ...]:
    """A configured channel list as a sorted, deduped tuple of ints."""
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)):
        return ()
    try:
        values = {int(v) for v in raw}
    except (TypeError, ValueError):
        return ()
    return tuple(sorted(values))


def _count(n: int, noun: str) -> str:
    """``"1 radio"`` / ``"3 radios"``, so titles read like sentences, not templates."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return False


def _as_int(value: Any) -> Optional[int]:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _is_wireless_client(client: Entity) -> bool:
    """A wifi detector's subject is a wireless client; wired ones are excluded."""
    return not _as_bool(client.meta.get("is_wired"))


def _coverage_ok(ctx: Any, detector_key: str, *, job: str, default_window: int = 600) -> bool:
    """True when ``job`` coverage over its window clears the UNKNOWN floor.

    The single place each wifi detector decides whether it may speak. Below
    :data:`COVERAGE_MIN` the caller returns the engine's ``UNKNOWN`` sentinel —
    a collection gap is never a clean OK (ARCHITECTURE.md sections 4 & 6).
    """
    window_s = int(ctx.threshold(detector_key, "coverage_window_s", default_window))
    return ctx.coverage(window_s, job) >= COVERAGE_MIN


def _radios_by_ap(radios: Iterable[Entity]) -> dict[int, list[Entity]]:
    grouped: dict[int, list[Entity]] = {}
    for radio in radios:
        if radio.parent_id is not None:
            grouped.setdefault(radio.parent_id, []).append(radio)
    return grouped


def _event_data(row: Any) -> dict[str, Any]:
    raw = None
    try:
        raw = row["data"]
    except (KeyError, IndexError, TypeError):
        raw = None
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
        return decoded if isinstance(decoded, dict) else {}
    except (TypeError, ValueError):
        return {}


def _parse_meta(raw: Any) -> dict[str, Any]:
    """Decode an ``entities.meta`` JSON blob to a dict (``{}`` on anything else)."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
        return decoded if isinstance(decoded, dict) else {}
    except (TypeError, ValueError):
        return {}


def _norm_rogue_band(raw: Any, channel: Optional[int]) -> Optional[str]:
    """Normalized band (``"2.4"``/``"5"``/``"6"``) for a rogue BSS, or None.

    Prefers the reported band code; falls back to inferring from the channel
    number (1-14 -> 2.4, 32-196 -> 5). 6 GHz is only ever trusted from an explicit
    band code — its channel numbers collide with the other bands, and guessing
    would violate the conservatism rule, so an unlabelled high channel stays None
    and the rogue is skipped rather than mis-attributed.
    """
    if raw is not None:
        code = str(raw).lower()
        if code in _BAND_24:
            return "2.4"
        if code in _BAND_5:
            return "5"
        if code in _BAND_6:
            return "6"
    if channel is not None:
        if 1 <= channel <= 14:
            return "2.4"
        if 32 <= channel <= 196:
            return "5"
    return None


def _norm_mac(value: Any) -> Optional[str]:
    """Lower-cased, whitespace-stripped MAC/BSSID, or ``None`` if unusable."""
    if value is None:
        return None
    mac = str(value).strip().lower()
    return mac or None


def _mac_prefix(mac: Optional[str]) -> Optional[str]:
    """The first five octets of a colon-separated MAC (vendor + device bytes).

    UniFi virtual BSSIDs vary only the low octet per WLAN, so two BSSIDs from the
    same physical radio share this prefix; a different device never does.
    """
    if not mac:
        return None
    parts = mac.split(":")
    if len(parts) < 6:
        return None
    return ":".join(parts[:5])


def _bssid_set(raw: Any) -> set[str]:
    """Normalize a configured BSSID allowlist to a lower-cased set of strings."""
    if raw is None:
        return set()
    if isinstance(raw, str):
        raw = [raw]
    out: set[str] = set()
    try:
        for item in raw:
            if item is not None:
                out.add(str(item).strip().lower())
    except TypeError:
        return set()
    return out


# ====================================================================== #
# wifi.sticky_client
# ====================================================================== #
class StickyClientDetector:
    """``wifi.sticky_client`` — a client glued to a far AP while a better one exists.

    Fires when a wireless client's RSSI is sustained below the sticky floor
    (default -75 dBm) for the analysis window **and** that same client has, in its
    own roam history, been served materially better by a *different* AP. Without
    that historically-better-AP evidence the symptom is a coverage hole, not
    stickiness — the detector suppresses and lets ``net.coverage_hole`` own it.
    Concentration of sticky clients on one current AP raises the finding to P2.
    """

    key = KEY_STICKY_CLIENT
    scope = EntityType.CLIENT
    cadence = Cadence.WINDOW

    def evaluate(self, ctx: Any) -> EvalResult:
        if not _coverage_ok(ctx, self.key, job="fast_sta"):
            return UNKNOWN

        window_s = int(ctx.threshold(self.key, "window_s", 600))
        rssi_floor = float(ctx.threshold(self.key, "rssi_floor_dbm", -75))
        sustained_frac = float(ctx.threshold(self.key, "sustained_fraction", 0.8))
        min_samples = int(ctx.threshold(self.key, "min_samples", 4))
        better_margin = float(ctx.threshold(self.key, "better_ap_margin_db", 8))
        cluster_min = int(ctx.threshold(self.key, "cluster_min", 3))
        low_rate_mbps = float(ctx.threshold(self.key, "low_rate_mbps", 24))

        raw: list[tuple[Entity, dict[str, Any], Optional[str]]] = []
        unknown: set[int] = set()
        for client in ctx.entities(EntityType.CLIENT):
            if client.entity_id is None or not _is_wireless_client(client):
                continue
            values = _values(ctx.window(client.entity_id, "rssi", window_s))
            if len(values) < min_samples:
                # Too few RSSI samples of this client's own (power-save, a sparse
                # poll) to judge stickiness -> freeze its issue, never clear it.
                unknown.add(client.entity_id)
                continue
            if _fraction_below(values, rssi_floor) < sustained_frac:
                continue  # not sustained-weak

            current_ap = ctx.repo.current_state(client.entity_id, "ap_mac")
            better = self._better_ap_evidence(
                ctx, client, current_ap, window_s, rssi_floor, better_margin
            )
            if better is None:
                continue  # no better AP -> coverage hole, suppressed here

            evidence = {
                "current_ap": current_ap,
                "median_rssi": _median(values),
                "sustained_fraction_below": round(_fraction_below(values, rssi_floor), 3),
                "rssi_floor_dbm": rssi_floor,
                "better_ap": better["ap"],
                "better_ap_median_rssi": better["rssi"],
            }
            self._corroborate(ctx, client, window_s, low_rate_mbps, evidence)
            raw.append((client, evidence, current_ap))

        return DetectorResult.of(self._build(raw, cluster_min), unknown)

    def _better_ap_evidence(
        self,
        ctx: Any,
        client: Entity,
        current_ap: Optional[str],
        window_s: int,
        rssi_floor: float,
        margin: float,
    ) -> Optional[dict[str, Any]]:
        """Has THIS client been served materially better by a different AP?

        Reads the client's ``ap_mac`` roam history: a prior attachment to another
        AP whose recorded RSSI beat the current median by ``margin`` dB (and sat
        above the sticky floor) is proof a better AP exists for this device.
        """
        history = ctx.repo.state_history(client.entity_id, "ap_mac", limit=50)
        prior_aps = {
            row["new_value"]
            for row in history
            if row["new_value"] and row["new_value"] != current_ap
        }
        if not prior_aps:
            return None
        # Compare against the client's own better historical RSSI on another AP:
        # its long RSSI window spans those prior attachments, so its high-water
        # mark is the signal it enjoyed on a nearer AP.
        long_window = int(ctx.threshold(self.key, "history_window_s", 6 * 3600))
        hist_values = _values(ctx.window(client.entity_id, "rssi", long_window))
        if not hist_values:
            return None
        best = max(hist_values)
        current_values = _values(ctx.window(client.entity_id, "rssi", window_s))
        current_med = _median(current_values) or best
        if best >= rssi_floor and (best - current_med) >= margin:
            return {"ap": sorted(prior_aps)[0], "rssi": best}
        return None

    def _corroborate(
        self, ctx: Any, client: Entity, window_s: int, low_rate_mbps: float, evidence: dict
    ) -> None:
        rates = _values(ctx.window(client.entity_id, "tx_rate", window_s))
        med_rate = _median(rates)
        if med_rate is not None:
            evidence["median_tx_rate_mbps"] = med_rate
            evidence["low_rate_corroborated"] = med_rate <= low_rate_mbps

    def _build(
        self, raw: list[tuple[Entity, dict[str, Any], Optional[str]]], cluster_min: int
    ) -> list[Finding]:
        by_ap: dict[Optional[str], int] = {}
        for _, _, ap in raw:
            by_ap[ap] = by_ap.get(ap, 0) + 1

        findings: list[Finding] = []
        for client, evidence, ap in raw:
            clustered = ap is not None and by_ap.get(ap, 0) >= cluster_min
            confounders = ["better_ap_exists", "sustained_not_transient"]
            if "low_rate_corroborated" in evidence:
                confounders.append("low_rate_corroborated")
            label = client.name or client.native_id
            findings.append(
                Finding(
                    detector_key=self.key,
                    entity=client,
                    severity=Severity.P2 if clustered else Severity.P3,
                    title=f"Sticky client {label} on far AP",
                    dims={"ap": str(ap)} if ap else {},
                    evidence={**evidence, "clustered_on_ap": clustered},
                    confounders_checked=confounders,
                )
            )
        return findings


# ====================================================================== #
# wifi.pingpong_roamer
# ====================================================================== #
class PingpongRoamerDetector:
    """``wifi.pingpong_roamer`` — a client bouncing between two APs.

    Meraki's definition verbatim: a burst of >=4 roams between exactly 2 APs with
    consecutive roams <=10 s apart is a *definite* ping-pong (P2). On top of that,
    stationary-device rate tiers over the window flag a device roaming far more
    than a stationary client should — suspicious above ~5/h (P3), definite above
    ~12/h (P2). All from ``EVT_WU_Roam`` events.
    """

    key = KEY_PINGPONG_ROAMER
    scope = EntityType.CLIENT
    cadence = Cadence.WINDOW

    def evaluate(self, ctx: Any) -> EvalResult:
        if not _coverage_ok(ctx, self.key, job="fast_sta"):
            return UNKNOWN

        window_s = int(ctx.threshold(self.key, "window_s", 3600))
        min_roams = int(ctx.threshold(self.key, "burst_min_roams", 4))
        gap_s = int(ctx.threshold(self.key, "burst_max_gap_s", 10))
        suspicious_rate = float(ctx.threshold(self.key, "suspicious_rate_per_h", 5))
        definite_rate = float(ctx.threshold(self.key, "definite_rate_per_h", 12))

        start = ctx.now_ts - window_s
        findings: list[Finding] = []
        for client in ctx.entities(EntityType.CLIENT):
            if client.entity_id is None or not _is_wireless_client(client):
                continue
            events = ctx.events(entity_id=client.entity_id, keys=_ROAM_EVENT_KEYS, since_ts=start)
            if not events:
                continue
            roam_ts = sorted(int(e["ts"]) for e in events)
            n = len(roam_ts)
            aps = self._distinct_aps(events)
            burst = self._max_burst_run(roam_ts, gap_s)
            rate_per_h = n / (window_s / 3600.0)

            meraki = burst >= min_roams and len(aps) == 2
            severity: Optional[Severity] = None
            reason = ""
            if meraki:
                severity, reason = Severity.P2, "meraki_burst"
            elif rate_per_h >= definite_rate:
                severity, reason = Severity.P2, "rate_definite"
            elif rate_per_h >= suspicious_rate:
                severity, reason = Severity.P3, "rate_suspicious"
            if severity is None:
                continue

            confounders = ["sustained_rate_over_window"]
            if meraki:
                confounders.append("two_ap_bounce_not_walk")
            label = client.name or client.native_id
            findings.append(
                Finding(
                    detector_key=self.key,
                    entity=client,
                    severity=severity,
                    title=f"Ping-pong roamer {label}",
                    dims={},
                    evidence={
                        "roams": n,
                        "distinct_aps": len(aps),
                        "burst_run": burst,
                        "burst_max_gap_s": gap_s,
                        "roams_per_hour": round(rate_per_h, 2),
                        "reason": reason,
                    },
                    confounders_checked=confounders,
                )
            )
        return findings

    @staticmethod
    def _distinct_aps(events: list) -> set:
        aps: set = set()
        for e in events:
            rel = e["related_entity_id"]
            if rel is not None:
                aps.add(rel)
                continue
            data = _event_data(e)
            for k in ("from_ap", "to_ap", "ap_from", "ap_to"):
                if data.get(k) is not None:
                    aps.add(data[k])
        return aps

    @staticmethod
    def _max_burst_run(ts_sorted: list[int], gap_s: int) -> int:
        """Longest run of roams each within ``gap_s`` of the previous (count)."""
        if not ts_sorted:
            return 0
        best = run = 1
        for prev, cur in zip(ts_sorted, ts_sorted[1:]):
            if cur - prev <= gap_s:
                run += 1
                best = max(best, run)
            else:
                run = 1
        return best


# ====================================================================== #
# wifi.roam_quality
# ====================================================================== #
class RoamQualityDetector:
    """``wifi.roam_quality`` — roams that land the client on a worse signal.

    For each roam in the window, compares the client's settled RSSI just before
    and just after the transition. A post-roam signal sustained >10 dB worse is a
    bad roam (the client jumped to an inferior AP). A transient one-sample dip is
    excluded. Enough bad roams in the window opens a P3.
    """

    key = KEY_ROAM_QUALITY
    scope = EntityType.CLIENT
    cadence = Cadence.WINDOW

    def evaluate(self, ctx: Any) -> EvalResult:
        if not _coverage_ok(ctx, self.key, job="fast_sta"):
            return UNKNOWN

        window_s = int(ctx.threshold(self.key, "window_s", 3600))
        worse_db = float(ctx.threshold(self.key, "worse_than_db", 10))
        settle_s = int(ctx.threshold(self.key, "settle_s", 120))
        min_bad = int(ctx.threshold(self.key, "min_bad_roams", 2))

        start = ctx.now_ts - window_s
        findings: list[Finding] = []
        unknown: set[int] = set()
        for client in ctx.entities(EntityType.CLIENT):
            if client.entity_id is None or not _is_wireless_client(client):
                continue
            events = ctx.events(entity_id=client.entity_id, keys=_ROAM_EVENT_KEYS, since_ts=start)
            if not events:
                continue
            rssi_window = ctx.window(client.entity_id, "rssi", window_s)
            samples = [] if rssi_window is None else rssi_window.rows
            if len(samples) < 4:
                # Roams happened but too few RSSI samples to compare pre/post ->
                # cannot judge roam quality this cycle; freeze, don't clear.
                unknown.add(client.entity_id)
                continue

            bad = 0
            worst_delta = 0.0
            for e in events:
                delta = self._post_roam_delta(samples, int(e["ts"]), settle_s)
                if delta is not None and delta <= -worse_db:
                    bad += 1
                    worst_delta = min(worst_delta, delta)
            if bad < min_bad:
                continue

            label = client.name or client.native_id
            findings.append(
                Finding(
                    detector_key=self.key,
                    entity=client,
                    severity=Severity.P3,
                    title=f"Poor roam quality for {label}",
                    dims={},
                    evidence={
                        "bad_roams": bad,
                        "total_roams": len(events),
                        "worst_post_roam_delta_db": worst_delta,
                        "worse_than_db": worse_db,
                    },
                    confounders_checked=[
                        "transient_dip_excluded",
                        "settled_rssi_compared",
                    ],
                )
            )
        return DetectorResult.of(findings, unknown)

    @staticmethod
    def _post_roam_delta(samples: list[dict], roam_ts: int, settle_s: int) -> Optional[float]:
        """Settled-after minus settled-before RSSI around ``roam_ts`` (dB), or None.

        "Settled" = the median of the samples in the ``settle_s`` window on each
        side, so a single transient dip at the moment of roam does not count as a
        bad roam — only a sustained drop does.
        """
        before = [
            float(r["value"])
            for r in samples
            if roam_ts - settle_s <= int(r["ts"]) < roam_ts and r.get("value") is not None
        ]
        after = [
            float(r["value"])
            for r in samples
            if roam_ts <= int(r["ts"]) <= roam_ts + settle_s and r.get("value") is not None
        ]
        med_before = _median(before)
        med_after = _median(after)
        if med_before is None or med_after is None:
            return None
        return med_after - med_before


# ====================================================================== #
# wifi.min_rssi_misconfig
# ====================================================================== #
class MinRssiMisconfigDetector:
    """``wifi.min_rssi_misconfig`` — min-RSSI enabled where it does harm.

    A config audit. Min-RSSI kicks clients below a floor to force a roam, which is
    only safe with somewhere to roam *to*. It fires when min-RSSI is enabled on:
    a mesh-uplink AP (kicking the uplink is a latent outage — P2), a single-AP
    site (no roam target, just disconnections — P2), or set stricter than -70 dBm
    (aggressive drops — P3).
    """

    key = KEY_MIN_RSSI_MISCONFIG
    scope = EntityType.RADIO
    cadence = Cadence.DAILY

    def evaluate(self, ctx: Any) -> EvalResult:
        if not _coverage_ok(ctx, self.key, job="fast_device"):
            return UNKNOWN

        strict_floor = float(ctx.threshold(self.key, "strict_floor_dbm", -70))
        aps = {ap.entity_id: ap for ap in ctx.entities(EntityType.AP) if ap.entity_id is not None}
        ap_count = len(aps)

        findings: list[Finding] = []
        for radio in ctx.entities(EntityType.RADIO):
            if not _as_bool(radio.meta.get("min_rssi_enabled")):
                continue
            min_rssi = _as_int(radio.meta.get("min_rssi"))
            parent = aps.get(radio.parent_id)
            mesh = parent is not None and _is_mesh_ap(ctx, parent)
            single_ap = ap_count == 1
            too_strict = min_rssi is not None and min_rssi > strict_floor

            if not (mesh or single_ap or too_strict):
                continue

            if mesh:
                severity, reason = Severity.P2, "mesh_uplink_ap"
            elif single_ap:
                severity, reason = Severity.P2, "single_ap_site"
            else:
                severity, reason = Severity.P3, "stricter_than_floor"

            label = radio.name or radio.native_id
            findings.append(
                Finding(
                    detector_key=self.key,
                    entity=radio,
                    severity=severity,
                    title=f"min-RSSI misconfigured on {label}",
                    dims={"band": _band_of(radio) or "?"},
                    evidence={
                        "min_rssi_dbm": min_rssi,
                        "reason": reason,
                        "ap_count": ap_count,
                        "on_mesh_ap": mesh,
                        "strict_floor_dbm": strict_floor,
                    },
                    confounders_checked=[
                        "mesh_uplink_checked",
                        "single_ap_site_checked",
                    ],
                )
            )
        return findings


# ====================================================================== #
# wifi.channel_plan
# ====================================================================== #
class ChannelPlanDetector:
    """``wifi.channel_plan``: per-radio channel defects and per-band plan defects.

    Two kinds of problem live under this key, and they are reported at two
    different scopes because they have two different fixes.

    **Per-radio config defects, one issue per radio.**
    ``channel_off_grid`` (a 2.4 GHz radio off the non-overlapping 1/6/11 grid) and
    ``wide_channel_24ghz`` (40 MHz on 2.4 GHz, which leaves no room for three
    cells). Each radio is independently wrong, independently fixable by a single
    channel/width change, and keeps the fingerprint it has always had.

    **Plan-level defects, one site-scoped issue per band**, pinned on the
    ``rf_env`` pseudo-entity ``rf:<band>`` that ``wifi.neighbor_density``
    introduced:

    ``co_channel_reuse``
        Our own radios pile onto one channel while the band's candidate set still
        has room. Reporting this per radio was wrong three ways: one physical
        conflict became one issue per member, a rotation applied independently to
        both members moved both onto the *same* new channel, and, since a band
        with more radios than candidate channels **must** reuse some of them,
        optimal assignments were flagged as permanent, unfixable defects. So the
        fire condition is *avoidability*, not sharing: with our radios counted per
        candidate channel, the band fires only when the busiest candidate carries
        at least two more radios than the quietest one. That is exactly the
        condition under which moving one radio strictly improves the spread; a
        balanced maximal spread (four radios over 1/6/11, six radios over three
        channels) is optimal and stays silent. Radios sitting off the candidate
        set are not counted here: on 2.4 GHz that radio already has its own
        ``channel_off_grid`` issue, and on 5 GHz choosing among DFS channels is
        RF planning we do not claim to do.

    ``wide_channel_dense_5ghz``
        80 MHz on 5 GHz once the site has ``wide_channel_ap_min`` APs (too few
        non-overlapping 80 MHz channels to go round). One policy decision, so one
        issue for the band; the affected radios ride along as evidence.

    Severity is P3, a config audit with no measured harm, and lifts to
    P2 only when one of the radios named in the finding is *materially congested*
    (median ``cu_total`` at or above ``congested_cu_pct`` over
    ``congested_window_s``), the same escalation and the same thresholds
    ``wifi.neighbor_density`` uses, and the hook into ``wifi.airtime_saturation``
    through correlation.

    Neighbour/rogue mutual-RSSI is not in the store, so that Cisco refinement is
    deliberately not claimed as checked.
    """

    key = KEY_CHANNEL_PLAN
    scope = EntityType.RADIO
    cadence = Cadence.DAILY

    def evaluate(self, ctx: Any) -> EvalResult:
        if not _coverage_ok(ctx, self.key, job="fast_device"):
            return UNKNOWN

        wide_ap_min = int(ctx.threshold(self.key, "wide_channel_ap_min", 4))
        congest_window = int(ctx.threshold(self.key, "congested_window_s", 900))
        congest_cu = float(ctx.threshold(self.key, "congested_cu_pct", 50))
        candidates = {
            "2.4": _channels(ctx.threshold(self.key, "candidate_channels_24", _CANDIDATES_24)),
            "5": _channels(ctx.threshold(self.key, "candidate_channels_5", _CANDIDATES_5)),
        }

        radios = [r for r in ctx.entities(EntityType.RADIO) if r.entity_id is not None]
        ap_names = {
            ap.entity_id: (ap.name or ap.native_id)
            for ap in ctx.entities(EntityType.AP)
            if ap.entity_id is not None
        }
        ap_count = len({r.parent_id for r in radios if r.parent_id is not None})

        findings: list[Finding] = []
        placed: dict[str, dict[int, list[Entity]]] = {}
        wide_5: list[Entity] = []
        for radio in radios:
            band = _band_of(radio)
            channel = _radio_channel(ctx, radio)
            ht = _as_int(radio.meta.get("ht"))

            if band == "2.4" and channel is not None and channel not in _VALID_24_CHANNELS:
                findings.append(self._radio_finding(radio, "channel_off_grid", band, channel, ht))
            if band == "2.4" and ht is not None and ht >= 40:
                findings.append(self._radio_finding(radio, "wide_channel_24ghz", band, channel, ht))
            if band == "5" and ht is not None and ht >= 80 and ap_count >= wide_ap_min:
                wide_5.append(radio)
            if band is not None and channel is not None and channel in candidates.get(band, ()):
                placed.setdefault(band, {}).setdefault(channel, []).append(radio)

        for band in sorted(placed):
            conflict = self._conflict_finding(
                ctx,
                band,
                candidates[band],
                placed[band],
                ap_names,
                congest_window,
                congest_cu,
            )
            if conflict is not None:
                findings.append(conflict)

        if wide_5:
            findings.append(
                self._width_finding(
                    ctx, wide_5, ap_names, ap_count, wide_ap_min, congest_window, congest_cu
                )
            )
        return findings

    # ------------------------------------------------------------------ #
    # Per-radio defects (fingerprints unchanged since the first release)
    # ------------------------------------------------------------------ #
    def _radio_finding(
        self,
        radio: Entity,
        subtype: str,
        band: Optional[str],
        channel: Optional[int],
        ht: Optional[int],
    ) -> Finding:
        label = radio.name or radio.native_id
        return Finding(
            detector_key=self.key,
            entity=radio,
            severity=Severity.P3,
            title=f"Channel-plan issue ({subtype}) on {label}",
            dims={"subtype": subtype, "band": band or "?"},
            evidence={"subtype": subtype, "band": band, "channel": channel, "ht_mhz": ht},
            confounders_checked=["own_radio_config_read"],
        )

    # ------------------------------------------------------------------ #
    # Plan-level defects (one site-scoped issue per band)
    # ------------------------------------------------------------------ #
    def _conflict_finding(
        self,
        ctx: Any,
        band: str,
        candidates: tuple[int, ...],
        by_channel: dict[int, list[Entity]],
        ap_names: dict[Any, str],
        congest_window: int,
        congest_cu: float,
    ) -> Optional[Finding]:
        """One ``co_channel_reuse`` finding for ``band``, or None when it is optimal.

        ``by_channel`` holds only our radios that sit *on* a candidate channel, so
        the load vector is the assignment the fix path can actually re-plan.
        """
        if not candidates:
            return None
        loads = {ch: len(by_channel.get(ch, ())) for ch in candidates}
        if max(loads.values()) - min(loads.values()) < 2:
            return None  # already the best spread available: not a defect

        groups = [(ch, by_channel[ch]) for ch in sorted(by_channel) if len(by_channel[ch]) >= 2]
        conflicted = [radio for _, members in groups for radio in members]
        congested = sorted(
            radio.native_id
            for radio in conflicted
            if _radio_congested(ctx, radio, congest_window, congest_cu)
        )
        confounders = ["own_radio_config_read", "unavoidable_reuse_excluded"]
        if congested:
            confounders.append("conflicted_radio_congestion_checked")
        return Finding(
            detector_key=self.key,
            entity=_rf_entity(ctx.site_id, band),
            severity=Severity.P2 if congested else Severity.P3,
            title=(
                f"Avoidable co-channel reuse on {band} GHz: "
                f"{_count(len(conflicted), 'radio')} on {_count(len(groups), 'channel')}"
            ),
            dims={"subtype": "co_channel_reuse", "band": band},
            evidence={
                "subtype": "co_channel_reuse",
                "band": band,
                "conflict_groups": [
                    {
                        "channel": ch,
                        "radios": [self._radio_detail(r, ap_names) for r in members],
                    }
                    for ch, members in groups
                ],
                "candidate_channels": list(candidates),
                "per_channel": {str(ch): loads[ch] for ch in candidates},
                "unused_candidates": [ch for ch in candidates if loads[ch] == 0],
                "congested_radios": congested,
                "materially_congested": bool(congested),
            },
            confounders_checked=confounders,
        )

    def _width_finding(
        self,
        ctx: Any,
        radios: list[Entity],
        ap_names: dict[Any, str],
        ap_count: int,
        wide_ap_min: int,
        congest_window: int,
        congest_cu: float,
    ) -> Finding:
        """One ``wide_channel_dense_5ghz`` finding: a band-wide width policy call."""
        ordered = sorted(radios, key=lambda r: r.native_id)
        congested = sorted(
            radio.native_id
            for radio in ordered
            if _radio_congested(ctx, radio, congest_window, congest_cu)
        )
        confounders = ["own_radio_config_read", "single_ap_site_checked"]
        if congested:
            confounders.append("wide_radio_congestion_checked")
        return Finding(
            detector_key=self.key,
            entity=_rf_entity(ctx.site_id, "5"),
            severity=Severity.P2 if congested else Severity.P3,
            title=(
                f"80 MHz 5 GHz width on {_count(len(ordered), 'radio')} "
                f"across {_count(ap_count, 'AP')}"
            ),
            dims={"subtype": "wide_channel_dense_5ghz", "band": "5"},
            evidence={
                "subtype": "wide_channel_dense_5ghz",
                "band": "5",
                "ap_count": ap_count,
                "wide_channel_ap_min": wide_ap_min,
                "radios": [self._radio_detail(r, ap_names) for r in ordered],
                "congested_radios": congested,
                "materially_congested": bool(congested),
            },
            confounders_checked=confounders,
        )

    @staticmethod
    def _radio_detail(radio: Entity, ap_names: dict[Any, str]) -> dict[str, Any]:
        return {
            "native_id": radio.native_id,
            "name": radio.name or radio.native_id,
            "ap_name": ap_names.get(radio.parent_id),
            "ht_mhz": _as_int(radio.meta.get("ht")),
        }


# ====================================================================== #
# wifi.dfs_recurring
# ====================================================================== #
class DfsRecurringDetector:
    """``wifi.dfs_recurring`` — an AP that keeps eating radar hits.

    Counts ``EVT_AP_RadarDetected`` per AP over a multi-day lookback. Averaging
    >=1/day means the DFS channel is unusable here; recommend a non-DFS channel.
    When the hits cluster in the same hour of day (a predictable local radar), the
    case is stronger and escalates to P2. A single stray radar event never fires.
    """

    key = KEY_DFS_RECURRING
    scope = EntityType.AP
    cadence = Cadence.DAILY

    def evaluate(self, ctx: Any) -> EvalResult:
        if not _coverage_ok(ctx, self.key, job="fast_device"):
            return UNKNOWN

        lookback_days = int(ctx.threshold(self.key, "lookback_days", 7))
        per_day_min = float(ctx.threshold(self.key, "events_per_day_min", 1.0))
        same_hour_frac = float(ctx.threshold(self.key, "same_hour_fraction", 0.6))
        window_s = lookback_days * 86_400
        start = ctx.now_ts - window_s

        findings: list[Finding] = []
        for ap in ctx.entities(EntityType.AP):
            if ap.entity_id is None:
                continue
            events = ctx.events(entity_id=ap.entity_id, keys={_RADAR_EVENT_KEY}, since_ts=start)
            if not events:
                continue
            count = len(events)
            per_day = count / lookback_days
            if per_day < per_day_min:
                continue

            hours = [(int(e["ts"]) % 86_400) // 3600 for e in events]
            top_hour, top_frac = self._dominant_hour(hours)
            same_hour = top_frac >= same_hour_frac and count >= 3
            severity = Severity.P2 if same_hour else Severity.P3

            confounders = ["recurrence_over_days"]
            if same_hour:
                confounders.append("same_hour_clustering")
            label = ap.name or ap.native_id
            findings.append(
                Finding(
                    detector_key=self.key,
                    entity=ap,
                    severity=severity,
                    title=f"Recurring DFS radar on {label}",
                    dims={},
                    evidence={
                        "radar_events": count,
                        "lookback_days": lookback_days,
                        "events_per_day": round(per_day, 2),
                        "dominant_hour_utc": top_hour if same_hour else None,
                        "dominant_hour_fraction": round(top_frac, 2),
                    },
                    confounders_checked=confounders,
                )
            )
        return findings

    @staticmethod
    def _dominant_hour(hours: list[int]) -> tuple[Optional[int], float]:
        if not hours:
            return None, 0.0
        counts: dict[int, int] = {}
        for h in hours:
            counts[h] = counts.get(h, 0) + 1
        top = max(counts, key=lambda h: counts[h])
        return top, counts[top] / len(hours)


# ====================================================================== #
# wifi.airtime_saturation
# ====================================================================== #
class AirtimeSaturationDetector:
    """``wifi.airtime_saturation`` — a radio's channel is full.

    Sustained ``cu_total`` above 50% is degrading (P2); above 80% is critical
    (P1). The fix path hinges on the self/non-self split: airtime we generate
    (``cu_self_rx + cu_self_tx``) points at load we can shed or steer, while
    non-self utilization points at neighbors/interference on the channel and a
    channel change. Both go in the evidence so the planner can branch.
    """

    key = KEY_AIRTIME_SATURATION
    scope = EntityType.RADIO
    cadence = Cadence.WINDOW

    def evaluate(self, ctx: Any) -> EvalResult:
        if not _coverage_ok(ctx, self.key, job="fast_device"):
            return UNKNOWN

        window_s = int(ctx.threshold(self.key, "window_s", 900))
        degraded = float(ctx.threshold(self.key, "degraded_pct", 50))
        critical = float(ctx.threshold(self.key, "critical_pct", 80))
        sustained_frac = float(ctx.threshold(self.key, "sustained_fraction", 0.8))
        min_samples = int(ctx.threshold(self.key, "min_samples", 4))

        findings: list[Finding] = []
        unknown: set[int] = set()
        for radio in ctx.entities(EntityType.RADIO):
            if radio.entity_id is None:
                continue
            cu = _values(ctx.window(radio.entity_id, "cu_total", window_s))
            if len(cu) < min_samples:
                # Too few cu_total samples this window to judge saturation ->
                # freeze this radio's issue rather than clear it by absence.
                unknown.add(radio.entity_id)
                continue

            if _fraction_atleast(cu, critical) >= sustained_frac:
                severity, level = Severity.P1, "critical"
            elif _fraction_atleast(cu, degraded) >= sustained_frac:
                severity, level = Severity.P2, "degraded"
            else:
                continue

            self_rx = _median(_values(ctx.window(radio.entity_id, "cu_self_rx", window_s))) or 0.0
            self_tx = _median(_values(ctx.window(radio.entity_id, "cu_self_tx", window_s))) or 0.0
            cu_self = self_rx + self_tx
            cu_med = _median(cu) or 0.0
            cu_nonself = max(0.0, cu_med - cu_self)
            dominant = "self" if cu_self >= cu_nonself else "non_self"

            label = radio.name or radio.native_id
            findings.append(
                Finding(
                    detector_key=self.key,
                    entity=radio,
                    severity=severity,
                    title=f"Airtime saturation ({level}) on {label}",
                    dims={"band": _band_of(radio) or "?"},
                    evidence={
                        "cu_total_median": round(cu_med, 1),
                        "cu_self": round(cu_self, 1),
                        "cu_non_self": round(cu_nonself, 1),
                        "dominant_source": dominant,
                        "level": level,
                    },
                    confounders_checked=[
                        "sustained_not_burst",
                        "self_vs_non_self_split",
                    ],
                )
            )
        return DetectorResult.of(findings, unknown)


# ====================================================================== #
# wifi.tx_power_loud
# ====================================================================== #
class TxPowerLoudDetector:
    """``wifi.tx_power_loud`` — a multi-AP site shouting at max power.

    On a site with 2+ APs, radios at High/auto-max power inflate cell overlap and
    breed sticky clients; that is a P3 by itself, escalating to P2 when sticky
    clients concentrate on the loud AP. Separately, a 2.4 GHz radio not held
    ~6 dB below its 5 GHz sibling wastes range asymmetry (2.4 already travels
    farther), which is flagged as a power-imbalance sub-case.
    """

    key = KEY_TX_POWER_LOUD
    scope = EntityType.RADIO
    cadence = Cadence.DAILY

    def evaluate(self, ctx: Any) -> EvalResult:
        if not _coverage_ok(ctx, self.key, job="fast_device"):
            return UNKNOWN

        loud_modes = {str(m).lower() for m in ctx.threshold(self.key, "loud_modes", ("high",))}
        imbalance_db = float(ctx.threshold(self.key, "band_imbalance_db", 6))
        sticky_cluster_min = int(ctx.threshold(self.key, "sticky_cluster_min", 3))

        radios = [r for r in ctx.entities(EntityType.RADIO) if r.entity_id is not None]
        by_ap = _radios_by_ap(radios)
        ap_count = len([a for a in ctx.entities(EntityType.AP) if a.entity_id is not None])
        if ap_count < 2:
            return []  # single-AP: loud power is not a cell-overlap problem

        sticky_by_ap = self._sticky_counts(ctx)

        findings: list[Finding] = []
        for radio in radios:
            mode = str(radio.meta.get("tx_power_mode") or "").lower()
            if mode in loud_modes:
                sticky = sticky_by_ap.get(radio.parent_id, 0)
                clustered = sticky >= sticky_cluster_min
                confounders = ["multi_ap_site"]
                if radio.parent_id is not None:
                    confounders.append("sticky_concentration_checked")
                label = radio.name or radio.native_id
                findings.append(
                    Finding(
                        detector_key=self.key,
                        entity=radio,
                        severity=Severity.P2 if clustered else Severity.P3,
                        title=f"Loud tx-power on {label}",
                        dims={"subtype": "loud_power", "band": _band_of(radio) or "?"},
                        evidence={
                            "tx_power_mode": mode,
                            "ap_count": ap_count,
                            "sticky_clients_on_ap": sticky,
                            "clustered": clustered,
                        },
                        confounders_checked=confounders,
                    )
                )

        findings.extend(self._imbalance_findings(by_ap, imbalance_db))
        return findings

    @staticmethod
    def _sticky_counts(ctx: Any) -> dict[int, int]:
        """Wireless clients per AP whose recent RSSI is sustained weak.

        A self-contained proxy for sticky-client concentration (the loud-power
        corroborator) computed from live client RSSI, not the issues table — dims
        are folded into the fingerprint and not a queryable column, so blame
        cannot be read back from an issue row.
        """
        window_s = int(ctx.threshold(KEY_TX_POWER_LOUD, "sticky_window_s", 600))
        weak_dbm = float(ctx.threshold(KEY_TX_POWER_LOUD, "sticky_rssi_dbm", -72))
        sustained_frac = float(ctx.threshold(KEY_TX_POWER_LOUD, "sticky_fraction", 0.8))
        min_samples = int(ctx.threshold(KEY_TX_POWER_LOUD, "sticky_min_samples", 4))
        counts: dict[int, int] = {}
        for client in ctx.entities(EntityType.CLIENT):
            if client.entity_id is None or client.parent_id is None:
                continue
            if not _is_wireless_client(client):
                continue
            rssi = _values(ctx.window(client.entity_id, "rssi", window_s))
            if len(rssi) < min_samples:
                continue
            if _fraction_below(rssi, weak_dbm) >= sustained_frac:
                counts[client.parent_id] = counts.get(client.parent_id, 0) + 1
        return counts

    def _imbalance_findings(
        self, by_ap: dict[int, list[Entity]], imbalance_db: float
    ) -> list[Finding]:
        out: list[Finding] = []
        for radios in by_ap.values():
            r24 = next((r for r in radios if _band_of(r) == "2.4"), None)
            r5 = next((r for r in radios if _band_of(r) == "5"), None)
            if r24 is None or r5 is None:
                continue
            p24 = _as_int(r24.meta.get("tx_power"))
            p5 = _as_int(r5.meta.get("tx_power"))
            if p24 is None or p5 is None:
                continue
            # 2.4 should sit ~imbalance_db below 5 GHz; flag when it does not.
            if p24 > p5 - imbalance_db:
                label = r24.name or r24.native_id
                out.append(
                    Finding(
                        detector_key=self.key,
                        entity=r24,
                        severity=Severity.P3,
                        title=f"2.4/5 GHz power imbalance on {label}",
                        dims={"subtype": "band_imbalance", "band": "2.4"},
                        evidence={
                            "tx_power_24_dbm": p24,
                            "tx_power_5_dbm": p5,
                            "expected_gap_db": imbalance_db,
                        },
                        confounders_checked=["both_band_power_read"],
                    )
                )
        return out


# ====================================================================== #
# wifi.legacy_rates
# ====================================================================== #
class LegacyRatesDetector:
    """``wifi.legacy_rates`` — 802.11b-rate clients dragging the cell.

    A client whose data rate stays within the 802.11b set (1/2/5.5/11 Mbps) across
    the window occupies the medium far longer per frame than a modern client and
    slows everyone on that radio. Requires the low rate to be sustained (a single
    low sample during a lull is not an 11b client). Wired clients are excluded.
    """

    key = KEY_LEGACY_RATES
    scope = EntityType.CLIENT
    cadence = Cadence.DAILY

    def evaluate(self, ctx: Any) -> EvalResult:
        if not _coverage_ok(ctx, self.key, job="fast_sta"):
            return UNKNOWN

        window_s = int(ctx.threshold(self.key, "window_s", 3600))
        max_b_rate = float(ctx.threshold(self.key, "max_b_rate_mbps", 11))
        sustained_frac = float(ctx.threshold(self.key, "sustained_fraction", 0.8))
        min_samples = int(ctx.threshold(self.key, "min_samples", 4))

        findings: list[Finding] = []
        unknown: set[int] = set()
        for client in ctx.entities(EntityType.CLIENT):
            if client.entity_id is None or not _is_wireless_client(client):
                continue
            rates = [r for r in _values(ctx.window(client.entity_id, "tx_rate", window_s)) if r > 0]
            if len(rates) < min_samples:
                # Too few positive tx_rate samples to judge the client's rate tier
                # -> freeze its issue, do not clear it by absence.
                unknown.add(client.entity_id)
                continue
            if sum(1 for r in rates if r <= max_b_rate) / len(rates) < sustained_frac:
                continue

            med = _median(rates)
            is_b = med is not None and med in _B_RATES
            label = client.name or client.native_id
            findings.append(
                Finding(
                    detector_key=self.key,
                    entity=client,
                    severity=Severity.P3,
                    title=f"Legacy-rate client {label}",
                    dims={"ssid": str(client.meta.get("essid") or "")},
                    evidence={
                        "median_tx_rate_mbps": med,
                        "max_b_rate_mbps": max_b_rate,
                        "matches_11b_rate": is_b,
                    },
                    confounders_checked=[
                        "rate_sustained_not_momentary",
                        "wired_client_excluded",
                    ],
                )
            )
        return DetectorResult.of(findings, unknown)


# ====================================================================== #
# wifi.band_steering
# ====================================================================== #
class BandSteeringDetector:
    """``wifi.band_steering`` — a dual-band client on the wrong band.

    Two shapes. A client parked on 2.4 GHz at strong RSSI while an idle 5 GHz
    radio waits on the *same* AP should be steered up — but only when the client
    is provably dual-band (its own history shows a prior 5 GHz attachment), so a
    single-band device is never nagged. The inverse: a client pinned to 5 GHz at
    <= -80 dBm is being held on a band it can barely hear and should fall back to
    2.4 for range.
    """

    key = KEY_BAND_STEERING
    scope = EntityType.CLIENT
    cadence = Cadence.WINDOW

    def evaluate(self, ctx: Any) -> EvalResult:
        if not _coverage_ok(ctx, self.key, job="fast_sta"):
            return UNKNOWN

        window_s = int(ctx.threshold(self.key, "window_s", 900))
        strong_24 = float(ctx.threshold(self.key, "strong_24_rssi_dbm", -65))
        weak_5 = float(ctx.threshold(self.key, "weak_5_rssi_dbm", -80))
        idle_cu = float(ctx.threshold(self.key, "idle_5ghz_cu_pct", 40))
        sustained_frac = float(ctx.threshold(self.key, "sustained_fraction", 0.8))
        min_samples = int(ctx.threshold(self.key, "min_samples", 4))

        radios = [r for r in ctx.entities(EntityType.RADIO) if r.entity_id is not None]
        by_ap = _radios_by_ap(radios)

        findings: list[Finding] = []
        unknown: set[int] = set()
        for client in ctx.entities(EntityType.CLIENT):
            if client.entity_id is None or not _is_wireless_client(client):
                continue
            band = self._client_band(ctx, client)
            rssi = _values(ctx.window(client.entity_id, "rssi", window_s))
            if len(rssi) < min_samples or band is None:
                # Too few RSSI samples, or the current band is unreadable this
                # cycle -> cannot judge steering; freeze the issue, don't clear it.
                unknown.add(client.entity_id)
                continue
            med = _median(rssi) or -127.0
            ap_mac = ctx.repo.current_state(client.entity_id, "ap_mac")

            finding = None
            if band == "2.4" and _fraction_atleast(rssi, strong_24) >= sustained_frac:
                finding = self._steer_up(ctx, client, ap_mac, med, by_ap, idle_cu, window_s)
            elif band == "5" and _fraction_below(rssi, weak_5) >= sustained_frac:
                finding = self._steer_down(client, ap_mac, med, weak_5)
            if finding is not None:
                findings.append(finding)
        return DetectorResult.of(findings, unknown)

    def _client_band(self, ctx: Any, client: Entity) -> Optional[str]:
        raw = ctx.repo.current_state(client.entity_id, "band")
        if raw is None:
            return None
        code = str(raw).lower()
        if code in _BAND_24:
            return "2.4"
        if code in _BAND_5:
            return "5"
        return None

    def _dual_band_confirmed(self, ctx: Any, client: Entity) -> bool:
        """Proof the client is dual-band: a prior 5 GHz attachment in its history."""
        history = ctx.repo.state_history(client.entity_id, "band", limit=50)
        for row in history:
            code = str(row["new_value"] or "").lower()
            if code in _BAND_5:
                return True
        return False

    def _steer_up(
        self,
        ctx: Any,
        client: Entity,
        ap_mac: Optional[str],
        med: float,
        by_ap: dict[int, list[Entity]],
        idle_cu: float,
        window_s: int,
    ) -> Optional[Finding]:
        if not self._dual_band_confirmed(ctx, client):
            return None  # cannot prove dual-band -> do not nag a single-band device
        radio5 = self._idle_5ghz_on_ap(ctx, client, by_ap, idle_cu, window_s)
        if radio5 is None:
            return None
        label = client.name or client.native_id
        return Finding(
            detector_key=self.key,
            entity=client,
            severity=Severity.P3,
            title=f"Band-steer {label} up to 5 GHz",
            dims={"subtype": "parked_on_24", "ap": str(ap_mac) if ap_mac else ""},
            evidence={
                "band": "2.4",
                "median_rssi": med,
                "idle_5ghz_radio": radio5.native_id,
            },
            confounders_checked=[
                "dual_band_confirmed",
                "five_ghz_idle_on_same_ap",
                "strong_rssi_sustained",
            ],
        )

    def _steer_down(
        self, client: Entity, ap_mac: Optional[str], med: float, weak_5: float
    ) -> Finding:
        label = client.name or client.native_id
        return Finding(
            detector_key=self.key,
            entity=client,
            severity=Severity.P3,
            title=f"Band-steer {label} down to 2.4 GHz",
            dims={"subtype": "held_on_5", "ap": str(ap_mac) if ap_mac else ""},
            evidence={"band": "5", "median_rssi": med, "weak_5_rssi_dbm": weak_5},
            confounders_checked=["on_5ghz_confirmed", "weak_rssi_sustained"],
        )

    def _idle_5ghz_on_ap(
        self,
        ctx: Any,
        client: Entity,
        by_ap: dict[int, list[Entity]],
        idle_cu: float,
        window_s: int,
    ) -> Optional[Entity]:
        parent_id = client.parent_id
        if parent_id is None:
            return None
        for radio in by_ap.get(parent_id, ()):  # type: ignore[arg-type]
            if _band_of(radio) != "5":
                continue
            cu = _median(_values(ctx.window(radio.entity_id, "cu_total", window_s)))
            if cu is None or cu <= idle_cu:
                return radio
        return None


# ====================================================================== #
# wifi.mesh_uplink
# ====================================================================== #
class MeshUplinkDetector:
    """``wifi.mesh_uplink`` — a wireless-uplink AP on a marginal backhaul.

    A meshed AP whose uplink RSSI is sustained worse than -70 dBm (or -65 with
    corroboration) is on a backhaul that will drop under load; deep hop counts
    (>=3) and repeated uplink reconnect cycles corroborate it, opening a P2.
    Separately, an AP currently on a *wired* uplink but with meshing still enabled
    carries a latent failover risk and is flagged low (P3).
    """

    key = KEY_MESH_UPLINK
    scope = EntityType.AP
    cadence = Cadence.WINDOW

    def evaluate(self, ctx: Any) -> EvalResult:
        if not _coverage_ok(ctx, self.key, job="fast_device"):
            return UNKNOWN

        window_s = int(ctx.threshold(self.key, "window_s", 900))
        warn_rssi = float(ctx.threshold(self.key, "warn_rssi_dbm", -65))
        bad_rssi = float(ctx.threshold(self.key, "bad_rssi_dbm", -70))
        deep_hops = int(ctx.threshold(self.key, "deep_hops", 3))
        sustained_frac = float(ctx.threshold(self.key, "sustained_fraction", 0.8))
        min_samples = int(ctx.threshold(self.key, "min_samples", 4))
        reconnect_min = int(ctx.threshold(self.key, "reconnect_min", 2))

        start = ctx.now_ts - window_s
        findings: list[Finding] = []
        for ap in ctx.entities(EntityType.AP):
            if ap.entity_id is None:
                continue
            uplink_type = str(ctx.repo.current_state(ap.entity_id, "uplink_type") or "").lower()
            wireless = uplink_type == "wireless"

            if not wireless:
                # Latent-risk config case: wired now, meshing still enabled.
                if _as_bool(ap.meta.get("mesh_enabled")):
                    findings.append(self._latent_finding(ap, uplink_type))
                continue

            rssi = _values(ctx.window(ap.entity_id, "uplink_rssi", window_s))
            if len(rssi) < min_samples:
                continue
            med = _median(rssi) or -127.0
            hops = _as_int(ctx.repo.current_state(ap.entity_id, "uplink_hops")) or _as_int(
                ap.meta.get("uplink_hops")
            )
            reconnects = len(
                [
                    e
                    for e in ctx.events(entity_id=ap.entity_id, since_ts=start)
                    if str(e["key"] or "").endswith(_LOST_CONTACT_SUFFIX)
                ]
            )
            corroborated = (hops is not None and hops >= deep_hops) or reconnects >= reconnect_min

            if _fraction_below(rssi, bad_rssi) >= sustained_frac:
                severity = Severity.P2
            elif _fraction_below(rssi, warn_rssi) >= sustained_frac and corroborated:
                severity = Severity.P2
            elif _fraction_below(rssi, warn_rssi) >= sustained_frac:
                severity = Severity.P3
            else:
                continue

            confounders = ["sustained_poor_rssi"]
            if hops is not None:
                confounders.append("hop_depth_checked")
            confounders.append("reconnect_corroboration_checked")
            label = ap.name or ap.native_id
            findings.append(
                Finding(
                    detector_key=self.key,
                    entity=ap,
                    severity=severity,
                    title=f"Weak mesh uplink on {label}",
                    dims={"subtype": "wireless_uplink"},
                    evidence={
                        "median_uplink_rssi": med,
                        "warn_rssi_dbm": warn_rssi,
                        "bad_rssi_dbm": bad_rssi,
                        "hops": hops,
                        "reconnect_cycles": reconnects,
                        "corroborated": corroborated,
                    },
                    confounders_checked=confounders,
                )
            )
        return findings

    def _latent_finding(self, ap: Entity, uplink_type: str) -> Finding:
        label = ap.name or ap.native_id
        return Finding(
            detector_key=self.key,
            entity=ap,
            severity=Severity.P3,
            title=f"Meshing enabled on wired AP {label}",
            dims={"subtype": "wired_with_mesh_enabled"},
            evidence={"uplink_type": uplink_type, "mesh_enabled": True},
            confounders_checked=["uplink_type_read"],
        )


# ====================================================================== #
# Shared neighbour-scan helpers (wifi.neighbor_density + wifi.rogue_ap)
# ====================================================================== #
def _neighbor_rows(ctx: Any) -> list[dict[str, Any]]:
    """Decode the ``rogue_bss`` inventory rows into judged neighbour dicts.

    Read straight off the repository (``list_entities``) rather than through
    ``ctx.entities``: a neighbour BSS is not a managed ``EntityType`` and would
    raise on decode. ``meta`` (channel/rssi/band/security written by the rogueap
    poll) is parsed here; the normalized band folds the reported code with a
    channel-number fallback. Both neighbour detectors read the inventory through
    this one decoder so they never disagree about what the scan said.
    """
    rows = ctx.repo.list_entities(ROGUE_BSS_TYPE, site_id=ctx.site_id)
    out: list[dict[str, Any]] = []
    for row in rows:
        meta = _parse_meta(row["meta"])
        channel = _as_int(meta.get("channel"))
        entity = Entity(
            entity_type=ROGUE_BSS_TYPE,  # type: ignore[arg-type]
            native_id=row["native_id"],
            site_id=ctx.site_id,
            entity_id=row["entity_id"],
            name=row["name"],
            first_seen_ts=row["first_seen_ts"],
            last_seen_ts=row["last_seen_ts"],
            meta=meta,
        )
        channels = meta.get("channels")
        out.append(
            {
                "entity": entity,
                "bssid": row["native_id"],
                "essid": row["name"],
                "channel": channel,
                "channels": [c for c in channels if isinstance(c, int)]
                if isinstance(channels, list)
                else None,
                "band": _norm_rogue_band(meta.get("band"), channel),
                "rssi": _as_int(meta.get("rssi")),
                "security": meta.get("security"),
                "seen_by_ap": meta.get("seen_by_ap"),
                "is_rogue": meta.get("is_rogue"),
                "is_ubnt": meta.get("is_ubnt"),
                "scan_ts": (meta.get("scan_ts") if isinstance(meta.get("scan_ts"), list) else None),
                "first_seen": _as_int(row["first_seen_ts"]),
                "last_seen": _as_int(row["last_seen_ts"]),
            }
        )
    return out


def _own_hardware_ids(ctx: Any) -> tuple[set[str], set[str]]:
    """Our managed devices' MACs and 5-octet prefixes, for own-hardware exclusion.

    A UniFi AP broadcasts one virtual BSSID per WLAN, derived from the device MAC
    by varying only the low octet(s); the scan reports each with the controller's
    ``is_ubnt`` flag set. Cross-referencing a neighbour BSSID's vendor+device
    prefix against our managed devices auto-excludes our own hardware from the
    daily neighbour scan, so a multi-AP/mesh site no longer flags its own radios
    (the empty default allowlist could not).
    """
    prefixes: set[str] = set()
    macs: set[str] = set()
    for etype in (EntityType.AP, EntityType.SWITCH, EntityType.GATEWAY):
        for dev in ctx.entities(etype):
            mac = _norm_mac(dev.native_id)
            if mac is None:
                continue
            macs.add(mac)
            prefix = _mac_prefix(mac)
            if prefix is not None:
                prefixes.add(prefix)
    return prefixes, macs


def _is_own_hardware(
    bssid: Optional[str], is_ubnt: Any, own_prefixes: set[str], own_macs: set[str]
) -> bool:
    """True when this neighbour BSS is one of our own managed radios."""
    mac = _norm_mac(bssid)
    if mac is None:
        return False
    if mac in own_macs:
        return True
    # A virtual BSSID shares the device's vendor+device bytes; only own Ubiquiti
    # hardware is auto-excluded on a prefix match (a neighbour's own Ubiquiti gear
    # on a different device prefix is still foreign hardware).
    if _as_bool(is_ubnt):
        prefix = _mac_prefix(mac)
        return prefix is not None and prefix in own_prefixes
    return False


def _persistent(
    rg: dict[str, Any],
    span: Optional[int],
    persist_span: int,
    min_scans: int,
    recency_s: int,
    now_ts: int,
) -> bool:
    """Persistence = seen in ``min_scans`` distinct recent scans.

    Prefers the per-scan sighting log (``scan_ts``) so a BSS seen once long ago
    and again today — a large span, but absent the whole interim — does not read
    as persistent. Falls back to the first-to-last span only for legacy rows
    written before the sighting log existed.
    """
    scans = rg.get("scan_ts")
    if isinstance(scans, list):
        recent = {int(t) for t in scans if isinstance(t, (int, float)) and t >= now_ts - recency_s}
        return len(recent) >= min_scans
    return span is not None and span >= persist_span


def _our_radios(ctx: Any) -> list[tuple[str, int, Entity]]:
    """Our own radios as ``(band, channel, radio)`` tuples (both readable)."""
    out: list[tuple[str, int, Entity]] = []
    for radio in ctx.entities(EntityType.RADIO):
        if radio.entity_id is None:
            continue
        band = _band_of(radio)
        channel = _radio_channel(ctx, radio)
        if band is not None and channel is not None:
            out.append((band, channel, radio))
    return out


def _overlaps(our: tuple[str, int, Entity], band: str, channel: int, dist_24: int) -> bool:
    """Does the neighbour on ``(band, channel)`` overlap our radio ``our``?

    Same band is required. Co-channel (identical channel) overlaps on every band;
    on 2.4 GHz, where 20 MHz cells are only 5 MHz apart, channels within
    ``dist_24`` also overlap. 5/6 GHz adjacency is deliberately *not* claimed (it
    needs both parties' widths, which the scan does not give) — co-channel only,
    keeping the link conservative.
    """
    our_band, our_channel, _ = our
    if our_band != band:
        return False
    if our_channel == channel:
        return True
    if band == "2.4":
        return abs(our_channel - channel) <= dist_24
    return False


def _radio_congested(ctx: Any, radio: Entity, window_s: int, congest_cu: float) -> bool:
    """True when ``radio``'s median ``cu_total`` over the window is at/above floor."""
    if radio.entity_id is None:
        return False
    med = _median(_values(ctx.window(radio.entity_id, "cu_total", window_s)))
    return med is not None and med >= congest_cu


def _our_ssids(ctx: Any, fresh_s: int) -> tuple[dict[str, dict[str, Any]], Optional[str]]:
    """Our own SSIDs, casefolded, with the source that produced them.

    Preferred source is the ``rest/wlanconf`` read the inventory job performs
    (WLAN entities, carrying the configured security mode). A WLAN entity not
    refreshed inside ``fresh_s`` is treated as deleted from the controller and
    dropped, so a retired SSID cannot make a neighbour who later reuses that name
    look like an evil twin. When the controller serves no WLAN config — an older
    console, or one that refuses the route — the set falls back to the distinct
    ``essid`` values our *own associated wireless clients* report, which is weaker
    (a client parked on a neighbour's SSID would poison it) but is still our
    network's own view.

    Returns ``({}, None)`` when neither source yields an SSID. The caller must
    treat that as UNKNOWN for the spoof subtype: guessing our SSID set would turn
    every neighbour into a candidate evil twin.
    """
    ssids: dict[str, dict[str, Any]] = {}
    for wlan in ctx.entities(EntityType.WLAN):
        name = (wlan.name or "").strip()
        if not name:
            continue
        if not _as_bool(wlan.meta.get("enabled", True)):
            continue  # a disabled WLAN is not on the air; we cannot be twinned on it
        last_seen = _as_int(wlan.last_seen_ts)
        if last_seen is None or last_seen < ctx.now_ts - fresh_s:
            continue  # no longer in the controller's WLAN config
        ssids[name.casefold()] = {"name": name, "security": wlan.meta.get("security")}
    if ssids:
        return ssids, "wlanconf"

    for client in ctx.entities(EntityType.CLIENT):
        if not _is_wireless_client(client):
            continue
        name = str(client.meta.get("essid") or "").strip()
        if name:
            ssids.setdefault(name.casefold(), {"name": name, "security": None})
    if ssids:
        return ssids, "client_essid"
    return {}, None


def _wired_client_prefixes(ctx: Any) -> dict[str, list[str]]:
    """Vendor+device MAC prefixes of our *wired* clients, prefix -> MACs.

    The on-wire corroboration for a controller-flagged rogue: a BSSID whose
    vendor+device prefix matches a wired client's MAC is very likely the same
    physical box, i.e. an AP plugged into our LAN rather than a neighbour's
    network overheard through a wall. Severity-lifting evidence only — never the
    trigger (ARCHITECTURE.md 17 conservatism).
    """
    out: dict[str, list[str]] = {}
    for client in ctx.entities(EntityType.CLIENT):
        if _is_wireless_client(client):
            continue
        mac = _norm_mac(client.native_id)
        prefix = _mac_prefix(mac)
        if mac is not None and prefix is not None:
            out.setdefault(prefix, []).append(mac)
    return out


# ====================================================================== #
# wifi.neighbor_density
# ====================================================================== #
class NeighborDensityDetector:
    """``wifi.neighbor_density`` — how crowded our channels are, one issue per band.

    The daily ``stat/rogueap`` scan sees every BSS within earshot. Reporting each
    one is not a finding, it is a flood: a dense suburb yields well over a hundred
    neighbour BSSes, and a neighbour that changes channel would churn a fresh
    issue every time. This detector aggregates the scan into the decision an admin
    can actually act on — *this band's air is crowded* — as **one site-scoped issue
    per band**, keyed on an ``rf_env`` pseudo-entity (``rf:2.4`` / ``rf:5`` /
    ``rf:6``). The channel plan is a band-level decision; the per-AP and per-BSSID
    breakdown is evidence, not separate issues.

    A neighbour BSS counts toward the density when it is, all at once: recent
    (seen inside ``recency_window_s``), persistent (present in at least
    ``persist_min_scans`` distinct recent scans), strong enough to matter (RSSI
    above ``rssi_floor_dbm``), and overlapping one of our radios (co-channel on
    any band, plus adjacent-channel overlap within ``overlap_24_distance`` on
    2.4 GHz). Own hardware is excluded two ways, exactly as before: the
    config-driven known-BSSID allowlist and the automatic own-Ubiquiti-prefix
    cross-reference.

    The band fires at ``density_min_count`` qualifying neighbours (default 3).
    Below that the band is simply not crowded and nothing is emitted, so an open
    issue drains through the normal clear streak. P3 by default: a crowded band is
    context. P2 only when at least one overlapped radio is *materially congested*
    (median ``cu_total`` at/above ``congested_cu_pct``), which is the case that
    ties into ``wifi.airtime_saturation`` through correlation. Density alone is
    never P2. UNKNOWN when device coverage is below floor or the neighbour table
    is empty — a fresh, unscanned store is not a clean "quiet neighbourhood".
    """

    key = KEY_NEIGHBOR_DENSITY
    scope = EntityType.RADIO
    cadence = Cadence.DAILY

    def evaluate(self, ctx: Any) -> EvalResult:
        if not _coverage_ok(ctx, self.key, job="fast_device"):
            return UNKNOWN

        rssi_floor = float(ctx.threshold(self.key, "rssi_floor_dbm", -75))
        dist_24 = int(ctx.threshold(self.key, "overlap_24_distance", 4))
        persist_span = int(ctx.threshold(self.key, "persist_min_span_s", 82_800))
        persist_min_scans = int(ctx.threshold(self.key, "persist_min_scans", 2))
        recency_s = int(ctx.threshold(self.key, "recency_window_s", 2 * 86_400))
        congest_window = int(ctx.threshold(self.key, "congested_window_s", 900))
        congest_cu = float(ctx.threshold(self.key, "congested_cu_pct", 50))
        min_count = int(ctx.threshold(self.key, "density_min_count", 3))
        top_n = int(ctx.threshold(self.key, "top_offenders_max", 10))
        allowlist = _bssid_set(ctx.threshold(self.key, "known_bssids", ()))

        neighbors = _neighbor_rows(ctx)
        if not neighbors:
            # No neighbour rows at all: cannot tell a fresh/unscanned store from a
            # genuinely empty RF neighbourhood -> freeze, never a clean clear.
            return UNKNOWN

        our_radios = _our_radios(ctx)
        own_prefixes, own_macs = _own_hardware_ids(ctx)

        seen: dict[str, int] = {}
        qualifying: dict[str, list[dict[str, Any]]] = {}
        overlapped: dict[str, dict[str, Entity]] = {}
        for rg in neighbors:
            last_seen = rg["last_seen"]
            if last_seen is None or last_seen < ctx.now_ts - recency_s:
                continue  # stale sighting: the neighbour is gone
            band = rg["band"]
            if band is None:
                continue  # cannot place it on the plan -> cannot count it
            seen[band] = seen.get(band, 0) + 1

            bssid = rg["bssid"]
            if bssid and bssid.lower() in allowlist:
                continue
            if _is_own_hardware(bssid, rg["is_ubnt"], own_prefixes, own_macs):
                continue
            first_seen = rg["first_seen"]
            span = None if first_seen is None else last_seen - first_seen
            if not _persistent(rg, span, persist_span, persist_min_scans, recency_s, ctx.now_ts):
                continue  # transient / long-absent
            channel, rssi = rg["channel"], rg["rssi"]
            if channel is None or rssi is None or rssi <= rssi_floor:
                continue  # unplaceable, or a weak/distant neighbour
            hits = [r for r in our_radios if _overlaps(r, band, channel, dist_24)]
            if not hits:
                continue  # not on any of our channels -> not our air

            qualifying.setdefault(band, []).append(rg)
            for _, _, radio in hits:
                overlapped.setdefault(band, {})[radio.native_id] = radio

        findings: list[Finding] = []
        for band in sorted(qualifying):
            rows = qualifying[band]
            if len(rows) < min_count:
                continue
            radios = overlapped.get(band, {})
            congested = sorted(
                nid
                for nid, radio in radios.items()
                if _radio_congested(ctx, radio, congest_window, congest_cu)
            )
            findings.append(
                self._finding(
                    ctx.site_id, band, rows, seen.get(band, len(rows)), radios, congested, top_n
                )
            )
        return findings

    # ------------------------------------------------------------------ #
    def _finding(
        self,
        site_id: str,
        band: str,
        rows: list[dict[str, Any]],
        total_seen: int,
        radios: dict[str, Entity],
        congested: list[str],
        top_n: int,
    ) -> Finding:
        per_channel: dict[str, int] = {}
        for rg in rows:
            key = str(rg["channel"])
            per_channel[key] = per_channel.get(key, 0) + 1
        offenders = sorted(rows, key=lambda r: (-(r["rssi"] or -127), str(r["bssid"])))[:top_n]
        confounders = [
            "known_bssid_allowlist_checked",
            "own_ubnt_hardware_excluded",
            "transient_single_scan_excluded",
            "persistence_over_distinct_recent_scans",
            "weak_neighbor_excluded",
            "own_radio_channel_overlap",
            "density_floor_applied",
        ]
        if congested:
            confounders.append("overlapped_radio_congestion_checked")
        entity = _rf_entity(site_id, band)
        return Finding(
            detector_key=self.key,
            entity=entity,
            severity=Severity.P2 if congested else Severity.P3,
            title=f"{len(rows)} neighbouring networks share our {band} GHz channels",
            dims={"band": band},
            evidence={
                "band": band,
                "qualifying_count": len(rows),
                "total_seen": total_seen,
                "per_channel": dict(sorted(per_channel.items(), key=lambda kv: int(kv[0]))),
                "top_offenders": [
                    {
                        "bssid": rg["bssid"],
                        "essid": rg["essid"],
                        "channel": rg["channel"],
                        "rssi_dbm": rg["rssi"],
                        "seen_by_ap": rg["seen_by_ap"],
                        "scan_count": (
                            len(rg["scan_ts"]) if isinstance(rg["scan_ts"], list) else None
                        ),
                    }
                    for rg in offenders
                ],
                "overlapping_radios": sorted(radios),
                "congested_overlap_radios": congested,
                "materially_congested": bool(congested),
            },
            confounders_checked=confounders,
        )


# ====================================================================== #
# wifi.rogue_ap
# ====================================================================== #
class RogueApDetector:
    """``wifi.rogue_ap`` — a neighbour BSS that is a *security* problem, not noise.

    Crowded air is ``wifi.neighbor_density``'s job. This detector fires only on
    the two claims the ``stat/rogueap`` data can honestly support, one issue per
    BSSID per subtype (channel is deliberately **out** of the fingerprint, so a
    channel-hopping twin stays one issue and the observed channels ride along in
    evidence):

    ``ssid_spoof`` (P1)
        A neighbour BSS broadcasting one of *our own* SSIDs while failing the
        own-hardware checks: an evil twin, or at best a misconfigured box
        impersonating our network. Our SSID set comes from the ``rest/wlanconf``
        read (WLAN entities), falling back to the distinct ESSIDs our associated
        wireless clients report. With **no** SSID set the subtype is UNKNOWN and
        never guessed. This fires on first sighting — an evil twin that has to
        wait three daily cycles to be confirmed is a detector that missed it — and
        an open twin of a secured SSID of ours is recorded as corroboration.

    ``controller_flagged`` (P2, P1 corroborated)
        The controller's own ``is_rogue`` attestation, which we stored and
        ignored until now. It is surfaced plainly as the controller's verdict, not
        as our measurement. It lifts to P1 when the BSSID's vendor+device MAC
        prefix matches a *wired* client on our LAN — the on-wire signal that this
        AP is plugged into our network. That prefix match is severity-lifting
        evidence only, never the trigger.

    Both subtypes exclude own hardware (known-BSSID allowlist plus the automatic
    own-Ubiquiti-prefix cross-reference) and stale sightings (a BSS not seen
    inside ``recency_window_s`` is gone; let the issue clear). A BSSID that spoofs
    our SSID reports only the P1 spoof finding, with the controller's flag inside
    its evidence, rather than two issues about one box.

    What this cannot do: ``stat/rogueap`` alone cannot see an on-wire rogue that
    the controller does not flag and whose wired MAC is unrelated to its BSSID.
    That miss is stated in the playbook rather than papered over.

    UNKNOWN when device coverage is below floor, or when the neighbour table is
    empty (a fresh/unscanned store is not a clean "no rogues"). When the SSID set
    cannot be resolved, the examined BSSIDs are returned as per-entity UNKNOWNs so
    an open spoof issue freezes instead of falsely resolving.
    """

    key = KEY_ROGUE_AP
    scope = EntityType.RADIO
    cadence = Cadence.DAILY

    def evaluate(self, ctx: Any) -> EvalResult:
        if not _coverage_ok(ctx, self.key, job="fast_device"):
            return UNKNOWN

        recency_s = int(ctx.threshold(self.key, "recency_window_s", 2 * 86_400))
        wlan_fresh_s = int(ctx.threshold(self.key, "wlan_fresh_s", 7 * 86_400))
        allowlist = _bssid_set(ctx.threshold(self.key, "known_bssids", ()))
        # Floors for the controller-flagged subtype only (see below). Same defaults
        # as the density detector, so "near enough and seen often enough to matter"
        # means one thing across the neighbour detectors.
        rssi_floor = float(ctx.threshold(self.key, "flagged_rssi_floor_dbm", -75))
        persist_span = int(ctx.threshold(self.key, "persist_min_span_s", 3_600))
        persist_min_scans = int(ctx.threshold(self.key, "persist_min_scans", 2))

        neighbors = _neighbor_rows(ctx)
        if not neighbors:
            return UNKNOWN

        own_prefixes, own_macs = _own_hardware_ids(ctx)
        our_ssids, ssid_source = _our_ssids(ctx, wlan_fresh_s)
        wired_prefixes = _wired_client_prefixes(ctx)

        findings: list[Finding] = []
        examined: set[Any] = set()
        for rg in neighbors:
            last_seen = rg["last_seen"]
            if last_seen is None or last_seen < ctx.now_ts - recency_s:
                continue  # gone from the air -> let any open issue clear
            bssid = rg["bssid"]
            if bssid and bssid.lower() in allowlist:
                continue  # explicit known-BSSID allowlist
            if _is_own_hardware(bssid, rg["is_ubnt"], own_prefixes, own_macs):
                continue  # our own AP/mesh radio seen in a neighbour scan
            examined.add(rg["entity"].entity_id)

            essid = str(rg["essid"] or "").strip().casefold()
            ours = our_ssids.get(essid) if essid else None
            if ours is not None:
                findings.append(self._spoof_finding(rg, ours, ssid_source))
                continue  # one box, one issue: the spoof claim subsumes the flag
            if _as_bool(rg["is_rogue"]):
                # The controller's flag is weak, unbounded evidence: nothing caps how
                # many BSSes it sets it on, and it says nothing about proximity. Left
                # ungated it recreates exactly the flood migration 0005 exists to
                # erase (80 neighbours at -92 dBm, seen once, off-channel, each
                # becoming its own P2 that fans out to alerts and auto-investigation).
                # So require the same "near enough, seen often enough" floors the
                # density detector uses. The spoof subtype above is deliberately NOT
                # gated this way: an SSID impersonating ours is urgent at any signal.
                first_seen = rg["first_seen"]
                span = None if first_seen is None else last_seen - first_seen
                if not _persistent(
                    rg, span, persist_span, persist_min_scans, recency_s, ctx.now_ts
                ):
                    continue  # transient sighting
                rssi = rg["rssi"]
                if rssi is None or rssi <= rssi_floor:
                    continue  # weak/distant: not actionable as a security finding
                findings.append(self._controller_finding(rg, wired_prefixes))

        if our_ssids:
            return findings
        # No SSID set at all: the spoof subtype could not be evaluated. Freeze the
        # BSSIDs we looked at so an open spoof issue is never cleared on a guess.
        return DetectorResult.of(findings, examined)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _common_evidence(rg: dict[str, Any]) -> dict[str, Any]:
        return {
            "bssid": rg["bssid"],
            "essid": rg["essid"],
            "band": rg["band"],
            "channel": rg["channel"],
            "channels_seen": rg["channels"],
            "rssi_dbm": rg["rssi"],
            "seen_by_ap": rg["seen_by_ap"],
            "neighbor_security": rg["security"],
            "controller_flagged_rogue": _as_bool(rg["is_rogue"]),
            "controller_is_ubnt": _as_bool(rg["is_ubnt"]),
            "last_seen_ts": rg["last_seen"],
            "logged_scan_count": (len(rg["scan_ts"]) if isinstance(rg["scan_ts"], list) else None),
        }

    def _spoof_finding(
        self, rg: dict[str, Any], ours: dict[str, Any], ssid_source: Optional[str]
    ) -> Finding:
        our_security = str(ours.get("security") or "").strip().lower()
        their_security = str(rg["security"] or "").strip().lower()
        # An open twin of a secured SSID of ours is the classic credential-harvest
        # shape. Only claimed when both sides' security modes are readable.
        security_mismatch: Optional[bool] = None
        if our_security and their_security:
            security_mismatch = our_security != their_security
        evidence = self._common_evidence(rg)
        evidence.update(
            {
                "subtype": "ssid_spoof",
                "matched_our_ssid": ours.get("name"),
                "our_ssid_source": ssid_source,
                "our_ssid_security": ours.get("security"),
                "security_mismatch": security_mismatch,
            }
        )
        return Finding(
            detector_key=self.key,
            entity=rg["entity"],
            severity=Severity.P1,
            title=f"Foreign AP broadcasting our SSID {ours.get('name')}",
            dims={"subtype": "ssid_spoof"},
            evidence=evidence,
            confounders_checked=[
                "known_bssid_allowlist_checked",
                "own_ubnt_hardware_excluded",
                "stale_sighting_excluded",
                f"our_ssid_set_from_{ssid_source}",
                "security_mode_compared",
            ],
        )

    def _controller_finding(
        self, rg: dict[str, Any], wired_prefixes: dict[str, list[str]]
    ) -> Finding:
        prefix = _mac_prefix(_norm_mac(rg["bssid"]))
        matches = sorted(wired_prefixes.get(prefix, [])) if prefix else []
        label = rg["essid"] or rg["bssid"]
        evidence = self._common_evidence(rg)
        evidence.update(
            {
                "subtype": "controller_flagged",
                "verdict_source": "controller",
                "wired_mac_prefix_match": bool(matches),
                "matching_wired_clients": matches,
            }
        )
        return Finding(
            detector_key=self.key,
            entity=rg["entity"],
            severity=Severity.P1 if matches else Severity.P2,
            title=f"Controller flagged {label} as a rogue AP",
            dims={"subtype": "controller_flagged"},
            evidence=evidence,
            confounders_checked=[
                "known_bssid_allowlist_checked",
                "own_ubnt_hardware_excluded",
                "stale_sighting_excluded",
                "wired_mac_prefix_corroboration_checked",
            ],
        )


# ---------------------------------------------------------------------- #
# Shared AP-config helper
# ---------------------------------------------------------------------- #
def _is_mesh_ap(ctx: Any, ap: Entity) -> bool:
    """Whether ``ap`` runs on (or is configured for) a wireless mesh uplink."""
    if _as_bool(ap.meta.get("mesh_enabled")):
        return True
    if ap.entity_id is not None:
        uplink_type = str(ctx.repo.current_state(ap.entity_id, "uplink_type") or "").lower()
        return uplink_type == "wireless"
    return False


__all__ = [
    "KEY_STICKY_CLIENT",
    "KEY_PINGPONG_ROAMER",
    "KEY_ROAM_QUALITY",
    "KEY_MIN_RSSI_MISCONFIG",
    "KEY_CHANNEL_PLAN",
    "KEY_DFS_RECURRING",
    "KEY_AIRTIME_SATURATION",
    "KEY_TX_POWER_LOUD",
    "KEY_LEGACY_RATES",
    "KEY_BAND_STEERING",
    "KEY_MESH_UPLINK",
    "KEY_NEIGHBOR_DENSITY",
    "KEY_ROGUE_AP",
    "ROGUE_BSS_TYPE",
    "RF_ENV_TYPE",
    "RF_ENV_PREFIX",
    "StickyClientDetector",
    "PingpongRoamerDetector",
    "RoamQualityDetector",
    "MinRssiMisconfigDetector",
    "ChannelPlanDetector",
    "DfsRecurringDetector",
    "AirtimeSaturationDetector",
    "TxPowerLoudDetector",
    "LegacyRatesDetector",
    "BandSteeringDetector",
    "MeshUplinkDetector",
    "NeighborDensityDetector",
    "RogueApDetector",
    "wifi_entries",
]


def wifi_entries() -> list:
    """The thirteen ``wifi.*`` catalog registrations, in catalog order.

    A factory (not a module constant) so importing this module for its detector
    classes never eagerly builds :class:`~netadmin.detect.catalog.CatalogEntry`
    objects; the catalog calls this when it assembles ``DEFAULT_CATALOG``.
    """
    from netadmin.detect.catalog import CatalogEntry

    return [
        CatalogEntry(StickyClientDetector(), Severity.P2, "Sticky client {entity}"),
        CatalogEntry(PingpongRoamerDetector(), Severity.P2, "Ping-pong roamer {entity}"),
        CatalogEntry(RoamQualityDetector(), Severity.P3, "Poor roam quality for {entity}"),
        CatalogEntry(MinRssiMisconfigDetector(), Severity.P2, "min-RSSI misconfigured on {entity}"),
        CatalogEntry(ChannelPlanDetector(), Severity.P3, "Channel-plan issue on {entity}"),
        CatalogEntry(DfsRecurringDetector(), Severity.P2, "Recurring DFS radar on {entity}"),
        CatalogEntry(AirtimeSaturationDetector(), Severity.P1, "Airtime saturation on {entity}"),
        CatalogEntry(TxPowerLoudDetector(), Severity.P2, "Loud tx-power on {entity}"),
        CatalogEntry(LegacyRatesDetector(), Severity.P3, "Legacy-rate client {entity}"),
        CatalogEntry(BandSteeringDetector(), Severity.P3, "Band-steering opportunity for {entity}"),
        CatalogEntry(MeshUplinkDetector(), Severity.P2, "Weak mesh uplink on {entity}"),
        CatalogEntry(
            NeighborDensityDetector(), Severity.P2, "Crowded RF neighbourhood on {entity}"
        ),
        CatalogEntry(RogueApDetector(), Severity.P1, "Rogue AP {entity}"),
    ]
