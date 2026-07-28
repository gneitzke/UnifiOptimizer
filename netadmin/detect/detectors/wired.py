"""Wired-plane detectors (``docs/ARCHITECTURE.md`` section 6, ``wired.*``).

Eight port/switch detectors that turn the ``stat/device`` port/switch signals the
store already records — error/broadcast/multicast counters, PoE draw, SFP DOM,
negotiated speed/duplex, link up/down history, and the ``EVT_SW_*`` event stream —
into confounder-checked :class:`~netadmin.domain.entities.Finding` objects:

* :class:`BadCableDetector` (``wired.bad_cable``) — rx/tx error-rate deltas, or a
  gigabit-capable port negotiated down to 10/100 (broken-pair downshift).
* :class:`DuplexMismatchDetector` (``wired.duplex_mismatch``) — half-duplex on a
  modern (>=100 Mbps) up link.
* :class:`PortFlappingDetector` (``wired.port_flapping``) — link transitions above
  a short/long tier; PoE-draw-to-zero between flaps flags a reboot loop; infra
  (uplink) ports escalate to P1.
* :class:`UplinkSaturationDetector` (``wired.uplink_saturation``) — uplink
  utilisation past a % of negotiated speed with rising ``tx_dropped``, checked
  against the hour-of-day throughput baseline first.
* :class:`PoeBudgetDetector` (``wired.poe_budget``) — Σ PoE draw past a % of the
  switch budget, or an ``EVT_SW_PoeOverload``.
* :class:`StpLoopDetector` (``wired.stp_loop``) — an ``EVT_SW_StpPortBlocking`` or a
  port sitting in a blocking STP state.
* :class:`BroadcastStormDetector` (``wired.broadcast_storm``) — broadcast rate
  outlier-relative to its 24 h baseline on **multiple** ports of one switch at once.
* :class:`SfpDegradedDetector` (``wired.sfp_degraded``) — SFP DOM out of band:
  rx power near the sensitivity floor or drifting down, tx power below its floor,
  module temperature over its limit, bias current climbing away from its own
  baseline, or an rx/tx fault flag.

Every detector gates on ``fast_device`` coverage and returns the engine's
``UNKNOWN`` sentinel below :data:`~netadmin.detect.engine.COVERAGE_MIN` rather than
guess at wired state through a collection gap. Thresholds are module-level defaults
overridable per detector via ``ctx.threshold(key, name, default)``. The site has no
UniFi gateway; these detectors read only switch/port entities and no-op cleanly
when the signal is absent (a missing series, a cold baseline, a field ingest does
not yet persist) rather than fabricate a finding.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Iterable, Optional

from netadmin.detect import device_kb
from netadmin.detect.baseline import hour_label
from netadmin.detect.engine import COVERAGE_MIN, UNKNOWN, EvalResult
from netadmin.domain.entities import Entity, Finding
from netadmin.domain.types import Cadence, EntityType, Severity
from netadmin.logging import get_logger

_log = get_logger("detect.wired")

KEY_BAD_CABLE = "wired.bad_cable"
KEY_DUPLEX_MISMATCH = "wired.duplex_mismatch"
KEY_PORT_FLAPPING = "wired.port_flapping"
KEY_UPLINK_SATURATION = "wired.uplink_saturation"
KEY_POE_BUDGET = "wired.poe_budget"
KEY_STP_LOOP = "wired.stp_loop"
KEY_BROADCAST_STORM = "wired.broadcast_storm"
KEY_SFP_DEGRADED = "wired.sfp_degraded"

_COVERAGE_JOB = "fast_device"

# Device-name / OUI substrings whose peers commonly negotiate 10/100 by design, so
# a gigabit-capable switch port sitting at 100 Mbps to one of them is NOT a
# broken-pair downshift. Seeded with common wired IoT/legacy classes and augmented
# best-effort from wifi_device_capabilities.json's known-2.4GHz-only list
# (those classes — smart plugs, ESP modules, legacy consoles — are the same
# fast-ethernet-at-best population).
_KNOWN_100MBPS_HINTS: tuple[str, ...] = (
    "esp32",
    "esp8266",
    "sonoff",
    "shelly",
    "tasmota",
    "smart plug",
    "smart switch",
    "printer",
    "brother",
    "chromecast",
    "ring",
    "nest",
    "wemo",
    "roku",
    "wii",
    "ps3",
    "xbox 360",
    "raspberry pi zero",
    # 10/100 smart-home hubs: fixed-100 by design, frequently wired. A gigabit
    # port sitting at 100 to one of these is the device, not a broken pair —
    # confirmed against a real Sure Petcare Hub (clean link, zero errors).
    "petcare",
    "surehub",
    "hue bridge",
    "smartthings",
    "harmony hub",
    "lutron",
    "envisalink",
    # UniFi Protect cameras that ship a 10/100 port BY MODEL, per Ubiquiti's own
    # tech specs ("10/100 MbE RJ45 port"). A PoE switch port sitting at 100 Mbps
    # to one of these is the camera, not a broken pair — confirmed on a real site
    # where both G6 Turrets negotiated 100 with zero rx/tx errors and zero drops
    # across 5,518 samples. Without this a UniFi tool reports UniFi hardware as
    # cable faults, which is how this list acquired its first entries too.
    #
    # Listed PER MODEL, never by form factor: "turret" would also match the G6
    # Pro Turret and "lpr" the AI LPR, and BOTH of those are "GbE RJ45 port" —
    # matching them would suppress a genuine downshift. Verify the spec page
    # before adding a model here. One spelling each: _normalise_for_match folds
    # hyphens and underscores to spaces, so "g6 turret" matches the real-world
    # "g6-turret---driveway".
    "g6 turret",
    "g5 turret ultra",
    "g5 flex",
    "g5 bullet",
)


@lru_cache(maxsize=1)
def _known_100mbps_patterns() -> tuple[str, ...]:
    """Built-in 10/100 device hints plus the capabilities-file 2.4GHz-only list.

    Loaded once, best-effort, via :mod:`netadmin.detect.device_kb`: a missing or
    unparseable KB simply yields the built-in list. The 2.4GHz-only classes there
    (ESP modules, smart plugs, legacy consoles) overlap the wired fast-ethernet
    population, so they are a usable supplementary hint.

    This reads the default KB location only. The ``client.known_pathology``
    ``kb_path`` override does not apply here: the result is process-cached and
    this helper has no DetectorContext to read thresholds from.

    A failure is worth one line now that a baseline always ships inside the
    package: it means the operator's copy is corrupt or the install is damaged,
    neither of which is routine. ``client.known_pathology`` cannot be relied on
    to report it -- it returns UNKNOWN on low coverage before ever loading the
    KB, and under a ``kb_path`` override it reads a different file entirely.
    """
    patterns = list(_KNOWN_100MBPS_HINTS)
    kb = device_kb.load_kb()
    if kb is None:
        _log.warning(
            "bad_cable: device KB at %s is missing or unparseable; falling back to "
            "built-in 10/100 hints only",
            device_kb.default_kb_path(),
        )
    patterns.extend(device_kb.section_patterns(kb, "known_2.4ghz_only"))
    return tuple(dict.fromkeys(patterns))  # de-dup, order-stable


# ---------------------------------------------------------------------- #
# Shared helpers
# ---------------------------------------------------------------------- #
def _as_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> Optional[bool]:
    """Coerce a stored state string/scalar to bool (``current_state`` returns str)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "up", "full"):
        return True
    if s in ("false", "0", "no", "down", "half"):
        return False
    return None


