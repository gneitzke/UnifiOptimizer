"""Network-wide structural detectors (section 6, ``net.*``).

Two detectors that reason across many entities at once rather than one:

* :class:`CoverageHoleDetector` (``net.coverage_hole``) — a Cisco-CHD-style
  per-AP client-RSSI histogram. An AP is a coverage hole when its attached
  clients read weak (histogram p25 < −75 dBm, or > 20 % of client-samples
  < −80 dBm) **and** those weak clients have no better AP anywhere in their
  history — the check that separates a genuine dead spot from a sticky client
  that simply refuses to roam to the strong AP it can reach.
* :class:`FirmwareRegressionDetector` (``net.firmware_regression``) — a
  change-point around a firmware upgrade. For each device it compares the 7 days
  before the upgrade against the 7 days after (skipping the first 2 h of
  post-upgrade settling) on disconnects-per-hour and port-error rate; a
  regression that hits several devices of the **same model + version** escalates
  from P2 to P1 as a fleet-wide bad build.

Both gate on the coverage that feeds them — ``fast_sta`` for the RSSI histogram,
``fast_device`` for the post-upgrade window — and return ``UNKNOWN`` rather than
draw a conclusion through a collection gap.
"""

from __future__ import annotations

from typing import Any, Optional

from netadmin.detect.engine import COVERAGE_MIN, UNKNOWN, DetectorResult, EvalResult
from netadmin.domain.entities import Entity, Finding
from netadmin.domain.types import Cadence, EntityType, Severity
from netadmin.logging import get_logger

_log = get_logger("detect.net")

KEY_COVERAGE_HOLE = "net.coverage_hole"
KEY_FIRMWARE_REGRESSION = "net.firmware_regression"

DEFAULT_DISCONNECT_KEYS: tuple[str, ...] = ("EVT_WU_Disconnected", "EVT_LU_Disconnected")

# Port error metrics whose per-window totals stand in for "port errors".
_PORT_ERROR_METRICS: tuple[str, ...] = ("rx_errors", "tx_errors")


def _percentile(values_sorted: list[float], q: float) -> float:
    n = len(values_sorted)
    if n == 1:
        return values_sorted[0]
    idx = q * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return values_sorted[lo] + (values_sorted[hi] - values_sorted[lo]) * frac


def _window_values(ctx: Any, entity_id: int, metric: str, seconds: int) -> list[float]:
    wr = ctx.window(entity_id, metric, seconds)
    if wr is None:
        return []
    out: list[float] = []
    for row in wr.rows:
        val = row.get("value")
        if val is not None:
            out.append(float(val))
    return out


