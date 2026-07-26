"""Infrastructure liveness and health detectors (section 6, ``infra.*``).

Two P1 detectors anchor the inhibition tree the issue engine already knows:

* :class:`ControllerDownDetector` (``infra.controller_down``) — the controller is
  unreachable, inferred from a run of consecutive fast-job poll failures. It is a
  ``global`` inhibition source: while it is active the issue engine freezes every
  other detector's issues (absence of evidence is not evidence of absence). Being
  the coverage detector itself, it never gates on coverage — a coverage gap is its
  fire condition, not a reason to abstain.

* :class:`DeviceDownDetector` (``infra.device_down``) — an infrastructure device
  (ap / switch / gateway) is offline, from its recorded ``state``, a
  ``*_Lost_Contact`` event with no later reconnect, or a stale ``last_seen`` whose
  last recorded state is *not* online (a stale-only trigger against an online or
  transitional state is a collection/sync gap, not a downed device, and is
  suppressed). It is a ``children`` inhibition source (a downed switch freezes its
  ports' issues). Clients are **not** devices — they are out of scope here (their
  flakiness is ``client.*``).

Both run on the FAST tier. ``device_down`` gates on ``fast_device`` coverage: below
0.5 it returns ``UNKNOWN`` rather than guess at device state through a gap; the
total-outage case is additionally covered by ``controller_down`` inhibition.

A third detector watches thermals on the WINDOW tier:

* :class:`DeviceOverheatingDetector` (``infra.device_overheating``) — a chassis
  running hot, from the controller's own ``overheating`` flag (P1), a temperature
  sustained at or above the critical tier across the window (P2), or a sustained
  rise above the device's own temperature baseline (P3, the dust/failing-fan
  creep). It only inspects hardware that actually reports a sensor: UniFi APs set
  ``has_temperature: false`` and carry no thermal data at all, so the detector
  reads that capability flag from entity meta rather than treating an absent
  reading as a cool device.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from netadmin.detect.engine import COVERAGE_MIN, UNKNOWN, EvalResult
from netadmin.domain.entities import Entity, Finding
from netadmin.domain.types import Cadence, EntityType, Severity
from netadmin.logging import get_logger

_log = get_logger("detect.infra")

KEY_CONTROLLER_DOWN = "infra.controller_down"
KEY_DEVICE_DOWN = "infra.device_down"
KEY_DEVICE_OVERHEATING = "infra.device_overheating"

# Infrastructure device types device_down iterates. Clients are excluded by design.
DEVICE_TYPES: tuple[EntityType, ...] = (EntityType.AP, EntityType.SWITCH, EntityType.GATEWAY)

# UniFi ``device.state`` codes, recorded as strings in ``state_changes``:
#   1 = connected/online, 0 = disconnected/offline,
#   2 = pending adoption, 4 = upgrading, 5 = provisioning (transitional).
DEFAULT_ONLINE_STATES: tuple[str, ...] = ("1",)
DEFAULT_DOWN_STATES: tuple[str, ...] = ("0",)
DEFAULT_TRANSITIONAL_STATES: tuple[str, ...] = ("2", "4", "5")

_LOST_CONTACT_SUFFIX = "_Lost_Contact"
_RECONNECT_SUFFIX = "_Connected"


class ControllerDownDetector:
    """``infra.controller_down`` — consecutive fast-job poll failures -> P1.

    Reads the tail of ``poll_runs`` for the primary fast job and counts the run of
    trailing failures. At or above the threshold the controller is treated as
    unreachable and a P1 finding fires; a recent success (streak below threshold)
    is a clear evaluation, which lets the engine advance the issue's clear streak
    and eventually lift the global freeze.
    """

    key = KEY_CONTROLLER_DOWN
    scope = EntityType.GATEWAY  # site-edge / controller marker (no real gateway needed)
    cadence = Cadence.FAST

    def evaluate(self, ctx: Any) -> EvalResult:
        job = str(ctx.threshold(self.key, "job", "fast_device"))
        threshold = int(ctx.threshold(self.key, "consecutive_failures", 3))
        window_s = int(ctx.threshold(self.key, "window_s", 3600))

        start_ts = ctx.now_ts - window_s
        runs = ctx.repo.read_poll_runs(job, start_ts, ctx.now_ts + 1)

        streak = 0
        last_error: Optional[str] = None
        last_fail_ts: Optional[int] = None
        for row in reversed(runs):
            if int(row["ok"]) == 0:
                streak += 1
                if last_fail_ts is None:
                    last_fail_ts = int(row["ts"])
                    last_error = row["error"]
            else:
                break

        if streak < threshold:
            return []  # clear: controller reachable (or not yet a sustained outage)

        entity = _controller_entity(ctx.site_id)
        evidence = {
            "consecutive_failures": streak,
            "threshold": threshold,
            "job": job,
            "window_s": window_s,
            "last_failure_ts": last_fail_ts,
            "last_error": last_error,
        }
        return [
            Finding(
                detector_key=self.key,
                entity=entity,
                severity=Severity.P1,
                title=f"Controller unreachable: {streak} consecutive {job} poll failures",
                dims={},
                evidence=evidence,
                confounders_checked=[
                    "requires_consecutive_streak",  # one blip never fires
                    "not_gated_on_coverage",  # this detector IS the coverage signal
                ],
            )
        ]


class DeviceDownDetector:
    """``infra.device_down`` — an infra device is offline -> P1 (per device).

    Down when any of: the latest recorded ``state`` is an offline code; a
    ``*_Lost_Contact`` event is the most recent lifecycle event (no later
    reconnect); or ``last_seen`` has gone stale **while the last recorded state is
    not online**. A stale-only trigger on a device the controller still reports as
    online (or one in a transitional adopting / upgrading / provisioning state) is
    suppressed: that silence is a collection/inventory-sync gap — a controller that
    drops the device from ``stat/device`` for a few cycles while poll_runs stay
    ok=1 — not a downed device. A real outage flips the state to an offline code or
    emits a ``*_Lost_Contact`` event, both of which still fire. Returns ``UNKNOWN``
    when coverage is too thin to trust device state.
    """

    key = KEY_DEVICE_DOWN
    scope = EntityType.SWITCH  # representative infra device; iterates all DEVICE_TYPES
    cadence = Cadence.FAST

    def evaluate(self, ctx: Any) -> EvalResult:
        coverage_window_s = int(ctx.threshold(self.key, "coverage_window_s", 600))
        coverage_job = str(ctx.threshold(self.key, "coverage_job", "fast_device"))
        if ctx.coverage(coverage_window_s, coverage_job) < COVERAGE_MIN:
            return UNKNOWN  # cannot trust device state through a collection gap

        down_states = _as_str_set(ctx.threshold(self.key, "down_states", DEFAULT_DOWN_STATES))
        online_states = _as_str_set(ctx.threshold(self.key, "online_states", DEFAULT_ONLINE_STATES))
        transitional_states = _as_str_set(
            ctx.threshold(self.key, "transitional_states", DEFAULT_TRANSITIONAL_STATES)
        )
        stale_s = int(ctx.threshold(self.key, "stale_last_seen_s", 300))
        event_window_s = int(ctx.threshold(self.key, "event_window_s", 3600))

        findings: list[Finding] = []
        for etype in DEVICE_TYPES:
            for entity in ctx.entities(etype):
                if entity.entity_id is None:
                    continue
                evidence = self._down_evidence(
                    ctx,
                    entity,
                    down_states=down_states,
                    online_states=online_states,
                    transitional_states=transitional_states,
                    stale_s=stale_s,
                    event_window_s=event_window_s,
                )
                if evidence is None:
                    continue
                findings.append(self._finding(entity, evidence))
        return findings

    def _down_evidence(
        self,
        ctx: Any,
        entity: Entity,
        *,
        down_states: set[str],
        online_states: set[str],
        transitional_states: set[str],
        stale_s: int,
        event_window_s: int,
    ) -> Optional[dict[str, Any]]:
        state = ctx.repo.current_state(entity.entity_id, "state")
        state_str = None if state is None else str(state)
        state_offline = state_str is not None and state_str in down_states

        since_ts = ctx.now_ts - event_window_s
        events = ctx.events(entity_id=entity.entity_id, since_ts=since_ts)
        lost_active, last_lost_ts = _lost_contact_active(events)

        last_seen = entity.last_seen_ts
        seconds_since_seen = None if last_seen is None else ctx.now_ts - last_seen
        stale = seconds_since_seen is not None and seconds_since_seen > stale_s

        triggers: list[str] = []
        if state_offline:
            triggers.append("state_offline")
        if lost_active:
            triggers.append("lost_contact")
        if stale:
            triggers.append("stale_last_seen")

        if not triggers:
            return None

        # A stale-only trigger is suppressed when the device's last recorded state
        # is online (or transitional). A device the controller still reports as up
        # but that has merely dropped out of recent stat/device responses — a known
        # UniFi provisioning/restart quirk that empties the device list for a few
        # cycles while poll_runs stay ok=1, so controller_down never trips to
        # inhibit — is a collection gap, NOT a downed device. Firing P1 on staleness
        # alone would false-positive every AP/switch at once and freeze every child
        # issue beneath them. A genuine outage flips state to an offline code or
        # emits a *_Lost_Contact event; both of those still fire here.
        if (
            triggers == ["stale_last_seen"]
            and state_str is not None
            and (state_str in transitional_states or state_str in online_states)
        ):
            return None

        return {
            "state": state_str,
            "triggers": triggers,
            "last_seen_ts": last_seen,
            "seconds_since_seen": seconds_since_seen,
            "last_lost_contact_ts": last_lost_ts,
        }

    def _finding(self, entity: Entity, evidence: dict[str, Any]) -> Finding:
        label = entity.name or entity.native_id
        return Finding(
            detector_key=self.key,
            entity=entity,
            severity=Severity.P1,
            title=f"{entity.entity_type.value} {label} is down",
            dims={},  # one issue per device; native_id already anchors the fingerprint
            evidence=evidence,
            confounders_checked=[
                "controller_reachable_gated",  # UNKNOWN below coverage floor
                "stale_only_requires_non_online_state",  # online/transitional stale = sync gap
                "recent_reconnect_excluded",  # a later *_Connected clears the lost event
            ],
        )


@dataclass
class _Thermal:
    """One device's thermal picture for this evaluation window."""

    entity: Entity
    latest_c: Optional[float] = None
    max_c: Optional[float] = None
    min_c: Optional[float] = None
    overheating: Optional[bool] = None
    has_fan: Optional[bool] = None
    fan_level: Optional[float] = None
    uptime_s: Optional[float] = None
    baseline_mean_c: Optional[float] = None

    @property
    def has_signal(self) -> bool:
        return self.latest_c is not None or self.overheating is not None