def _mean_rate(window: Any) -> Optional[float]:
    """Mean per-second rate of a counter window (uses ``WindowResult.rate()``)."""
    if window is None:
        return None
    rates = window.rate()
    if not rates:
        return None
    return sum(r["rate"] for r in rates) / len(rates)


def _mean_raw(window: Any) -> Optional[float]:
    """Mean per-interval sample value over a window (delta space for counters)."""
    if window is None or not window.rows:
        return None
    vals: list[float] = []
    for row in window.rows:
        v = row.get("value") if "value" in row else row.get("avg")
        if v is not None:
            vals.append(float(v))
    if not vals:
        return None
    return sum(vals) / len(vals)


def _latest_gauge(window: Any) -> Optional[float]:
    """Latest gauge value in a window (raw ``value``, else rollup ``last``)."""
    if window is None or not window.rows:
        return None
    row = window.rows[-1]
    v = row.get("value")
    if v is None:
        v = row.get("last")
    return None if v is None else float(v)


def _ports(ctx: Any) -> list[Entity]:
    return [p for p in ctx.entities(EntityType.PORT) if p.entity_id is not None]


def _switches_by_id(ctx: Any) -> dict[int, Entity]:
    return {s.entity_id: s for s in ctx.entities(EntityType.SWITCH) if s.entity_id is not None}


def _is_infra_port(port: Entity) -> bool:
    """A switch-to-switch / switch-to-AP uplink port (weighted higher, P1)."""
    return bool(port.meta.get("is_uplink"))


def _coverage_gate(ctx: Any, key: str) -> bool:
    """True when live ``fast_device`` coverage clears the UNKNOWN floor."""
    window_s = int(ctx.threshold(key, "coverage_window_s", 600))
    job = str(ctx.threshold(key, "coverage_job", _COVERAGE_JOB))
    return ctx.coverage(window_s, job) >= COVERAGE_MIN


def _band(ctx: Any, entity_id: int, metric: str, *, bucket: Optional[str] = None) -> Any:
    """The baseline :class:`Band` for a series, or ``None`` (cold / unknown)."""
    series_id = ctx.repo.get_series(entity_id, metric)
    if series_id is None:
        return None
    return ctx.baselines.band(series_id, bucket=bucket)


def _wired_clients_under(ctx: Any, switch_id: int) -> list[Entity]:
    return [
        c
        for c in ctx.entities(EntityType.CLIENT)
        if c.parent_id == switch_id and c.meta.get("is_wired")
    ]


def _normalise_for_match(text: str) -> str:
    """Lowercase, and collapse every run of non-alphanumerics to one space.

    Operators name devices "G6-Turret---Driveway" or "Kitchen_Smart_Plug", so a
    raw substring test against a multi-word pattern never fires: "smart plug"
    does not appear in "Kitchen-Smart-Plug". Normalising once here is what lets a
    pattern be written the way the product is spelled, and is why this list needs
    only one spelling per model.
    """
    return " ".join("".join(c if c.isalnum() else " " for c in text.lower()).split())


