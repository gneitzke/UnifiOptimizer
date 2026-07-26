"""SLE classifier rules (ARCHITECTURE.md section 8, the health-model table).

Each SLE (coverage, roaming, capacity, connect, wan, infra) judges a client-minute
(or, for infra, a device-minute) either *pass* (``ok``) or *fail* under **exactly
one** classifier. That exclusivity is the whole point of the Mist-style model: a
failed minute is attributed to one cause, so ``SUM(minutes)`` over the classifier
breakdown is the same number as the SLE's total exposed minutes and the score and
its explanation are the *same* ``sle_minutes`` GROUP BY.

Everything here is a **pure function**: given the already-extracted per-minute (or
per-bucket) signals plus thresholds and, where the metric is diurnal, a baseline
:class:`~netadmin.detect.baseline.Band`, it returns the single classifier name or
``None`` (a passing minute). No I/O, no repository, no clock — :mod:`minutes`
extracts the signals from the store and calls these. That keeps the rules
exhaustively unit-testable and deterministic: the same inputs always yield the
same classifier.

Threshold provenance: the numeric defaults on :class:`SleConfig` are the researched
section-6/8 values (coverage floor −72 dBm, sticky −75 dBm, >10 dB post-roam
degradation, cu_total 50 % degraded, bufferbloat >200 ms, ISP loss >1 %). Metrics
that swing with time of day (channel utilisation, WAN throughput/latency) also get
a **2σ-from-baseline** test via :func:`exceeds_baseline` so "busy at 2 pm" is not
mistaken for a fault; RSSI, which baseline.py deliberately keeps time-of-day
invariant, is judged on the absolute dBm floor alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Mapping, Optional

from netadmin.domain.types import EntityType

__all__ = [
    # SLE names (mirror sle_minutes.sle)
    "SLE_COVERAGE",
    "SLE_ROAMING",
    "SLE_CAPACITY",
    "SLE_CONNECT",
    "SLE_WAN",
    "SLE_INFRA",
    "ALL_SLES",
    "OK",
    # classifier names (mirror sle_minutes.classifier)
    "CLS_WEAK_SIGNAL",
    "CLS_ASYMMETRY",
    "CLS_PINGPONG",
    "CLS_STICKY",
    "CLS_SLOW_ROAM",
    "CLS_WIFI_INTERFERENCE",
    "CLS_NON_WIFI_UTIL",
    "CLS_CLIENT_LOAD",
    "CLS_ASSOC",
    "CLS_AUTH",
    "CLS_DHCP",
    "CLS_DNS",
    "CLS_ISP_LATENCY",
    "CLS_ISP_LOSS",
    "CLS_BUFFERBLOAT",
    "CLS_WAN_DOWN",
    "CLS_AP_DOWN",
    "CLS_SW_DOWN",
    "CLS_GW_DOWN",
    "CLS_RESTART_LOOP",
    "CLASSIFIERS_BY_SLE",
    # config + rules
    "SleConfig",
    "exceeds_baseline",
    "classify_coverage",
    "classify_capacity",
    "classify_roaming",
    "classify_connect",
    "classify_wan",
    "infra_down_classifier",
]

# --------------------------------------------------------------------------- #
# Names (the exact strings written to sle_minutes)
# --------------------------------------------------------------------------- #
SLE_COVERAGE = "coverage"
SLE_ROAMING = "roaming"
SLE_CAPACITY = "capacity"
SLE_CONNECT = "connect"
SLE_WAN = "wan"
SLE_INFRA = "infra"
ALL_SLES: tuple[str, ...] = (
    SLE_COVERAGE,
    SLE_ROAMING,
    SLE_CAPACITY,
    SLE_CONNECT,
    SLE_WAN,
    SLE_INFRA,
)

OK = "ok"

# coverage
CLS_WEAK_SIGNAL = "weak_signal"
CLS_ASYMMETRY = "asymmetry_suspected"
# roaming
CLS_PINGPONG = "pingpong"
CLS_STICKY = "sticky"
CLS_SLOW_ROAM = "slow_roam"
# capacity
CLS_WIFI_INTERFERENCE = "wifi_interference"
CLS_NON_WIFI_UTIL = "non_wifi_util"
CLS_CLIENT_LOAD = "client_load"
# connect
CLS_ASSOC = "assoc"
CLS_AUTH = "auth"
CLS_DHCP = "dhcp"
CLS_DNS = "dns"
# wan
CLS_ISP_LATENCY = "isp_latency"
CLS_ISP_LOSS = "isp_loss"
CLS_BUFFERBLOAT = "bufferbloat"
CLS_WAN_DOWN = "wan_down"
# infra
CLS_AP_DOWN = "ap_down"
CLS_SW_DOWN = "sw_down"
CLS_GW_DOWN = "gw_down"
CLS_RESTART_LOOP = "restart_loop"

# The full failure vocabulary per SLE (``ok`` is implicit for every SLE). Kept
# here so scores.py and any UI can enumerate breakdown columns without guessing.
CLASSIFIERS_BY_SLE: dict[str, tuple[str, ...]] = {
    SLE_COVERAGE: (CLS_WEAK_SIGNAL, CLS_ASYMMETRY),
    SLE_ROAMING: (CLS_PINGPONG, CLS_STICKY, CLS_SLOW_ROAM),
    SLE_CAPACITY: (CLS_WIFI_INTERFERENCE, CLS_NON_WIFI_UTIL, CLS_CLIENT_LOAD),
    SLE_CONNECT: (CLS_ASSOC, CLS_AUTH, CLS_DHCP, CLS_DNS),
    SLE_WAN: (CLS_ISP_LATENCY, CLS_ISP_LOSS, CLS_BUFFERBLOAT, CLS_WAN_DOWN),
    SLE_INFRA: (CLS_AP_DOWN, CLS_SW_DOWN, CLS_GW_DOWN, CLS_RESTART_LOOP),
}

# Infra device-type -> its "down" classifier (restart_loop overrides both).
_INFRA_DOWN_BY_TYPE: dict[EntityType, str] = {
    EntityType.AP: CLS_AP_DOWN,
    EntityType.SWITCH: CLS_SW_DOWN,
    EntityType.GATEWAY: CLS_GW_DOWN,
}


# --------------------------------------------------------------------------- #
# Config (dataclass defaults; overridable via settings.thresholds["sle"])
# --------------------------------------------------------------------------- #
@dataclass
class SleConfig:
    """Thresholds for the SLE minute job and its classifiers.

    Defaults are the researched section-6/8 values. Every field is overridable
    from ``settings.thresholds["sle"][<field>]`` via :meth:`from_settings`, matching
    the detector convention (dataclass defaults, per-key override section). Weights
    live with the scorer (:mod:`netadmin.sle.scores`), not here — this dataclass is
    strictly the *classification* contract.
    """

    # bucketing / presence
    bucket_seconds: int = 300
    poll_cadence_s: int = 60  # nominal stat/sta cadence -> ~5 samples/bucket

    # activity gate (the "idle client contributes zero" rule, section 8)
    activity_metrics: tuple[str, ...] = ("rx_bytes", "tx_bytes")
    activity_bytes_per_min: float = 1024.0  # ~1 KB/min; a client below this is idle

    # coverage (absolute dBm; RSSI has no diurnal baseline)
    coverage_weak_dbm: float = -72.0
    coverage_snr_min_db: float = 15.0  # rssi-noise below this = suspected asymmetry

    # roaming
    roam_pingpong_count: int = 3  # >= this many roams in one 5-min bucket = ping-pong
    sticky_rssi_dbm: float = -75.0
    slow_roam_degradation_db: float = 10.0  # post-roam RSSI this much worse = slow_roam

    # capacity (channel utilisation %, diurnal -> also 2σ tested)
    capacity_degraded_pct: float = 50.0
    capacity_self_share_min: float = 0.6  # cu_self/cu_total at/above this = client_load

    # wan
    wan_loss_threshold: float = 1.0  # WAN/www drops above this (per-sample) = loss
    wan_latency_abs_ms: float = 100.0
    wan_bufferbloat_ms: float = 200.0
    # isp_latency is judged on a rolling-window p50 of the latency signal, not the
    # bucket's max, so a Starlink ~15 s satellite-handoff spike (80-120 ms, a brief
    # minority of samples) does not brand every client isp_latency — only a
    # genuinely sustained elevation moves a 15-min median. The signal falls back
    # from UniFi wan_latency/www_latency to the probe gw_rtt_ms so the gateway-less
    # Starlink site is judged at all. See minutes.SleMinutesJob._wan_latency_robust.
    wan_latency_window_s: int = 900  # trailing window the latency p50 is taken over
    wan_latency_min_samples: int = 5  # samples needed to trust that window p50
    # bufferbloat load gate: bufferbloat is latency that only appears under load,
    # so an RTT spike counts only when WAN throughput is near the provisioned plan
    # rate (mirrors the wan.bufferbloat detector). Without a throughput/plan signal
    # the "under load" premise is unproven and bufferbloat is not evaluated.
    wan_near_plan_fraction: float = 0.8
    # wan_down needs a sustained majority of failed gw_rtt probes across a lookback
    # window, not a single bucket where the prober hiccuped (which would brand the
    # WAN down for every active client at once).
    wan_down_window_s: int = 900
    wan_down_fail_fraction: float = 0.8
    wan_down_min_polls: int = 3

    # connect: controller event-key substring -> classifier (best-effort; classic
    # controllers expose auth/assoc failures unevenly, so it is data, not code).
    connect_failure_keys: Mapping[str, str] = field(default_factory=dict)

    # shared 2σ multiplier for baseline-relative tests
    sigmas: float = 2.0

    # infra device state codes (mirror detect.detectors.infra)
    infra_down_states: tuple[str, ...] = ("0",)
    infra_online_states: tuple[str, ...] = ("1",)

    @classmethod
    def from_settings(cls, settings: Any) -> "SleConfig":
        """Build a config, overriding defaults from ``settings.thresholds["sle"]``.

        Never raises on a missing/partial section: unknown keys are ignored and
        absent keys keep the dataclass default. Tuple-typed fields accept any
        sequence in the override and are coerced to tuples.
        """
        base = cls()
        thresholds = getattr(settings, "thresholds", None)
        section = thresholds.get("sle") if isinstance(thresholds, dict) else None
        if not isinstance(section, dict):
            return base
        tuple_fields = {f.name for f in fields(cls) if isinstance(getattr(base, f.name), tuple)}
        overrides: dict[str, Any] = {}
        valid = {f.name for f in fields(cls)}
        for key, value in section.items():
            if key not in valid:
                continue
            overrides[key] = tuple(value) if key in tuple_fields else value
        return cls(**{**base.__dict__, **overrides})


def exceeds_baseline(value: Optional[float], band: Any, sigmas: float) -> bool:
    """True when ``value`` sits more than ``sigmas`` σ above a series' baseline.

    ``band`` is a :class:`~netadmin.detect.baseline.Band` (or any object exposing
    ``mean`` and ``std``) or ``None``. A ``None`` band (cold start / non-diurnal
    metric) makes this return ``False`` so the caller falls back to the absolute
    threshold alone — a fabricated baseline never fires a classifier.
    """
    if value is None or band is None:
        return False
    mean = getattr(band, "mean", None)
    std = getattr(band, "std", None)
    if mean is None or std is None:
        return False
    return value > mean + sigmas * std


# --------------------------------------------------------------------------- #
# Per-SLE classifier rules (each returns the classifier name, or None for ok)
# --------------------------------------------------------------------------- #
def classify_coverage(
    rssi: Optional[float],
    noise: Optional[float],
    *,
    weak_threshold_dbm: float,
    snr_min_db: float,
) -> Optional[str]:
    """Coverage. Signal: per-client RSSI (dBm — the controller ``signal`` field,
    not the 0-based ``rssi`` index) and the noise floor.

    RSSI is deliberately non-diurnal (3 am RSSI should equal 3 pm RSSI,
    baseline.py), so coverage is judged on the absolute dBm floor:

    * ``weak_signal`` — RSSI below the coverage floor.
    * ``asymmetry_suspected`` — RSSI adequate but the effective SNR
      (``rssi - noise``) is poor. Client-side downlink RSSI is out of scope
      (section 6), so a low SNR despite an adequate raw RSSI is the observable
      proxy for a degraded reverse path.

    Returns the classifier or ``None`` (a passing minute). ``rssi is None`` (no
    sample) also returns ``None`` — absence of signal is not a coverage failure.
    """
    if rssi is None:
        return None
    if rssi < weak_threshold_dbm:
        return CLS_WEAK_SIGNAL
    if noise is not None and (rssi - noise) < snr_min_db:
        return CLS_ASYMMETRY
    return None


def classify_capacity(
    cu_total: Optional[float],
    cu_self: Optional[float],
    *,
    degraded_pct: float,
    self_share_min: float,
    neighbor_present: bool,
    band: Any = None,
    sigmas: float = 2.0,
) -> Optional[str]:
    """Capacity. Signal: radio channel utilisation ``cu_total`` (%) with the
    self-share ``cu_self`` (``cu_self_rx + cu_self_tx``).

    Fires when ``cu_total`` exceeds the absolute degraded floor **or** sits >2σ
    above the radio's hour-of-day baseline (utilisation is diurnal, so trend beats
    absolute). The fix path needs self vs non-self split (section 6):

    * ``client_load`` — our own airtime dominates (``cu_self/cu_total`` at or above
      ``self_share_min``): the cell is genuinely busy with our clients.
    * ``wifi_interference`` — non-self airtime dominates and a neighbouring BSS is
      known (rogue/neighbour table): another Wi-Fi network is stealing airtime.
    * ``non_wifi_util`` — non-self airtime with no Wi-Fi neighbour to explain it:
      unexplained utilisation, the section-6 non-Wi-Fi-interferer *inference*
      (we never claim spectrum classification we cannot do).

    Returns the classifier or ``None``.
    """
    if cu_total is None:
        return None
    over = cu_total >= degraded_pct or exceeds_baseline(cu_total, band, sigmas)
    if not over:
        return None
    self_share = (cu_self / cu_total) if (cu_self is not None and cu_total > 0) else 0.0
    if self_share >= self_share_min:
        return CLS_CLIENT_LOAD
    if neighbor_present:
        return CLS_WIFI_INTERFERENCE
    return CLS_NON_WIFI_UTIL


def classify_roaming(
    roams: int,
    *,
    min_rssi: Optional[float],
    pre_rssi: Optional[float],
    post_rssi: Optional[float],
    pingpong_count: int,
    sticky_rssi_dbm: float,
    slow_roam_degradation_db: float,
) -> Optional[str]:
    """Roaming. Signal: roam count in the bucket (``roam_count`` counter delta and
    ``*Roam*`` events) plus the client's RSSI trajectory across the bucket.

    Only meaningful for a bucket where a roam actually occurred (``roams > 0``); a
    stationary bucket is not a roaming judgement and the caller does not invoke
    this then.

    * ``pingpong`` — too many roams in one bucket (rapid AP flip-flop).
    * ``sticky`` — the client hung onto a weak AP first: the bucket's minimum RSSI
      is below the sticky floor.
    * ``slow_roam`` — post-roam RSSI is materially worse than pre-roam (it roamed
      to a worse AP): ``post - pre <= -slow_roam_degradation_db``.

    Returns the classifier or ``None`` (a clean roam).
    """
    if roams >= pingpong_count:
        return CLS_PINGPONG
    if min_rssi is not None and min_rssi < sticky_rssi_dbm:
        return CLS_STICKY
    if (
        pre_rssi is not None
        and post_rssi is not None
        and (post_rssi - pre_rssi) <= -slow_roam_degradation_db
    ):
        return CLS_SLOW_ROAM
    return None


def classify_connect(
    *,
    link_local_ip: bool,
    failure_classifier: Optional[str],
    connected: bool,
) -> Optional[str]:
    """Connect. Signal: connection-lifecycle events plus the client's assigned IP.

    The caller only invokes this for a bucket where a connection actually happened
    (a ``*Connected`` event, a mapped failure event, or a self-assigned address),
    so ``connect`` is measured at connection time — the honest Mist "time to
    connect" semantics — never for every idle bucket.

    * ``dhcp`` — a 169.254.x self-assigned address means DHCP never completed
      (section 6): the strongest DHCP signal, so it wins outright.
    * ``auth`` / ``assoc`` / ``dns`` — a controller failure event mapped by key
      (``SleConfig.connect_failure_keys``; best-effort, configurable).
    * otherwise ``ok`` — a clean (re)association with a routable address.

    Returns the classifier or ``None`` (ok).
    """
    if link_local_ip:
        return CLS_DHCP
    if failure_classifier:
        return failure_classifier
    # ``connected`` distinguishes a real (ok) connect event from a no-op; the
    # caller already gated exposure, so a bare True here is a passing minute.
    return None


def classify_wan(
    *,
    reachable: bool,
    loss: Optional[float],
    latency_ms: Optional[float],
    rtt_loaded_ms: Optional[float],
    rtt_idle_ms: Optional[float],
    loss_threshold: float,
    latency_abs_ms: float,
    bufferbloat_ms: float,
    latency_band: Any = None,
    sigmas: float = 2.0,
) -> Optional[str]:
    """WAN. A single per-bucket judgement of the shared uplink, attributed to the
    gateway and applied to every active client (impact-weighted by headcount).

    Priority ``wan_down`` > ``isp_loss`` > ``isp_latency`` > ``bufferbloat``:

    * ``wan_down`` — the uplink was unreachable this bucket (probe failures, no
      RTT samples at all).
    * ``isp_loss`` — WAN/www drop rate above the loss floor.
    * ``isp_latency`` — WAN/www latency above the absolute floor **or** >2σ over its
      7-day baseline (trend beats absolute, section 6).
    * ``bufferbloat`` — loaded RTT minus idle RTT over the bufferbloat floor.

    On the gateway-less site this fires only where probe metrics exist
    (``gw_rtt`` → ``wan_down``/``bufferbloat``); ``isp_latency``/``isp_loss`` stay
    silent for want of ``stat/health`` WAN metrics. Returns the classifier or
    ``None`` (ok). The caller decides evaluability (no data at all → no-op).
    """
    if not reachable:
        return CLS_WAN_DOWN
    if loss is not None and loss > loss_threshold:
        return CLS_ISP_LOSS
    if latency_ms is not None and (
        latency_ms > latency_abs_ms or exceeds_baseline(latency_ms, latency_band, sigmas)
    ):
        return CLS_ISP_LATENCY
    if (
        rtt_loaded_ms is not None
        and rtt_idle_ms is not None
        and (rtt_loaded_ms - rtt_idle_ms) > bufferbloat_ms
    ):
        return CLS_BUFFERBLOAT
    return None


def infra_down_classifier(entity_type: EntityType, *, restart_loop: bool) -> str:
    """Infra down-minute classifier for a device: ``ap_down`` / ``sw_down`` /
    ``gw_down`` by device type, or ``restart_loop`` when the device bounced (a
    down→up cycle repeated inside the bucket, or a lost-contact/reconnect pair).

    ``restart_loop`` overrides the plain down classifier because a flapping device
    is a distinct fault from a cleanly-down one. Unknown device types fall back to
    ``ap_down`` (the caller only ever passes AP/switch/gateway).
    """
    if restart_loop:
        return CLS_RESTART_LOOP
    return _INFRA_DOWN_BY_TYPE.get(entity_type, CLS_AP_DOWN)