class DeviceOverheatingDetector:
    """``infra.device_overheating`` — a chassis running hot, on three arms.

    * **Controller flag** (``overheating`` true) -> P1. This is the controller's
      own authoritative verdict and precedes a throttle or a thermal shutdown, so
      it fires on its own and is never suppressed by the reboot check.
    * **Sustained critical temperature** (every sample in the window at or above
      ``crit_c``) -> P2. A single hot sample is a spike, not a fault.
    * **Baseline drift** (latest at least ``drift_c`` above the device's own
      temperature baseline mean) -> P3. This is the slow creep of a dust-clogged
      vent or a fan losing speed, which never trips an absolute limit until the
      day it does.

    Only hardware that reports a sensor is inspected: a device whose ingest meta
    says ``has_temperature: false`` (every UniFi AP) is skipped rather than judged
    on missing data. Confounders: warm ambient air (two or more sensors all warm
    at once suppresses the drift arm), a post-reboot transient (fresh uptime
    suppresses the temperature arms), and fan state (``has_fan`` with
    ``fan_level`` recorded as evidence, since hot-with-a-stopped-fan is a much
    stronger finding than hot alone).
    """

    key = KEY_DEVICE_OVERHEATING
    scope = EntityType.SWITCH  # representative infra device; iterates DEVICE_TYPES
    cadence = Cadence.WINDOW

    def evaluate(self, ctx: Any) -> EvalResult:
        coverage_window_s = int(ctx.threshold(self.key, "coverage_window_s", 600))
        coverage_job = str(ctx.threshold(self.key, "coverage_job", "fast_device"))
        if ctx.coverage(coverage_window_s, coverage_job) < COVERAGE_MIN:
            return UNKNOWN  # a collection gap is not a cool chassis

        window_s = int(ctx.threshold(self.key, "window_s", 900))
        warn_c = float(ctx.threshold(self.key, "warn_c", 80.0))
        crit_c = float(ctx.threshold(self.key, "crit_c", 90.0))
        drift_c = float(ctx.threshold(self.key, "drift_c", 8.0))
        min_uptime_s = int(ctx.threshold(self.key, "min_uptime_s", 600))

        readings = [self._read(ctx, entity, window_s) for entity in self._candidates(ctx)]
        measured = [r for r in readings if r.latest_c is not None]

        # Ambient check: when every temperature-capable sensor on the site is warm
        # at the same time, the room is warm -- that is one environmental fact, not
        # N independently failing devices. It suppresses the drift arm (which is
        # exactly what a warm room looks like) and is only meaningful with two or
        # more sensors; a single-sensor site cannot separate ambient from device.
        multi_sensor = len(measured) >= 2
        ambient = multi_sensor and all(float(r.latest_c) >= warn_c for r in measured)

        findings: list[Finding] = []
        for reading in readings:
            finding = self._assess(
                reading,
                warn_c=warn_c,
                crit_c=crit_c,
                drift_c=drift_c,
                min_uptime_s=min_uptime_s,
                window_s=window_s,
                multi_sensor=multi_sensor,
                ambient=ambient,
            )
            if finding is not None:
                findings.append(finding)
        return findings

    # ------------------------------------------------------------------ #
    def _candidates(self, ctx: Any) -> list[Entity]:
        """Infra devices that are not explicitly sensor-less.

        ``has_temperature: false`` in meta is a hardware fact from the controller
        and rules the device out. An absent flag is *unknown*, not *false*, so the
        device stays in the pool and simply produces no finding when it turns out
        to have no readings either.
        """
        out: list[Entity] = []
        for etype in DEVICE_TYPES:
            for entity in ctx.entities(etype):
                if entity.entity_id is None:
                    continue
                if entity.meta.get("has_temperature") is False:
                    continue
                out.append(entity)
        return out

    def _read(self, ctx: Any, entity: Entity, window_s: int) -> _Thermal:
        reading = _Thermal(entity=entity)
        reading.overheating = _as_bool(ctx.repo.current_state(entity.entity_id, "overheating"))
        reading.has_fan = _as_bool(entity.meta.get("has_fan"))

        window = ctx.window(entity.entity_id, "temp", window_s)
        values = _window_values(window)
        if values:
            reading.latest_c = values[-1]
            reading.max_c = max(values)
            reading.min_c = min(values)

        reading.fan_level = _latest_value(ctx.window(entity.entity_id, "fan_level", window_s))
        reading.uptime_s = _latest_value(ctx.window(entity.entity_id, "uptime", window_s))

        series_id = ctx.repo.get_series(entity.entity_id, "temp")
        if series_id is not None:
            band = ctx.baselines.band(series_id)
            if band is not None:
                reading.baseline_mean_c = float(band.mean)
        return reading

    def _assess(
        self,
        reading: _Thermal,
        *,
        warn_c: float,
        crit_c: float,
        drift_c: float,
        min_uptime_s: int,
        window_s: int,
        multi_sensor: bool,
        ambient: bool,
    ) -> Optional[Finding]:
        if not reading.has_signal:
            return None

        confounders = ["coverage_gated", "temperature_capability_checked"]
        confounders.append(
            "ambient_checked" if multi_sensor else "ambient_check_skipped_single_sensor"
        )

        # A device that has just booted is still settling; its temperature series
        # covers a boot transient rather than steady-state operation. This gates
        # the measured arms only -- the controller's own overheating flag is
        # authoritative and fires regardless.
        recent_reboot = reading.uptime_s is not None and reading.uptime_s < min_uptime_s
        if reading.uptime_s is not None:
            confounders.append("recent_reboot_checked")

        evidence: dict[str, Any] = {"window_s": window_s, "warn_c": warn_c, "crit_c": crit_c}
        signals: list[str] = []

        if reading.overheating:
            signals.append("controller_overheating_flag")
            evidence["overheating"] = True

        if reading.latest_c is not None:
            evidence["temp_c"] = round(reading.latest_c, 1)
            evidence["max_temp_c"] = round(float(reading.max_c), 1)
            evidence["min_temp_c"] = round(float(reading.min_c), 1)
            if not recent_reboot and float(reading.min_c) >= crit_c:
                signals.append("sustained_critical_temp")
            if reading.baseline_mean_c is not None:
                evidence["baseline_mean_c"] = round(reading.baseline_mean_c, 1)
                rise = reading.latest_c - reading.baseline_mean_c
                evidence["baseline_rise_c"] = round(rise, 1)
                evidence["drift_c"] = drift_c
                if not recent_reboot and not ambient and rise >= drift_c:
                    signals.append("baseline_drift")

        if recent_reboot:
            evidence["recent_reboot_uptime_s"] = int(reading.uptime_s)
        if ambient:
            evidence["ambient_warm"] = True

        # Fan state is evidence, never a trigger on its own: a chassis sitting hot
        # with its fan reported at level 0 points squarely at a dead fan.
        if reading.has_fan:
            confounders.append("fan_checked")
            evidence["has_fan"] = True
            if reading.fan_level is not None:
                evidence["fan_level"] = reading.fan_level
                if signals and reading.fan_level <= 0:
                    evidence["fan_stopped_while_hot"] = True

        if not signals:
            return None

        evidence["signals"] = signals
        if "controller_overheating_flag" in signals:
            severity = Severity.P1
        elif "sustained_critical_temp" in signals:
            severity = Severity.P2
        else:
            severity = Severity.P3

        label = reading.entity.name or reading.entity.native_id
        title = f"{label} overheating"
        if reading.latest_c is not None:
            title = f"{title} ({reading.latest_c:.0f} °C)"
        return Finding(
            detector_key=self.key,
            entity=reading.entity,
            severity=severity,
            title=title,
            dims={},  # one issue per device
            evidence=evidence,
            confounders_checked=confounders,
        )


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #
def _controller_entity(site_id: str) -> Entity:
    """A synthetic, unpersisted entity standing in for the controller.

    ``entity_id`` stays ``None`` (there is no controller row); the stable
    ``native_id`` gives ``infra.controller_down`` one fingerprint per site. Typed
    ``GATEWAY`` only as the nearest site-edge marker — no real gateway is required
    (and this site has none).
    """
    return Entity(
        entity_type=EntityType.GATEWAY,
        native_id=f"controller:{site_id}",
        site_id=site_id,
        entity_id=None,
        name="controller",
    )