def _port_index(port: Entity) -> Optional[int]:
    """The switch port number from a port entity's ``<sw_mac>:<idx>`` native id."""
    return _as_int(str(port.native_id).rsplit(":", 1)[-1])


def _peers_on_port(ctx: Any, switch_id: int, port: Entity) -> list[Entity]:
    """The wired clients to weigh against THIS port, narrowest scope available.

    The controller reports each wired client's ``sw_port``, so when that is known
    the only relevant peer is the one actually on this port. Falling back to every
    client on the switch -- which is all this could do before ``sw_port`` was
    persisted -- means one 10/100 camera suppresses the downshift arm for every
    other port on that switch, hiding a genuine broken pair on an unrelated run.

    The fallback survives only for clients polled before ``sw_port`` was stored:
    if ANY client under the switch reports a port, the port map is trusted and an
    empty result means "no wired peer here", not "check them all".
    """
    peers = _wired_clients_under(ctx, switch_id)
    if not any(c.meta.get("sw_port") is not None for c in peers):
        return peers  # no port map available; legacy switch-wide behaviour
    idx = _port_index(port)
    if idx is None:
        return []
    return [c for c in peers if _as_int(c.meta.get("sw_port")) == idx]


def _matches_known_100mbps(entity: Entity) -> bool:
    """True when the device's own name or OUI names a 10/100-by-design class.

    Fields are tested SEPARATELY, never joined: concatenating them lets a pattern
    straddle the boundary, so a client called "Cam-G5" from OUI "Flextronics"
    would match "g5 flex" and silence a real downshift.
    """
    fields = [_normalise_for_match(str(x)) for x in (entity.name, entity.meta.get("oui")) if x]
    pats = _known_100mbps_patterns()
    return any(pat in field for field in fields for pat in pats)


def _finding(
    key: str,
    entity: Entity,
    severity: Severity,
    title: str,
    evidence: dict[str, Any],
    confounders: Iterable[str],
    *,
    dims: Optional[dict[str, str]] = None,
) -> Finding:
    return Finding(
        detector_key=key,
        entity=entity,
        severity=severity,
        title=title,
        dims=dims or {},
        evidence=evidence,
        confounders_checked=list(confounders),
    )


# ====================================================================== #
# wired.bad_cable
# ====================================================================== #
class BadCableDetector:
    """``wired.bad_cable`` — error-rate deltas or a broken-pair speed downshift.

    Fires when a port's rx/tx error rate is sustained above an absolute per-minute
    floor **or** above a fraction of its packet volume (when ``rx_packets`` is
    recorded), or when a gigabit-capable port has negotiated down to 10/100 — the
    classic broken-pair symptom — after ruling out peers that are 10/100 by design.
    P2, escalated to P1 on an infra/uplink port. Empty list = every port clean.
    """

    key = KEY_BAD_CABLE
    scope = EntityType.PORT
    cadence = Cadence.WINDOW

    def evaluate(self, ctx: Any) -> EvalResult:
        if not _coverage_gate(ctx, self.key):
            return UNKNOWN

        window_s = int(ctx.threshold(self.key, "window_s", 900))
        errors_per_min = float(ctx.threshold(self.key, "errors_per_min", 10.0))
        err_fraction = float(ctx.threshold(self.key, "error_packet_fraction", 1e-5))

        switches = _switches_by_id(ctx)
        findings: list[Finding] = []
        for port in _ports(ctx):
            evidence, confounders = self._assess(
                ctx, port, switches, window_s, errors_per_min, err_fraction
            )
            if evidence is None:
                continue
            infra = _is_infra_port(port)
            sev = Severity.P1 if infra else Severity.P2
            label = port.name or port.native_id
            findings.append(
                _finding(
                    self.key,
                    port,
                    sev,
                    f"Cable/link fault on port {label}",
                    evidence,
                    confounders,
                )
            )
        return findings

    def _assess(
        self,
        ctx: Any,
        port: Entity,
        switches: dict[int, Entity],
        window_s: int,
        errors_per_min: float,
        err_fraction: float,
    ) -> tuple[Optional[dict[str, Any]], list[str]]:
        confounders: list[str] = ["coverage_gated", "counter_reset_handled"]
        signals: list[str] = []
        evidence: dict[str, Any] = {}

        rx = _mean_rate(ctx.window(port.entity_id, "rx_errors", window_s)) or 0.0
        tx = _mean_rate(ctx.window(port.entity_id, "tx_errors", window_s)) or 0.0
        err_per_min = (rx + tx) * 60.0

        pkt_rate = _mean_rate(ctx.window(port.entity_id, "rx_packets", window_s))
        fraction = None
        if pkt_rate and pkt_rate > 0:
            fraction = (rx + tx) / pkt_rate
            confounders.append("packet_volume_normalized")

        if err_per_min > errors_per_min or (fraction is not None and fraction > err_fraction):
            signals.append("error_rate")
            evidence["errors_per_min"] = round(err_per_min, 3)
            evidence["errors_per_min_threshold"] = errors_per_min
            if fraction is not None:
                evidence["error_packet_fraction"] = fraction

        down = self._downshift(ctx, port, switches, confounders)
        if down is not None:
            signals.append("speed_downshift")
            evidence.update(down)

        if not signals:
            return None, confounders
        evidence["signals"] = signals
        return evidence, confounders

    def _downshift(
        self,
        ctx: Any,
        port: Entity,
        switches: dict[int, Entity],
        confounders: list[str],
    ) -> Optional[dict[str, Any]]:
        """Gigabit-capable port negotiated at 10/100, peer not a known 10/100 class."""
        cap = _as_int(port.meta.get("max_speed")) or _speed_caps_max(port.meta.get("speed_caps"))
        if cap is None or cap < 1000:
            return None  # cannot assert the port is gigabit-capable
        neg = _as_int(ctx.repo.current_state(port.entity_id, "speed"))
        if neg is None or neg >= 1000 or neg <= 0:
            return None
        # Confounder: a peer that is 10/100 by design is not a bad cable. Only a
        # 100 Mbps link can be explained that way -- a 10/100 device sitting at
        # *10* is 100BASE-TX falling back, which is the broken-pair signature this
        # arm exists to catch, so it is never explained away by device class.
        switch = switches.get(port.parent_id) if port.parent_id is not None else None
        if switch is not None and neg == 100:
            candidates = _peers_on_port(ctx, switch.entity_id, port)
            if candidates:
                confounders.append("known_100mbps_device_class")
                match = next((c for c in candidates if _matches_known_100mbps(c)), None)
                if match is not None:
                    # Say so. A suppressed finding is never constructed, so the
                    # confounder list dies with it and the operator is left with
                    # an absence they cannot explain -- exactly the silence that
                    # made the un-shipped device KB invisible for so long.
                    _log.info(
                        "bad_cable: %s negotiated %s of %s Mbps, downshift not reported "
                        "-- peer %r is a known 10/100-by-design class",
                        port.native_id,
                        neg,
                        cap,
                        match.name,
                    )
                    return None
        confounders.append("port_gigabit_capable")
        return {"negotiated_speed": neg, "port_capable_speed": cap}