# ====================================================================== #
# net.coverage_hole
# ====================================================================== #
class CoverageHoleDetector:
    """``net.coverage_hole`` — per-AP weak-client histogram + no-better-AP gate.

    For each AP, the RSSI samples of its currently attached clients form a
    histogram. The AP is flagged when that histogram is weak (p25 below the weak
    line, or a large fraction below the very-weak line) **and** the weak clients
    are stuck there — their best RSSI *anywhere* in recorded history is also weak,
    proving no better AP exists for them. A client that has seen strong RSSI
    elsewhere is a sticky/roaming problem (a different detector), not a hole, and
    is excluded here.
    """

    key = KEY_COVERAGE_HOLE
    scope = EntityType.AP
    cadence = Cadence.WINDOW

    def evaluate(self, ctx: Any) -> EvalResult:
        window_s = int(ctx.threshold(self.key, "window_s", 21600))  # 6 h client-hours
        if ctx.coverage(window_s, "fast_sta") < COVERAGE_MIN:
            return UNKNOWN

        weak_dbm = float(ctx.threshold(self.key, "weak_p25_dbm", -75.0))
        very_weak_dbm = float(ctx.threshold(self.key, "very_weak_dbm", -80.0))
        very_weak_frac = float(ctx.threshold(self.key, "very_weak_fraction", 0.20))
        better_dbm = float(ctx.threshold(self.key, "better_ap_dbm", -70.0))
        history_s = int(ctx.threshold(self.key, "history_s", 604800))  # 7 d best-RSSI lookback
        min_samples = int(ctx.threshold(self.key, "min_samples", 20))

        clients_by_ap: dict[Any, list[Entity]] = {}
        for client in ctx.entities(EntityType.CLIENT):
            if client.entity_id is None or client.parent_id is None:
                continue
            clients_by_ap.setdefault(client.parent_id, []).append(client)

        findings: list[Finding] = []
        unknown: set[int] = set()
        for ap in ctx.entities(EntityType.AP):
            if ap.entity_id is None:
                continue
            finding, ap_unknown = self._evaluate_ap(
                ctx,
                ap,
                clients_by_ap.get(ap.entity_id, []),
                window_s=window_s,
                weak_dbm=weak_dbm,
                very_weak_dbm=very_weak_dbm,
                very_weak_frac=very_weak_frac,
                better_dbm=better_dbm,
                history_s=history_s,
                min_samples=min_samples,
            )
            if ap_unknown:
                # Too little client-signal on this AP this window to judge coverage
                # -> freeze any open hole issue on it, never clear it by absence.
                unknown.add(ap.entity_id)
            elif finding is not None:
                findings.append(finding)
        return DetectorResult.of(findings, unknown)

    def _evaluate_ap(
        self,
        ctx: Any,
        ap: Entity,
        clients: list[Entity],
        *,
        window_s: int,
        weak_dbm: float,
        very_weak_dbm: float,
        very_weak_frac: float,
        better_dbm: float,
        history_s: int,
        min_samples: int,
    ) -> tuple[Optional[Finding], bool]:
        """Return ``(finding_or_None, unknown)``. ``unknown`` is True only when the
        AP had too few client-RSSI samples to judge (a per-AP coverage gap); a
        weak-but-not-a-hole or no-stuck-clients outcome returns ``(None, False)`` —
        a genuine clear.
        """
        all_rssi: list[float] = []
        per_client: dict[int, list[float]] = {}
        for client in clients:
            vals = _window_values(ctx, client.entity_id, "rssi", window_s)
            if vals:
                per_client[client.entity_id] = vals
                all_rssi.extend(vals)

        if len(all_rssi) < min_samples:
            return None, True  # too little client-signal on this AP -> UNKNOWN

        ordered = sorted(all_rssi)
        p25 = _percentile(ordered, 0.25)
        very_weak_share = sum(1 for v in ordered if v < very_weak_dbm) / len(ordered)
        histogram_weak = p25 < weak_dbm or very_weak_share > very_weak_frac
        if not histogram_weak:
            return None, False  # evaluated: signal is fine -> a clear

        # No-better-AP gate: keep only weak clients whose *best ever* RSSI is also
        # weak. A client with strong RSSI somewhere in history can reach a better
        # AP -> sticky client, not a coverage hole.
        stuck: list[Entity] = []
        for client in clients:
            vals = per_client.get(client.entity_id)
            if not vals or _percentile(sorted(vals), 0.5) >= weak_dbm:
                continue
            best_ever = self._best_ever_rssi(ctx, client.entity_id, history_s)
            if best_ever is not None and best_ever < better_dbm:
                stuck.append(client)

        if not stuck:
            return None, False  # weak histogram but everyone can reach a better AP -> sticky

        label = ap.name or ap.native_id
        finding = Finding(
            detector_key=self.key,
            entity=ap,
            severity=Severity.P2,
            title=f"Coverage hole at {label} ({len(stuck)} stuck clients)",
            dims={},
            evidence={
                "client_rssi_p25": round(p25, 1),
                "very_weak_share": round(very_weak_share, 2),
                "weak_line_dbm": weak_dbm,
                "very_weak_line_dbm": very_weak_dbm,
                "stuck_clients": len(stuck),
                "sample_count": len(ordered),
            },
            confounders_checked=[
                "no_better_ap_in_history_gate",
                "sticky_client_excluded",
                "min_samples_required",
                "sta_coverage_gated",
            ],
        )
        return finding, False

    def _best_ever_rssi(self, ctx: Any, client_id: int, history_s: int) -> Optional[float]:
        """A client's best (highest) RSSI over the history window; baseline p95 else max."""
        series_id = ctx.repo.get_series(client_id, "rssi")
        if series_id is not None:
            band = ctx.baselines.band(series_id)
            if band is not None:
                return band.p95
        vals = _window_values(ctx, client_id, "rssi", history_s)
        return max(vals) if vals else None


