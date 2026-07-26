"""SLE user-minute accounting (ARCHITECTURE.md section 8, the health model).

A 5-minute-bucket job. For each **active** client it computes pass/fail minutes
per SLE (coverage, roaming, capacity, connect, wan) and, for each infrastructure
device, up/down minutes for the infra SLE. Every failed minute is attributed to
**exactly one** classifier (:mod:`netadmin.sle.classifiers`) and, where
determinable, one infrastructure entity (``attributed_entity_id`` — the AP, radio
or gateway the failure is pinned on). Rows are written to ``sle_minutes`` through
the repository; the health score and its explanation are then the same GROUP BY
over that table (:mod:`netadmin.sle.scores`).

The honest-by-construction property
-----------------------------------
"Active" means the client moved real traffic in the bucket: the summed delta of
its :attr:`SleConfig.activity_metrics` (default ``rx_bytes + tx_bytes``) reached
``activity_bytes_per_min`` × bucket minutes (default ~1 KB/min). An **idle** client
— even one sitting at a terrible RSSI — contributes **zero** minutes to every SLE,
so the score is impact-weighted by construction and a parked phone in a dead
corner never drags the number down. This is the whole point of the model and is
tested explicitly.

Minute arithmetic
-----------------
Per-sample SLEs (coverage, capacity) split a client's bucket minutes across the
samples actually present: ``minutes_per_sample = bucket_minutes /
max(n_samples, expected_samples)``. So a full bucket (~5 samples at the 60 s
cadence) totals the full 5 minutes, a half-covered bucket totals ~2.5 (honest
partial presence), and a dense/backfilled bucket is capped at 5. Each sample maps
to exactly one outcome (``ok`` or one failure classifier), so the per-SLE minutes
always sum to the client's exposed minutes and no minute is ever double-counted.
Per-bucket SLEs (roaming, connect, wan) assign the whole ``bucket_minutes`` to a
single classifier; infra integrates real down/up seconds from the device's state
timeline, so a state change mid-bucket splits the minutes at the transition.

Graceful no-op
--------------
Wired clients (no RSSI/radio samples) skip coverage/roaming/capacity. WAN is
evaluated once per bucket against the gateway; on a gateway-less site it fires
only where probe metrics (``gw_rtt``) exist and otherwise emits nothing, logging
the no-op once. Nothing is fabricated: absence of evidence produces no row, never
a zero-minute "ok".
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.logging import get_logger
from netadmin.sle.classifiers import (
    OK,
    SLE_CAPACITY,
    SLE_CONNECT,
    SLE_COVERAGE,
    SLE_INFRA,
    SLE_ROAMING,
    SLE_WAN,
    SleConfig,
    classify_capacity,
    classify_connect,
    classify_coverage,
    classify_roaming,
    classify_wan,
    infra_down_classifier,
)

_log = get_logger("sle.minutes")

__all__ = ["BucketResult", "SleMinutesJob", "bucket_of"]

# Event-key markers used to detect a (re)connection and roams within a bucket.
_CONNECTED_MARKER = "Connected"
_ROAM_MARKER = "Roam"
_LINK_LOCAL_PREFIX = "169.254."

# Infra restart-loop: this many down->up transitions inside one bucket flags a
# flapping/reboot-looping device rather than a cleanly-down one.
_RESTART_LOOP_CYCLES = 2


def bucket_of(ts: int, bucket_seconds: int = 300) -> int:
    """Start of the 5-minute bucket (UTC epoch seconds) containing ``ts``."""
    return ts - (ts % bucket_seconds)


def _percentile(values_sorted: list[float], q: float) -> float:
    """Linear-interpolated percentile of a pre-sorted list (``0 <= q <= 1``)."""
    n = len(values_sorted)
    if n == 1:
        return values_sorted[0]
    idx = q * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return values_sorted[lo] + (values_sorted[hi] - values_sorted[lo]) * frac


@dataclass
class BucketResult:
    """What one bucket computation produced — returned for tests/observability."""

    bucket_ts: int
    active_clients: int = 0
    rows_written: int = 0
    wan_evaluated: bool = False
    # (sle, classifier, entity_id) -> minutes, exactly as written this bucket.
    minutes: dict[tuple[str, str, int], float] = field(default_factory=dict)


@dataclass
class _Cell:
    """Accumulated minutes for one (sle, classifier, entity) with attribution.

    ``attributions`` tallies minutes per candidate ``attributed_entity_id`` so a
    client that roamed mid-bucket (samples pinned on two APs, same classifier)
    still writes one row, blamed on the entity that owned the most of the minutes.
    """

    minutes: float = 0.0
    attributions: dict[Optional[int], float] = field(default_factory=lambda: defaultdict(float))

    def add(self, minutes: float, attributed: Optional[int]) -> None:
        self.minutes += minutes
        self.attributions[attributed] += minutes

    @property
    def attributed_entity_id(self) -> Optional[int]:
        if not self.attributions:
            return None
        # Deterministic tie-break: most minutes, then lowest id (None sorts last).
        return max(
            self.attributions,
            key=lambda k: (self.attributions[k], -(k if k is not None else 1 << 62)),
        )


class SleMinutesJob:
    """Computes and persists ``sle_minutes`` for a 5-minute bucket.

    Construct once with a repository and (optionally) a
    :class:`~netadmin.detect.baseline.Baselines` for the 2σ-from-baseline tests;
    call :meth:`run_bucket` for one bucket or :meth:`run_range` to sweep a window.
    Reads go through the repository only (the store SQL seam); writes ride a single
    ``BEGIN IMMEDIATE`` per bucket via :meth:`Repository.transaction`.
    """

    def __init__(
        self,
        repo: Any,
        baselines: Any = None,
        *,
        settings: Any = None,
        config: Optional[SleConfig] = None,
    ) -> None:
        self.repo = repo
        self.baselines = baselines
        self._settings = settings
        self.cfg = config or SleConfig.from_settings(settings)
        self.site_id = getattr(repo, "site_id", "default")
        self._wan_noop_logged = False
        # per-run caches, keyed by bucket_ts so a range sweep does not restale them
        self._radio_cache: dict[int, dict[int, list[Entity]]] = {}
        self._entity_cache: dict[int, Entity] = {}

    # ------------------------------------------------------------------ #
    # Public entry points
    # ------------------------------------------------------------------ #
    def run_range(self, start_ts: int, end_ts: int) -> list[BucketResult]:
        """Compute every 5-minute bucket that starts in ``[start_ts, end_ts)``.

        ``start_ts`` is snapped down to its bucket boundary; buckets are processed
        oldest-first. Each bucket is independent, so a sample lands in exactly the
        bucket its timestamp falls in (the bucket-boundary math is the same
        :func:`bucket_of` the reads use).
        """
        b = self.cfg.bucket_seconds
        results: list[BucketResult] = []
        cur = bucket_of(int(start_ts), b)
        while cur < end_ts:
            results.append(self.run_bucket(cur))
            cur += b
        return results

    def run_bucket(self, bucket_ts: int) -> BucketResult:
        """Compute and persist all SLE minutes for one 5-minute bucket.

        ``bucket_ts`` is snapped to its bucket boundary. Returns a
        :class:`BucketResult`; the rows themselves are written to ``sle_minutes``.
        """
        b = self.cfg.bucket_seconds
        bucket_ts = bucket_of(int(bucket_ts), b)
        bucket_end = bucket_ts + b

        cells: dict[tuple[str, str, int], _Cell] = defaultdict(_Cell)

        # Shared per-bucket WAN judgement (attributed to the gateway), computed
        # once and applied to every active client below.
        wan_cls, wan_attr, wan_evaluable = self._wan_judgement(bucket_ts, bucket_end)

        result = BucketResult(bucket_ts=bucket_ts, wan_evaluated=wan_evaluable)

        for client in self.repo.list_entities(EntityType.CLIENT, site_id=self.site_id):
            cid = client["entity_id"]
            if cid is None:
                continue
            cid = int(cid)
            if not self._is_active(cid, bucket_ts, bucket_end):
                continue  # idle -> zero minutes across every SLE (the honest rule)
            result.active_clients += 1
            ap_id = self._client_ap_id(client)
            self._coverage(cells, cid, ap_id, bucket_ts, bucket_end)
            self._capacity(cells, cid, ap_id, bucket_ts, bucket_end)
            self._roaming(cells, cid, ap_id, bucket_ts, bucket_end)
            self._connect(cells, cid, ap_id, bucket_ts, bucket_end)
            if wan_evaluable:
                self._apply_wan(cells, cid, wan_cls, wan_attr)

        # Infra is device-keyed (not gated on client activity).
        for etype in (EntityType.AP, EntityType.SWITCH, EntityType.GATEWAY):
            for device in self.repo.list_entities(etype, site_id=self.site_id):
                self._infra(cells, device, etype, bucket_ts, bucket_end)

        result.rows_written = self._write(bucket_ts, cells, result)
        return result

    # ------------------------------------------------------------------ #
    # Activity gate
    # ------------------------------------------------------------------ #
    def _is_active(self, client_id: int, start: int, end: int) -> bool:
        """True when the client moved >= the activity floor of traffic this bucket.

        Sums the per-interval deltas of the configured activity metrics
        (``rx_bytes + tx_bytes`` by default) over the bucket. A client with no
        activity-metric series at all is treated as idle (we cannot confirm
        traffic), preserving the idle-zero property; a deployment without report
        byte counters should point ``activity_metrics`` at a live counter such as
        ``wifi_tx_attempts``.
        """
        threshold = self.cfg.activity_bytes_per_min * (self.cfg.bucket_seconds / 60.0)
        total = 0.0
        for metric in self.cfg.activity_metrics:
            for row in self._raw(client_id, metric, start, end):
                # Counter deltas; a negative (reset) delta is not real traffic.
                total += max(0.0, float(row["value"]))
        return total >= threshold

    # ------------------------------------------------------------------ #
    # Per-SLE evaluation
    # ------------------------------------------------------------------ #
    def _coverage(
        self, cells: dict, client_id: int, ap_id: Optional[int], start: int, end: int
    ) -> None:
        rssi_rows = self._raw(client_id, "rssi", start, end)
        if not rssi_rows:
            return  # wired / no signal -> coverage does not apply
        noise_by_ts = {
            int(r["ts"]): float(r["value"]) for r in self._raw(client_id, "noise", start, end)
        }
        per = self._minutes_per_sample(len(rssi_rows))
        for r in rssi_rows:
            rssi = float(r["value"])
            noise = noise_by_ts.get(int(r["ts"]))
            cls = classify_coverage(
                rssi,
                noise,
                weak_threshold_dbm=self.cfg.coverage_weak_dbm,
                snr_min_db=self.cfg.coverage_snr_min_db,
            )
            self._add(cells, SLE_COVERAGE, cls or OK, client_id, ap_id, per)

    def _capacity(
        self, cells: dict, client_id: int, ap_id: Optional[int], start: int, end: int
    ) -> None:
        radio = self._representative_radio(ap_id, start, end)
        if radio is None:
            return  # wired / no radio data -> capacity does not apply
        radio_id = int(radio["entity_id"])
        cu_rows = self._raw(radio_id, "cu_total", start, end)
        if not cu_rows:
            return
        self_rx = {
            int(r["ts"]): float(r["value"]) for r in self._raw(radio_id, "cu_self_rx", start, end)
        }
        self_tx = {
            int(r["ts"]): float(r["value"]) for r in self._raw(radio_id, "cu_self_tx", start, end)
        }
        band = self._band(radio_id, "cu_total", start)
        neighbor = self._neighbor_present(start, end)
        per = self._minutes_per_sample(len(cu_rows))
        for r in cu_rows:
            ts = int(r["ts"])
            cu_total = float(r["value"])
            cu_self = None
            if ts in self_rx or ts in self_tx:
                cu_self = self_rx.get(ts, 0.0) + self_tx.get(ts, 0.0)
            cls = classify_capacity(
                cu_total,
                cu_self,
                degraded_pct=self.cfg.capacity_degraded_pct,
                self_share_min=self.cfg.capacity_self_share_min,
                neighbor_present=neighbor,
                band=band,
                sigmas=self.cfg.sigmas,
            )
            self._add(cells, SLE_CAPACITY, cls or OK, client_id, radio_id, per)

    def _roaming(
        self, cells: dict, client_id: int, ap_id: Optional[int], start: int, end: int
    ) -> None:
        roam_delta = sum(
            max(0.0, float(r["value"])) for r in self._raw(client_id, "roam_count", start, end)
        )
        roam_events = [
            e
            for e in self.repo.read_events(start, end, entity_id=client_id)
            if _ROAM_MARKER in (e["key"] or "")
        ]
        roams = int(max(roam_delta, len(roam_events)))
        if roams <= 0:
            return  # no roam this bucket -> not a roaming judgement
        rssi_rows = self._raw(client_id, "rssi", start, end)
        rssis = [float(r["value"]) for r in rssi_rows]
        cls = classify_roaming(
            roams,
            min_rssi=min(rssis) if rssis else None,
            pre_rssi=rssis[0] if rssis else None,
            post_rssi=rssis[-1] if rssis else None,
            pingpong_count=self.cfg.roam_pingpong_count,
            sticky_rssi_dbm=self.cfg.sticky_rssi_dbm,
            slow_roam_degradation_db=self.cfg.slow_roam_degradation_db,
        )
        self._add(
            cells, SLE_ROAMING, cls or OK, client_id, ap_id, float(self.cfg.bucket_seconds) / 60.0
        )

    def _connect(
        self, cells: dict, client_id: int, ap_id: Optional[int], start: int, end: int
    ) -> None:
        events = self.repo.read_events(start, end, entity_id=client_id)
        connected = any(_CONNECTED_MARKER in (e["key"] or "") for e in events)
        failure_cls = self._connect_failure(events)
        ip = self._state_at(client_id, "ip", end)
        link_local = bool(ip) and str(ip).startswith(_LINK_LOCAL_PREFIX)
        # Exposure: only judge connect when a connection actually happened this
        # bucket (event or a self-assigned address), never for idle steady state.
        if not (connected or failure_cls or link_local):
            return
        cls = classify_connect(
            link_local_ip=link_local, failure_classifier=failure_cls, connected=connected
        )
        self._add(
            cells, SLE_CONNECT, cls or OK, client_id, ap_id, float(self.cfg.bucket_seconds) / 60.0
        )

    def _apply_wan(
        self, cells: dict, client_id: int, wan_cls: Optional[str], wan_attr: Optional[int]
    ) -> None:
        self._add(
            cells,
            SLE_WAN,
            wan_cls or OK,
            client_id,
            wan_attr,
            float(self.cfg.bucket_seconds) / 60.0,
        )

    def _infra(self, cells: dict, device: Any, etype: EntityType, start: int, end: int) -> None:
        did = device["entity_id"]
        if did is None:
            return
        did = int(did)
        down_s, up_s, cycles, bounced = self._state_timeline(did, start, end)
        if up_s > 0:
            self._add(cells, SLE_INFRA, OK, did, did, up_s / 60.0)
        if down_s > 0:
            restart = cycles >= _RESTART_LOOP_CYCLES or bounced
            cls = infra_down_classifier(etype, restart_loop=restart)
            self._add(cells, SLE_INFRA, cls, did, did, down_s / 60.0)

    # ------------------------------------------------------------------ #
    # WAN (shared, computed once per bucket)
    # ------------------------------------------------------------------ #
    def _wan_judgement(self, start: int, end: int) -> tuple[Optional[str], Optional[int], bool]:
        """Judge the shared uplink once. Returns (classifier|None, gw_id|None,
        evaluable). ``evaluable`` is False (and nothing is emitted) when there is
        no gateway entity or no WAN/probe data — the graceful gateway-less no-op.

        This mirrors the discipline of the ``wan.*`` detectors so the SLE cannot
        over-attribute fail-minutes to every client from ordinary consumer-router
        or Starlink-uplink behaviour (Finding 2):

        * ``wan_down`` requires a **sustained** majority of failed ``probe.gw_rtt``
          polls over a lookback window, not a single bucket where the prober
          hiccuped — otherwise one flaky bucket brands the WAN down for every
          active client at once.
        * ``isp_latency`` is judged on a **rolling-window p50** of the latency
          signal, not the single worst sample in the bucket. On a Starlink uplink,
          latency spikes to 80-120 ms at each ~15 s satellite handoff are normal;
          the bucket's *max* would brand every client isp_latency on one handoff,
          while a 15-minute median (which brief spikes barely move) only crosses
          the line when the elevation is genuinely sustained. The signal falls back
          from the UniFi ``wan_latency``/``www_latency`` to the probe ``gw_rtt_ms``,
          so the Starlink (gateway-less) site is judged at all.
        * ``bufferbloat`` requires a near-plan throughput reading (the load gate)
          before an RTT spike counts, and reads the loaded RTT as a robust p95, not
          the single worst ICMP sample. On a gateway-less site there is no
          throughput signal (and Starlink's wide throughput variance makes a fixed
          plan rate meaningless), so bufferbloat is simply not evaluated.
        """
        gws = self.repo.list_entities(EntityType.GATEWAY, site_id=self.site_id)
        gw = gws[0] if gws else None
        if gw is None:
            self._wan_noop_once("no gateway entity")
            return None, None, False
        gw_id = int(gw["entity_id"])

        latency, latency_metric = self._wan_latency_robust(gw_id, end)
        loss = self._max_sample(gw_id, ("wan_drops", "www_drops"), start, end)
        rtt_rows = self._raw(gw_id, "gw_rtt_ms", start, end)
        rtts = [float(r["value"]) for r in rtt_rows]

        # Sustained probe-failure accounting over a lookback window, not this bucket.
        lb_start = end - int(self.cfg.wan_down_window_s)
        polls = self.repo.read_poll_runs("probe.gw_rtt", lb_start, end)
        n_polls = len(polls)
        n_fail = sum(1 for r in polls if int(r["ok"]) == 0)

        # Internet-liveness comes from the PUBLIC DNS anchor (e.g. 1.1.1.1), not the
        # gateway RTT probe. The anchor resolving is direct proof the uplink is up.
        # The gw_rtt probe can be absent or failing for reasons that have nothing to
        # do with the WAN: no unprivileged ICMP inside a container, and a
        # consumer/Starlink router that serves no TCP port for the fallback to time.
        # Branding the WAN "down" off an absent ping while DNS resolves fine is the
        # bug this guards -- absence of one signal is never a failure when a better
        # signal is green.
        anchor_samples = self._raw(gw_id, "dns_anchor_latency_ms", lb_start, end)
        anchor_polls = self.repo.read_poll_runs("probe.dns.anchor", lb_start, end)
        internet_up = bool(anchor_samples) or any(int(r["ok"]) == 1 for r in anchor_polls)

        has_data = (
            bool(rtts)
            or latency is not None
            or loss is not None
            or n_polls > 0
            or bool(anchor_samples)
            or bool(anchor_polls)
        )
        if not has_data:
            self._wan_noop_once("no WAN or probe data")
            return None, gw_id, False

        # Down only when the internet is genuinely unreachable: the public DNS
        # anchor is NOT resolving AND the gateway RTT probe has a sustained majority
        # of failed polls with no successful RTT this bucket. A green anchor keeps
        # the WAN reachable no matter what gw_rtt does.
        sustained_down = (
            not internet_up
            and not rtts
            and n_polls >= int(self.cfg.wan_down_min_polls)
            and n_fail >= self.cfg.wan_down_fail_fraction * n_polls
        )
        reachable = not sustained_down

        # Bufferbloat load gate: only judge loaded-vs-idle RTT when WAN throughput
        # is confirmed near plan. Absent that (the gateway-less case), leave the RTT
        # inputs None so bufferbloat cannot fire on a bare ICMP spike.
        rtt_idle: Optional[float] = None
        rtt_loaded: Optional[float] = None
        if self._near_plan(gw_id, start, end):
            idle_band = self._band(gw_id, "gw_rtt_ms", start)
            rtt_idle = getattr(idle_band, "p50", None) if idle_band is not None else None
            if rtt_idle is None and rtts:
                rtt_idle = min(rtts)
            rtt_loaded = _percentile(sorted(rtts), 0.95) if rtts else None

        # Baseline for the same metric the robust p50 came from, so the trend test
        # in classify_wan compares like with like (gw_rtt vs gw_rtt on Starlink).
        latency_band = (
            self._band(gw_id, latency_metric, start) if latency_metric is not None else None
        )

        cls = classify_wan(
            reachable=reachable,
            loss=loss,
            latency_ms=latency,
            rtt_loaded_ms=rtt_loaded,
            rtt_idle_ms=rtt_idle,
            loss_threshold=self.cfg.wan_loss_threshold,
            latency_abs_ms=self.cfg.wan_latency_abs_ms,
            bufferbloat_ms=self.cfg.wan_bufferbloat_ms,
            latency_band=latency_band,
            sigmas=self.cfg.sigmas,
        )
        return cls, gw_id, True

    def _wan_latency_robust(self, gw_id: int, end: int) -> tuple[Optional[float], Optional[str]]:
        """A spike-robust WAN latency reading for the bucket, or ``(None, None)``.

        Returns the p50 of the latency signal over a trailing rolling window
        (``wan_latency_window_s``, default 15 min = 3 buckets), using the first
        source with enough samples to trust the median: UniFi ``wan_latency`` /
        ``www_latency`` if present, else the probe ``gw_rtt_ms`` (the only source on
        the gateway-less Starlink site). A window median — unlike the bucket's max —
        is barely moved by the brief 80-120 ms spikes Starlink emits at each ~15 s
        satellite handoff, so ``isp_latency`` fires only when the elevation is
        genuinely sustained. Too few samples (``wan_latency_min_samples``) returns
        ``None`` so a sparse bucket makes no latency claim.
        """
        window_s = int(self.cfg.wan_latency_window_s)
        min_samples = int(self.cfg.wan_latency_min_samples)
        start = end - window_s
        for metric in ("wan_latency", "www_latency", "gw_rtt_ms"):
            vals = [float(r["value"]) for r in self._raw(gw_id, metric, start, end)]
            if len(vals) >= min_samples:
                return _percentile(sorted(vals), 0.5), metric
        return None, None

    def _near_plan(self, gw_id: int, start: int, end: int) -> Optional[bool]:
        """Whether WAN throughput is near the provisioned plan rate this bucket.

        Returns True/False when it can be judged (a WAN xput series — UniFi gateway
        only — plus a configured plan rate in settings), else ``None`` (unknown, the
        gateway-less default). Mirrors ``wan.BufferbloatDetector._near_plan``: the
        load premise must be proven before an RTT spike is called bufferbloat.
        """
        settings = self._settings
        plan_down = getattr(settings, "wan_plan_down_mbps", None) if settings else None
        plan_up = getattr(settings, "wan_plan_up_mbps", None) if settings else None
        frac = float(self.cfg.wan_near_plan_fraction)
        down = [float(r["value"]) for r in self._raw(gw_id, "wan_xput_down", start, end)]
        up = [float(r["value"]) for r in self._raw(gw_id, "wan_xput_up", start, end)]
        judged = False
        if plan_down and down:
            judged = True
            if max(down) >= frac * float(plan_down):
                return True
        if plan_up and up:
            judged = True
            if max(up) >= frac * float(plan_up):
                return True
        return False if judged else None

    def _wan_noop_once(self, reason: str) -> None:
        if not self._wan_noop_logged:
            _log.info("SLE wan: no-op (%s); emitting no WAN minutes.", reason)
            self._wan_noop_logged = True

    # ------------------------------------------------------------------ #
    # Infra state timeline
    # ------------------------------------------------------------------ #
    def _state_timeline(
        self, device_id: int, start: int, end: int
    ) -> tuple[float, float, int, bool]:
        """Integrate down/up seconds over the bucket from the device's ``state``
        history. Returns (down_seconds, up_seconds, down_up_cycles, bounced).

        The state at the bucket start is the last ``state`` change before it; each
        change inside the bucket splits the interval at its timestamp. Unknown
        state (never recorded) is treated as up. ``bounced`` also covers a
        lost-contact/reconnect pair inside the bucket.
        """
        history = self.repo.state_history(device_id, "state", limit=10_000)
        changes = sorted(
            ((int(r["ts"]), r["new_value"]) for r in history if start <= int(r["ts"]) < end),
            key=lambda x: x[0],
        )
        before = [(int(r["ts"]), r["new_value"]) for r in history if int(r["ts"]) < start]
        cur_state = max(before, key=lambda x: x[0])[1] if before else None

        down_states = set(self.cfg.infra_down_states)

        def is_down(state: Any) -> bool:
            return state is not None and str(state) in down_states

        down_s = 0.0
        up_s = 0.0
        cycles = 0
        seg_start = start
        prev_down = is_down(cur_state)
        for ts, new_state in changes:
            span = ts - seg_start
            if prev_down:
                down_s += span
            else:
                up_s += span
            now_down = is_down(new_state)
            if prev_down and not now_down:
                cycles += 1  # a down -> up recovery
            prev_down = now_down
            seg_start = ts
        # final segment to bucket end
        span = end - seg_start
        if prev_down:
            down_s += span
        else:
            up_s += span

        bounced = self._had_bounce(device_id, start, end)
        return down_s, up_s, cycles, bounced

    def _had_bounce(self, device_id: int, start: int, end: int) -> bool:
        """A lost-contact followed by a reconnect inside the bucket (a flap)."""
        lost = False
        for e in self.repo.read_events(start, end, entity_id=device_id):
            key = e["key"] or ""
            if key.endswith("_Lost_Contact"):
                lost = True
            elif key.endswith("_Connected") and lost:
                return True
        return False

    # ------------------------------------------------------------------ #
    # Signal extraction helpers
    # ------------------------------------------------------------------ #
    def _raw(self, entity_id: int, metric: str, start: int, end: int) -> list:
        sid = self.repo.get_series(entity_id, metric)
        if sid is None:
            return []
        return self.repo.read_raw(sid, start, end)

    def _max_sample(
        self, entity_id: int, metrics: tuple[str, ...], start: int, end: int
    ) -> Optional[float]:
        best: Optional[float] = None
        for metric in metrics:
            for row in self._raw(entity_id, metric, start, end):
                v = float(row["value"])
                best = v if best is None else max(best, v)
        return best

    def _band(self, entity_id: int, metric: str, at_ts: int) -> Any:
        """The baseline band for a series, honouring hour-of-day for diurnal
        metrics. Returns None when no baselines engine is wired or the band is cold.
        """
        if self.baselines is None:
            return None
        sid = self.repo.get_series(entity_id, metric)
        if sid is None:
            return None
        from netadmin.detect.baseline import DIURNAL_METRICS, hour_label

        bucket = hour_label(at_ts) if metric in DIURNAL_METRICS else None
        band = self.baselines.band(sid, bucket=bucket)
        if band is None and bucket is not None:
            band = self.baselines.band(sid)  # fall back to the 'all' band
        return band

    def _minutes_per_sample(self, n_samples: int) -> float:
        """Per-sample minute weight: ``bucket_minutes / max(n, expected)``.

        Totals the full bucket minutes when the bucket is fully sampled, less when
        under-covered (honest partial presence), and caps at bucket minutes when
        over-sampled — so a client's per-SLE minutes never exceed the bucket.
        """
        bucket_minutes = self.cfg.bucket_seconds / 60.0
        expected = max(1, self.cfg.bucket_seconds // max(1, self.cfg.poll_cadence_s))
        return bucket_minutes / max(n_samples, expected)

    def _representative_radio(self, ap_id: Optional[int], start: int, end: int) -> Optional[Any]:
        """The AP radio the client's airtime is judged against: the busiest radio
        (highest mean ``cu_total`` in the bucket) among the AP's radios. Without a
        reliable per-client band we take the worst-case cell, which is the honest
        bottleneck. Returns None for a wired client or an AP with no radio data.
        """
        if ap_id is None:
            return None
        radios = self._ap_radios(ap_id, start)
        best = None
        best_mean = -1.0
        for radio in radios:
            rid = int(radio["entity_id"])
            rows = self._raw(rid, "cu_total", start, end)
            if not rows:
                continue
            mean = sum(float(r["value"]) for r in rows) / len(rows)
            if mean > best_mean:
                best_mean = mean
                best = radio
        return best

    def _ap_radios(self, ap_id: int, bucket_ts: int) -> list:
        cache = self._radio_cache.setdefault(bucket_ts, {})
        if ap_id in cache:
            return cache[ap_id]
        radios = [
            r
            for r in self.repo.list_entities(EntityType.RADIO, site_id=self.site_id)
            if r["parent_id"] is not None and int(r["parent_id"]) == ap_id
        ]
        cache[ap_id] = radios
        return radios

    def _client_ap_id(self, client: Any) -> Optional[int]:
        """The client's current point of attachment, if it is an AP. Coverage,
        roaming and connect pin their blame here; a wired client (switch parent)
        yields None and those wireless SLEs no-op for it.
        """
        parent_id = client["parent_id"]
        if parent_id is None:
            return None
        parent_id = int(parent_id)
        parent = self._entity(parent_id)
        if parent is None:
            return None
        if str(parent["entity_type"]) == EntityType.AP.value:
            return parent_id
        return None

    def _entity(self, entity_id: int) -> Any:
        if entity_id not in self._entity_cache:
            self._entity_cache[entity_id] = self.repo.get_entity(entity_id)
        return self._entity_cache[entity_id]

    def _neighbor_present(self, start: int, end: int) -> bool:
        """Whether a neighbouring/rogue BSS is known this window (rogue-AP events).

        Used to split capacity fail minutes into ``wifi_interference`` (a neighbour
        exists) vs ``non_wifi_util`` (unexplained). Best-effort: keyed off stored
        rogue/neighbour events; absent that signal, capacity attributes non-self
        airtime to non-Wi-Fi utilisation, never claiming a neighbour we cannot see.
        """
        for e in self.repo.read_events(start, end):
            key = (e["key"] or "").lower()
            if "rogue" in key or "neighbor" in key or "neighbour" in key:
                return True
        return False

    def _connect_failure(self, events: list) -> Optional[str]:
        """Map a bucket's events to a connect failure classifier, or None.

        Driven by ``SleConfig.connect_failure_keys`` (substring -> classifier),
        which is empty by default: classic controllers expose auth/assoc failures
        unevenly, so this is opt-in configuration rather than a guess. The strongest
        DHCP signal (a 169.254.x address) is handled by the caller, not here.
        """
        mapping = self.cfg.connect_failure_keys
        if not mapping:
            return None
        for e in events:
            key = e["key"] or ""
            for marker, cls in mapping.items():
                if marker in key:
                    return cls
        return None

    def _state_at(self, entity_id: int, attr: str, at_ts: int) -> Optional[str]:
        """The tracked attribute's value as of ``at_ts`` (latest change <= at_ts)."""
        history = self.repo.state_history(entity_id, attr, limit=10_000)
        best_ts = None
        best_val = None
        for r in history:
            ts = int(r["ts"])
            if ts <= at_ts and (best_ts is None or ts > best_ts):
                best_ts = ts
                best_val = r["new_value"]
        return best_val

    # ------------------------------------------------------------------ #
    # Accumulate + write
    # ------------------------------------------------------------------ #
    def _add(
        self,
        cells: dict,
        sle: str,
        classifier: str,
        entity_id: int,
        attributed: Optional[int],
        minutes: float,
    ) -> None:
        if minutes <= 0:
            return
        cells[(sle, classifier, entity_id)].add(minutes, attributed)

    def _write(self, bucket_ts: int, cells: dict, result: BucketResult) -> int:
        """Persist every accumulated cell as one ``sle_minutes`` row (idempotent
        upsert). One ``BEGIN IMMEDIATE`` for the whole bucket.
        """
        if not cells:
            return 0
        written = 0
        with self.repo.transaction():
            for (sle, classifier, entity_id), cell in cells.items():
                self.repo.upsert_sle_minute(
                    bucket_ts=bucket_ts,
                    sle=sle,
                    classifier=classifier,
                    entity_id=entity_id,
                    minutes=cell.minutes,
                    attributed_entity_id=cell.attributed_entity_id,
                )
                result.minutes[(sle, classifier, entity_id)] = cell.minutes
                written += 1
        return written