def _speed_caps_max(caps: Any) -> Optional[int]:
    """Highest speed (Mbps) advertised in a UniFi ``speed_caps`` bitmask, or None.

    ``speed_caps`` is the port's autoneg-advertisement bitmask. Bit 0 (``0x01``) is
    the autoneg flag; the remaining low bits advertise a supported speed/duplex,
    and the highest speed bit set is the port's capability ceiling:

    ==========  =============
    bit (mask)  speed (Mbps)
    ==========  =============
    ``0x02``    10 (half)
    ``0x04``    10 (full)
    ``0x08``    100 (half)
    ``0x10``    100 (full)
    ``0x20``    1000
    ``0x40``    2500
    ``0x80``    5000
    ``0x100``   10000
    ==========  =============

    Verified against recorded ``stat/device`` port rows: a copper GE port reports
    ``0x10002F`` (autoneg + 10/100 + ``0x20`` 1000) and a 1G SFP reports
    ``0x100020`` (``0x20`` only) -- both 1000-capable via bit ``0x20``. The common
    high capability flag (``0x100000`` on modern firmware) is **not** a speed bit
    and is deliberately ignored: only the speed bits below are consulted, so a
    stray flag can never inflate the ceiling. Absent/zero/no-speed-bit -> None.
    """
    bits = _as_int(caps)
    if not bits:
        return None
    ladder = (
        (0x100, 10000),
        (0x80, 5000),
        (0x40, 2500),
        (0x20, 1000),
        (0x10, 100),
        (0x08, 100),
        (0x04, 10),
        (0x02, 10),
    )
    for bit, mbps in ladder:
        if bits & bit:
            return mbps
    return None


# ====================================================================== #
# wired.duplex_mismatch
# ====================================================================== #
class DuplexMismatchDetector:
    """``wired.duplex_mismatch`` — half-duplex on a modern, up link -> P2."""

    key = KEY_DUPLEX_MISMATCH
    scope = EntityType.PORT
    cadence = Cadence.FAST

    def evaluate(self, ctx: Any) -> EvalResult:
        if not _coverage_gate(ctx, self.key):
            return UNKNOWN
        modern_min = int(ctx.threshold(self.key, "modern_speed_min", 100))

        findings: list[Finding] = []
        for port in _ports(ctx):
            up = _as_bool(ctx.repo.current_state(port.entity_id, "up"))
            if up is False:
                continue  # a down port has no meaningful duplex
            full = _as_bool(ctx.repo.current_state(port.entity_id, "full_duplex"))
            speed = _as_int(ctx.repo.current_state(port.entity_id, "speed"))
            if full is not False or speed is None or speed < modern_min:
                continue
            label = port.name or port.native_id
            findings.append(
                _finding(
                    self.key,
                    port,
                    Severity.P2,
                    f"Half-duplex on modern link: port {label}",
                    {"full_duplex": False, "speed": speed, "modern_speed_min": modern_min},
                    ["coverage_gated", "link_up_checked", "modern_speed_link"],
                )
            )
        return findings