# ====================================================================== #
# net.firmware_regression
# ====================================================================== #
class FirmwareRegressionDetector:
    """``net.firmware_regression`` — pre/post-upgrade change-point per device.

    For each AP/switch with a firmware change in the lookback, the window splits at
    the upgrade: [upgrade−7d, upgrade] vs [upgrade+2h, upgrade+7d]. Disconnects-per-
    hour (events attributed to the device) and port-error rate are compared; a
    post-upgrade rate above ``regression_factor × pre`` (and above a floor) is a
    regression. When several devices of the **same model** regressed on the **same
    firmware version**, it is a bad build and escalates to P1.
    """

    key = KEY_FIRMWARE_REGRESSION
    scope = EntityType.AP
    cadence = Cadence.DAILY

    def evaluate(self, ctx: Any) -> EvalResult:
        lookback_s = int(ctx.threshold(self.key, "lookback_s", 604800))  # 7 d
        compare_s = int(ctx.threshold(self.key, "compare_window_s", 604800))  # 7 d each side
        settle_s = int(ctx.threshold(self.key, "settle_s", 7200))  # first 2 h excluded
        factor = float(ctx.threshold(self.key, "regression_factor", 1.5))
        min_post_per_hour = float(ctx.threshold(self.key, "min_post_disconnects_per_hour", 0.5))
        fleet_min = int(ctx.threshold(self.key, "fleet_min", 2))

        # Gate on the post-upgrade window's live coverage (the recent side).
        if ctx.coverage(compare_s, "fast_device") < COVERAGE_MIN:
            return UNKNOWN

        devices = [
            d
            for etype in (EntityType.AP, EntityType.SWITCH)
            for d in ctx.entities(etype)
            if d.entity_id is not None
        ]

        # Per-device regression assessment.
        regressed: list[dict[str, Any]] = []
        for device in devices:
            upgrade = self._latest_upgrade(ctx, device, lookback_s, settle_s)
            if upgrade is None:
                continue
            result = self._assess(
                ctx, device, upgrade, compare_s, settle_s, factor, min_post_per_hour
            )
            if result is not None:
                regressed.append(result)

        if not regressed:
            return []

        # Fleet correlation: same model + same new firmware version across devices.
        fleet_counts: dict[tuple[str, str], int] = {}
        for r in regressed:
            key = (r["model"], r["version"])
            fleet_counts[key] = fleet_counts.get(key, 0) + 1

        findings: list[Finding] = []
        for r in regressed:
            fleet = fleet_counts[(r["model"], r["version"])]
            fleet_wide = fleet >= fleet_min
            severity = Severity.P1 if fleet_wide else Severity.P2
            device: Entity = r["device"]
            label = device.name or device.native_id
            confounders = [
                "settle_window_excluded_2h",
                "pre_post_same_device_baseline",
                "device_coverage_gated",
            ]
            if fleet_wide:
                confounders.append("same_model_version_fleet_correlated")
            findings.append(
                Finding(
                    detector_key=self.key,
                    entity=device,
                    severity=severity,
                    title=f"Firmware regression on {label} (v{r['version']})",
                    dims={},
                    evidence={
                        "version": r["version"],
                        "model": r["model"],
                        "upgrade_ts": r["upgrade_ts"],
                        "pre_disconnects_per_hour": round(r["pre_rate"], 3),
                        "post_disconnects_per_hour": round(r["post_rate"], 3),
                        "pre_port_errors": round(r["pre_errors"], 1),
                        "post_port_errors": round(r["post_errors"], 1),
                        "fleet_devices_regressed": fleet,
                        "fleet_wide": fleet_wide,
                    },
                    confounders_checked=confounders,
                )
            )
        return findings

    def _latest_upgrade(
        self, ctx: Any, device: Entity, lookback_s: int, settle_s: int
    ) -> Optional[dict[str, Any]]:
        """The most recent firmware change within lookback and past its settle time."""
        floor = ctx.now_ts - lookback_s
        history = ctx.repo.state_history(device.entity_id, "firmware", limit=50)
        for row in history:  # newest-first
            ts = int(row["ts"])
            new_value = row["new_value"]
            old_value = row["old_value"]
            if ts < floor:
                break
            if old_value is None or new_value is None or old_value == new_value:
                continue  # first-ever record is not an upgrade
            if ctx.now_ts - ts < settle_s:
                continue  # still inside the settle window; too early to judge
            return {"ts": ts, "version": str(new_value)}
        return None

    def _assess(
        self,
        ctx: Any,
        device: Entity,
        upgrade: dict[str, Any],
        compare_s: int,
        settle_s: int,
        factor: float,
        min_post_per_hour: float,
    ) -> Optional[dict[str, Any]]:
        up_ts = upgrade["ts"]
        pre_start = up_ts - compare_s
        post_start = up_ts + settle_s
        post_end = min(ctx.now_ts, up_ts + compare_s)
        if post_end <= post_start:
            return None

        pre_rate = self._disconnects_per_hour(ctx, device, pre_start, up_ts)
        post_rate = self._disconnects_per_hour(ctx, device, post_start, post_end)
        pre_errors = self._port_errors(ctx, device, pre_start, up_ts)
        post_errors = self._port_errors(ctx, device, post_start, post_end)

        disc_regressed = post_rate >= min_post_per_hour and post_rate > factor * max(pre_rate, 1e-9)
        err_regressed = post_errors > factor * max(pre_errors, 1e-9) and post_errors > 0
        if not (disc_regressed or err_regressed):
            return None
        return {
            "device": device,
            "model": device.model or "unknown",
            "version": upgrade["version"],
            "upgrade_ts": up_ts,
            "pre_rate": pre_rate,
            "post_rate": post_rate,
            "pre_errors": pre_errors,
            "post_errors": post_errors,
        }

    def _disconnects_per_hour(self, ctx: Any, device: Entity, start: int, end: int) -> float:
        """Disconnect events attributed to this device (as related AP) per hour."""
        if end <= start:
            return 0.0
        # Events attributed to the device sit in related_entity_id for client
        # disconnects; query the whole window and count those pointing at us.
        rows = ctx.events(keys=set(DEFAULT_DISCONNECT_KEYS), since_ts=start)
        count = 0
        for row in rows:
            ts = int(_row_val(row, "ts") or 0)
            if ts < start or ts >= end:
                continue
            related = _row_val(row, "related_entity_id")
            if related is not None and int(related) == device.entity_id:
                count += 1
        hours = (end - start) / 3600.0
        return count / hours if hours > 0 else 0.0

    def _port_errors(self, ctx: Any, device: Entity, start: int, end: int) -> float:
        """Sum of rx/tx error deltas across this device's ports in the window."""
        total = 0.0
        seconds = end - start
        if seconds <= 0:
            return 0.0
        for port in ctx.entities(EntityType.PORT):
            if port.entity_id is None or port.parent_id != device.entity_id:
                continue
            for metric in _PORT_ERROR_METRICS:
                series_id = ctx.repo.get_series(port.entity_id, metric)
                if series_id is None:
                    continue
                wr = ctx.repo.read_window(series_id, start, end, now=ctx.now_ts)
                for row in wr.rows:
                    val = row.get("sum", row.get("value"))
                    if val is not None:
                        total += float(val)
        return total


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _row_val(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


__all__ = [
    "KEY_COVERAGE_HOLE",
    "KEY_FIRMWARE_REGRESSION",
    "CoverageHoleDetector",
    "FirmwareRegressionDetector",
]