def _lost_contact_active(events: Any) -> tuple[bool, Optional[int]]:
    """Whether a ``*_Lost_Contact`` is the newest lifecycle event (unreconnected).

    ``events`` are oldest-first. Latest-wins between the last lost-contact and the
    last reconnect (``*_Connected``); a reconnect at or after the lost event clears
    it. Returns ``(active, last_lost_ts)``.
    """
    last_lost: Optional[int] = None
    last_reconnect: Optional[int] = None
    for row in events:
        key = row["key"] or ""
        ts = int(row["ts"])
        if key.endswith(_LOST_CONTACT_SUFFIX):
            last_lost = ts
        elif key.endswith(_RECONNECT_SUFFIX):
            last_reconnect = ts
    if last_lost is None:
        return False, None
    if last_reconnect is not None and last_reconnect >= last_lost:
        return False, last_lost
    return True, last_lost


def _as_str_set(values: Any) -> set[str]:
    """Coerce a threshold value (list/tuple/scalar) to a set of strings."""
    if isinstance(values, (list, tuple, set, frozenset)):
        return {str(v) for v in values}
    return {str(values)}


def _as_bool(value: Any) -> Optional[bool]:
    """Coerce a stored state / meta scalar to bool (``current_state`` returns str)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no"):
        return False
    return None


def _window_values(window: Any) -> list[float]:
    """Every gauge reading in a window, oldest-first.

    Raw rows carry ``value``; rollup rows alias ``value`` to the bucket average
    and also carry ``last``. Both are legitimate readings of a gauge, so ``value``
    wins and ``last`` is the fallback (the same convention the wired detectors use).
    """
    if window is None or not window.rows:
        return []
    values: list[float] = []
    for row in window.rows:
        raw = row.get("value")
        if raw is None:
            raw = row.get("last")
        if raw is not None:
            values.append(float(raw))
    return values


def _latest_value(window: Any) -> Optional[float]:
    """The newest gauge value in a window, or ``None`` when it has no rows."""
    values = _window_values(window)
    return values[-1] if values else None


__all__ = [
    "KEY_CONTROLLER_DOWN",
    "KEY_DEVICE_DOWN",
    "KEY_DEVICE_OVERHEATING",
    "DEVICE_TYPES",
    "ControllerDownDetector",
    "DeviceDownDetector",
    "DeviceOverheatingDetector",
]