# ====================================================================== #
# wired.port_flapping
# ====================================================================== #
class PortFlappingDetector:
    """``wired.port_flapping`` — link transitions above a short/long tier.

    Counts recorded ``up`` transitions in a short (10 min) and long (1 h) window.
    A PoE port whose draw falls to ~0 between transitions is a powered-device
    reboot loop (recorded in evidence). Infra/uplink ports escalate to P1.
    """

    key = KEY_PORT_FLAPPING
    scope = EntityType.PORT
    cadence = Cadence.WINDOW

    def evaluate(self, ctx: Any) -> EvalResult:
        if not _coverage_gate(ctx, self.key):
            return UNKNOWN
        short_s = int(ctx.threshold(self.key, "window_short_s", 600))
        long_s = int(ctx.threshold(self.key, "window_long_s", 3600))
        n_short = int(ctx.threshold(self.key, "transitions_short", 5))
        n_long = int(ctx.threshold(self.key, "transitions_long", 10))
        poe_floor = float(ctx.threshold(self.key, "poe_reboot_floor_w", 0.5))

        findings: list[Finding] = []
        for port in _ports(ctx):
            history = ctx.repo.state_history(port.entity_id, "up", limit=500)
            short_ct = sum(1 for r in history if int(r["ts"]) >= ctx.now_ts - short_s)
            long_ct = sum(1 for r in history if int(r["ts"]) >= ctx.now_ts - long_s)
            if short_ct < n_short and long_ct < n_long:
                continue

            evidence: dict[str, Any] = {
                "transitions_short": short_ct,
                "transitions_long": long_ct,
                "window_short_s": short_s,
                "window_long_s": long_s,
            }
            confounders = ["coverage_gated", "sustained_transition_count"]

            poe_win = ctx.window(port.entity_id, "poe_power", long_s)
            if poe_win is not None and poe_win.rows:
                confounders.append("poe_reboot_correlated")
                poe_min = min(_as_float(r.get("value")) or 0.0 for r in poe_win.rows)
                poe_max = max(_as_float(r.get("value")) or 0.0 for r in poe_win.rows)
                if poe_max > poe_floor and poe_min <= poe_floor:
                    evidence["poe_reboot_loop"] = True
                    evidence["poe_min_w"] = poe_min
                    evidence["poe_max_w"] = poe_max

            infra = _is_infra_port(port)
            sev = Severity.P1 if infra else Severity.P2
            label = port.name or port.native_id
            findings.append(
                _finding(
                    self.key,
                    port,
                    sev,
                    f"Port flapping: {label} ({short_ct} transitions/10m)",
                    evidence,
                    confounders,
                )
            )
        return findings


# ====================================================================== #
# wired.uplink_saturation
# ====================================================================== #
class UplinkSaturationDetector:
    """``wired.uplink_saturation`` — uplink past a % of negotiated speed -> P2.

    Utilisation is measured against the negotiated link speed with rising
    ``tx_dropped`` required as corroboration, and checked against the hour-of-day
    throughput baseline first: a busy-hour peak that stays within its diurnal p95
    is normal, not saturation.
    """

    key = KEY_UPLINK_SATURATION
    scope = EntityType.PORT
    cadence = Cadence.WINDOW

    def evaluate(self, ctx: Any) -> EvalResult:
        if not _coverage_gate(ctx, self.key):
            return UNKNOWN
        window_s = int(ctx.threshold(self.key, "window_s", 300))
        degraded_pct = float(ctx.threshold(self.key, "degraded_pct", 80.0))
        critical_pct = float(ctx.threshold(self.key, "critical_pct", 95.0))

        findings: list[Finding] = []
        for port in _ports(ctx):
            if not _is_infra_port(port):
                continue  # only uplink ports carry an aggregate this matters on
            speed = _as_int(ctx.repo.current_state(port.entity_id, "speed"))
            if not speed or speed <= 0:
                continue

            tx_rate = _mean_rate(ctx.window(port.entity_id, "tx_bytes", window_s)) or 0.0
            rx_rate = _mean_rate(ctx.window(port.entity_id, "rx_bytes", window_s)) or 0.0
            busiest = max(tx_rate, rx_rate)
            util_pct = (busiest * 8.0) / (speed * 1_000_000.0) * 100.0
            if util_pct < degraded_pct:
                continue

            confounders = ["coverage_gated"]
            # Hour-of-day baseline first: suppress a within-diurnal-norm peak.
            direction = "tx_bytes" if tx_rate >= rx_rate else "rx_bytes"
            band = _band(ctx, port.entity_id, direction, bucket=hour_label(ctx.now_ts))
            if band is not None:
                confounders.append("diurnal_hour_baseline")
                latest = _mean_raw(ctx.window(port.entity_id, direction, window_s))
                if latest is not None and latest <= band.p95:
                    continue  # normal for this hour

            drop_rate = _mean_rate(ctx.window(port.entity_id, "tx_dropped", window_s)) or 0.0
            if drop_rate <= 0:
                continue  # saturation without drops is headroom, not a problem
            confounders.append("tx_dropped_corroborated")

            label = port.name or port.native_id
            findings.append(
                _finding(
                    self.key,
                    port,
                    Severity.P2,
                    f"Uplink saturation on {label} ({util_pct:.0f}% of {speed} Mbps)",
                    {
                        "utilization_pct": round(util_pct, 1),
                        "degraded_pct": degraded_pct,
                        "critical": util_pct >= critical_pct,
                        "negotiated_speed_mbps": speed,
                        "tx_dropped_per_s": round(drop_rate, 3),
                    },
                    confounders,
                )
            )
        return findings


# ====================================================================== #
# wired.poe_budget
# ====================================================================== #
class PoeBudgetDetector:
    """``wired.poe_budget`` — Σ PoE draw past a % of budget, or an overload event.

    P2 at the warn tier, P1 at the critical tier or on ``EVT_SW_PoeOverload``. The
    budget is read from switch meta (``poe_budget`` / ``total_max_power``); without
    it, only the overload event can fire (no fabricated percentage).
    """

    key = KEY_POE_BUDGET
    scope = EntityType.SWITCH
    cadence = Cadence.FAST
    OVERLOAD_EVENT = "EVT_SW_PoeOverload"

    def evaluate(self, ctx: Any) -> EvalResult:
        if not _coverage_gate(ctx, self.key):
            return UNKNOWN
        warn_pct = float(ctx.threshold(self.key, "warn_pct", 80.0))
        crit_pct = float(ctx.threshold(self.key, "crit_pct", 90.0))
        event_window_s = int(ctx.threshold(self.key, "event_window_s", 900))
        poe_window_s = int(ctx.threshold(self.key, "poe_window_s", 600))

        ports_by_switch: dict[int, list[Entity]] = {}
        for port in _ports(ctx):
            if port.parent_id is not None:
                ports_by_switch.setdefault(port.parent_id, []).append(port)

        findings: list[Finding] = []
        for switch in _switches_by_id(ctx).values():
            draw = 0.0
            measured = False
            for port in ports_by_switch.get(switch.entity_id, ()):
                latest = _latest_gauge(ctx.window(port.entity_id, "poe_power", poe_window_s))
                if latest is not None:
                    draw += latest
                    measured = True

            budget = _as_float(switch.meta.get("poe_budget")) or _as_float(
                switch.meta.get("total_max_power")
            )
            overload = ctx.events(
                entity_id=switch.entity_id,
                keys=[self.OVERLOAD_EVENT],
                since_ts=ctx.now_ts - event_window_s,
            )
            confounders = ["coverage_gated", "overload_event_checked"]

            pct = None
            if budget and budget > 0 and measured:
                pct = draw / budget * 100.0
                confounders.append("budget_known")

            over_warn = pct is not None and pct >= warn_pct
            if not over_warn and not overload:
                continue

            critical = bool(overload) or (pct is not None and pct >= crit_pct)
            sev = Severity.P1 if critical else Severity.P2
            evidence: dict[str, Any] = {
                "poe_draw_w": round(draw, 2),
                "overload_events": len(overload),
            }
            if pct is not None:
                evidence["budget_pct"] = round(pct, 1)
                evidence["poe_budget_w"] = budget
                evidence["warn_pct"] = warn_pct
            label = switch.name or switch.native_id
            findings.append(
                _finding(
                    self.key,
                    switch,
                    sev,
                    f"PoE budget pressure on {label}",
                    evidence,
                    confounders,
                )
            )
        return findings


# ====================================================================== #
# wired.stp_loop
# ====================================================================== #
class StpLoopDetector:
    """``wired.stp_loop`` — an STP-blocking event or a port in a blocking state -> P1."""

    key = KEY_STP_LOOP
    scope = EntityType.PORT
    cadence = Cadence.FAST
    BLOCKING_EVENT = "EVT_SW_StpPortBlocking"
    DEFAULT_BLOCKING_STATES = ("blocking", "broken", "discarding")

    def evaluate(self, ctx: Any) -> EvalResult:
        if not _coverage_gate(ctx, self.key):
            return UNKNOWN
        event_window_s = int(ctx.threshold(self.key, "event_window_s", 900))
        blocking_states = {
            str(s).lower()
            for s in ctx.threshold(self.key, "blocking_states", self.DEFAULT_BLOCKING_STATES)
        }

        blocking_events = ctx.events(
            keys=[self.BLOCKING_EVENT], since_ts=ctx.now_ts - event_window_s
        )
        event_entity_ids = {
            int(r["entity_id"]) for r in blocking_events if r["entity_id"] is not None
        }

        findings: list[Finding] = []
        for port in _ports(ctx):
            has_event = port.entity_id in event_entity_ids
            state = ctx.repo.current_state(port.entity_id, "stp_state")
            state_blocking = state is not None and str(state).lower() in blocking_states
            if not has_event and not state_blocking:
                continue
            label = port.name or port.native_id
            findings.append(
                _finding(
                    self.key,
                    port,
                    Severity.P1,
                    f"STP loop / blocking on port {label}",
                    {
                        "stp_state": None if state is None else str(state),
                        "blocking_event": has_event,
                    },
                    ["coverage_gated", "stp_event_or_state"],
                )
            )
        return findings


# ====================================================================== #
# wired.broadcast_storm
# ====================================================================== #
class BroadcastStormDetector:
    """``wired.broadcast_storm`` — broadcast rate outlier on multiple ports at once.

    Outlier-relative, not absolute: a port storms when its recent broadcast rate is
    ``multiplier``x its own rolling baseline. A single chatty host does not qualify
    — a real storm lights up **multiple** ports of one switch simultaneously, which
    is the fire condition (P1, one issue per switch).
    """

    key = KEY_BROADCAST_STORM
    scope = EntityType.SWITCH
    cadence = Cadence.WINDOW

    def evaluate(self, ctx: Any) -> EvalResult:
        if not _coverage_gate(ctx, self.key):
            return UNKNOWN
        window_s = int(ctx.threshold(self.key, "window_s", 900))
        multiplier = float(ctx.threshold(self.key, "multiplier", 10.0))
        min_ports = int(ctx.threshold(self.key, "min_ports", 2))
        min_baseline = float(ctx.threshold(self.key, "min_baseline_delta", 1.0))

        ports_by_switch: dict[int, list[Entity]] = {}
        for port in _ports(ctx):
            if port.parent_id is not None:
                ports_by_switch.setdefault(port.parent_id, []).append(port)

        findings: list[Finding] = []
        for switch in _switches_by_id(ctx).values():
            storming: list[dict[str, Any]] = []
            for port in ports_by_switch.get(switch.entity_id, ()):
                current = _mean_raw(ctx.window(port.entity_id, "rx_broadcast", window_s))
                band = _band(ctx, port.entity_id, "rx_broadcast")
                if current is None or band is None:
                    continue
                baseline = max(band.p50, min_baseline)
                if current > multiplier * baseline:
                    # A port that is DOWN cannot storm: on combo uplinks (USW
                    # Flex 2.5G port 9 RJ45 / port 10 SFP+) the controller
                    # mirrors one uplink's counters onto both entries while only
                    # one can link, so the dead half double-counts the live one
                    # and a single chatty uplink reads as the "multiple ports
                    # simultaneously" this detector requires. Only an explicit
                    # down excludes -- unrecorded state is not evidence -- and
                    # the skip is logged, because a P1 that stops firing with no
                    # trace is the silence that hid the unshipped device KB.
                    if _as_bool(ctx.repo.current_state(port.entity_id, "up")) is False:
                        _log.info(
                            "broadcast_storm: %s reads %.1fx its broadcast baseline "
                            "but the link is down -- mirrored combo-uplink counters, "
                            "not counted as a storming port",
                            port.native_id,
                            current / baseline,
                        )
                        continue
                    storming.append(
                        {
                            "port": port.native_id,
                            "current_delta": round(current, 2),
                            "baseline_p50": round(band.p50, 2),
                            "ratio": round(current / baseline, 1),
                        }
                    )
            if len(storming) < min_ports:
                continue  # single-port chatter is not a storm
            label = switch.name or switch.native_id
            findings.append(
                _finding(
                    self.key,
                    switch,
                    Severity.P1,
                    f"Broadcast storm on {label} ({len(storming)} ports)",
                    {
                        "multiplier": multiplier,
                        "ports_storming": len(storming),
                        "detail": storming,
                    },
                    [
                        "coverage_gated",
                        "baseline_relative",
                        "multi_port_simultaneous",
                        "link_up_checked",
                    ],
                )
            )
        return findings


# ====================================================================== #
# wired.sfp_degraded
# ====================================================================== #
class SfpDegradedDetector:
    """``wired.sfp_degraded`` — SFP DOM out of band on any of six arms.

    The optic's full digital-monitoring block is read, not just rx power:

    * **rx power** at or below the sensitivity floor, or drifting down from its
      own baseline;
    * **tx power** at or below its floor (a laser that has stopped driving);
    * **module temperature** above its limit;
    * **bias current** risen well above its own baseline, the aging-laser
      signature (the laser draws more current to hold its output). Absolute bias
      limits are vendor-specific and the controller exposes no DOM alarm
      thresholds, so this is judged against the module's own history, never an
      invented absolute;
    * **fault latches** (``sfp_rxfault`` / ``sfp_txfault``).

    P2, dropping to P3 when the only signals are drift (a trend worth watching,
    not yet a broken link). Module temperature is deliberately *not* a standalone
    trigger when the host chassis is itself hot: a warm optic in a warm switch is
    the switch's problem, and ``infra.device_overheating`` owns that finding.
    """

    key = KEY_SFP_DEGRADED
    scope = EntityType.PORT
    cadence = Cadence.WINDOW

    # Signals that describe a trend rather than a link that is already out of
    # band. A finding built only from these is P3.
    DRIFT_SIGNALS = frozenset({"rx_power_drift", "bias_current_drift"})

    def evaluate(self, ctx: Any) -> EvalResult:
        if not _coverage_gate(ctx, self.key):
            return UNKNOWN
        window_s = int(ctx.threshold(self.key, "window_s", 900))
        rx_floor = float(ctx.threshold(self.key, "rx_power_floor_dbm", -14.0))
        drift_db = float(ctx.threshold(self.key, "drift_db", 3.0))
        tx_floor = float(ctx.threshold(self.key, "tx_power_floor_dbm", -8.0))
        module_temp_max = float(ctx.threshold(self.key, "module_temp_max_c", 70.0))
        bias_drift_pct = float(ctx.threshold(self.key, "bias_drift_pct", 25.0))
        chassis_hot_c = float(ctx.threshold(self.key, "chassis_hot_c", 60.0))

        findings: list[Finding] = []
        for port in _ports(ctx):
            rx = _latest_gauge(ctx.window(port.entity_id, "sfp_rxpower", window_s))
            tx = _latest_gauge(ctx.window(port.entity_id, "sfp_txpower", window_s))
            module_temp = _latest_gauge(ctx.window(port.entity_id, "sfp_temperature", window_s))
            bias = _latest_gauge(ctx.window(port.entity_id, "sfp_current", window_s))
            rxfault = _as_bool(ctx.repo.current_state(port.entity_id, "sfp_rxfault"))
            txfault = _as_bool(ctx.repo.current_state(port.entity_id, "sfp_txfault"))
            if (
                rx is None
                and tx is None
                and module_temp is None
                and bias is None
                and not rxfault
                and not txfault
            ):
                continue  # no optic / no DOM data on this port

            confounders = ["coverage_gated", "fault_flags_checked"]
            signals: list[str] = []
            evidence: dict[str, Any] = {}
            if rx is not None:
                evidence["sfp_rxpower_dbm"] = round(rx, 2)
                if rx <= rx_floor:
                    signals.append("rx_power_floor")
                    evidence["rx_power_floor_dbm"] = rx_floor
                band = _band(ctx, port.entity_id, "sfp_rxpower")
                if band is not None:
                    confounders.append("baseline_drift_checked")
                    drop = band.mean - rx
                    if drop >= drift_db:
                        signals.append("rx_power_drift")
                        evidence["rx_power_drop_db"] = round(drop, 2)
            if tx is not None:
                evidence["sfp_txpower_dbm"] = round(tx, 2)
                if tx <= tx_floor:
                    signals.append("tx_power_floor")
                    evidence["tx_power_floor_dbm"] = tx_floor
            if bias is not None:
                evidence["sfp_bias_current_ma"] = round(bias, 2)
                bias_band = _band(ctx, port.entity_id, "sfp_current")
                if bias_band is not None and bias_band.mean > 0:
                    confounders.append("bias_baseline_drift_checked")
                    rise_pct = (bias - bias_band.mean) / bias_band.mean * 100.0
                    evidence["bias_current_rise_pct"] = round(rise_pct, 1)
                    if rise_pct >= bias_drift_pct:
                        signals.append("bias_current_drift")
                        evidence["bias_drift_pct"] = bias_drift_pct
            if rxfault:
                signals.append("rx_fault")
            if txfault:
                signals.append("tx_fault")

            # Module temperature is judged last so the chassis confounder can see
            # whether anything else already fired.
            if module_temp is not None:
                evidence["sfp_temperature_c"] = round(module_temp, 1)
                if module_temp >= module_temp_max:
                    evidence["module_temp_max_c"] = module_temp_max
                    chassis = self._chassis_temp(ctx, port, window_s)
                    if chassis is not None:
                        confounders.append("chassis_temp_checked")
                        evidence["chassis_temp_c"] = round(chassis, 1)
                    if chassis is not None and chassis >= chassis_hot_c:
                        if not signals:
                            continue  # the host is hot; that is the finding, not this
                        evidence["module_temp_secondary_to_chassis"] = True
                    else:
                        signals.append("module_temp")
            if not signals:
                continue

            evidence["signals"] = signals
            severity = Severity.P3 if set(signals) <= self.DRIFT_SIGNALS else Severity.P2
            label = port.name or port.native_id
            findings.append(
                _finding(
                    self.key,
                    port,
                    severity,
                    f"SFP degraded on port {label}",
                    evidence,
                    confounders,
                )
            )
        return findings

    def _chassis_temp(self, ctx: Any, port: Entity, window_s: int) -> Optional[float]:
        """The host switch's latest chassis temperature, or ``None`` if unknown.

        A hot chassis explains a hot optic, so the module-temperature arm must not
        fire on its own in that case. ``None`` (no parent, no sensor, no series) is
        never read as hot: absence of data must not suppress a finding.
        """
        if port.parent_id is None:
            return None
        return _latest_gauge(ctx.window(port.parent_id, "temp", window_s))


__all__ = [
    "KEY_BAD_CABLE",
    "KEY_DUPLEX_MISMATCH",
    "KEY_PORT_FLAPPING",
    "KEY_UPLINK_SATURATION",
    "KEY_POE_BUDGET",
    "KEY_STP_LOOP",
    "KEY_BROADCAST_STORM",
    "KEY_SFP_DEGRADED",
    "BadCableDetector",
    "DuplexMismatchDetector",
    "PortFlappingDetector",
    "UplinkSaturationDetector",
    "PoeBudgetDetector",
    "StpLoopDetector",
    "BroadcastStormDetector",
    "SfpDegradedDetector",
]
