"""The synthetic bad-week end-to-end test (ARCHITECTURE.md section 15).

Drives one week of synthetic, ingest-shaped writes through the *real* pipeline —
baselines -> detectors -> issue engine, plus the SLE minute accounting — and
asserts the whole spine behaves like a network admin that remembers:

* a **degrading cable** (rx_errors ramp + gigabit->100 downshift) opens
  ``wired.bad_cable`` (P2 access port), and its evidence carries the
  ``confounders_checked`` audit trail;
* a **flaky client** (reason-code disconnect storm) opens ``client.flaky`` (P3,
  attributed one-client/one-AP = device_or_deadspot);
* a **firmware regression** (upgrade event + post-upgrade disconnect surge) opens
  ``net.firmware_regression`` (P2 single device);
* an **airtime-saturated radio** (cu_total pinned at 85 %) opens
  ``wifi.airtime_saturation`` (P1 critical);
* the pending -> active transition honours M (a WINDOW issue is ``pending`` after
  one pass, ``active`` after three);
* the SLE minutes attribute the failed minutes to the right entities (coverage ->
  the AP, capacity -> the radio);
* a **healthy parallel device** (clean switch/port + AP/radio + client) raises
  **nothing** and its SLE minutes are all ``ok`` — the false-positive guard.

Everything is written through the repository seam exactly as the collector would;
counter metrics are stored with a GAUGE ``kind`` override so the exact per-interval
delta rows are controlled verbatim (the same convention the SLE suite uses).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from netadmin.config import Settings
from netadmin.detect.baseline import Baselines
from netadmin.detect.engine import build_detector_engine
from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType, IssueState, Severity
from netadmin.issues.engine import IssueEngine
from netadmin.issues.store_repository import StoreIssueRepository
from netadmin.sle.minutes import SleMinutesJob, bucket_of
from netadmin.store.metrics import MetricKind
from netadmin.store.repository import Repository, SampleReading

DAY = 86_400
# Bucket-aligned to the 5-minute SLE grid so "the last complete bucket" is clean.
NOW = 1_900_000_200
BUCKET = NOW - 300  # the last complete 5-minute bucket: [NOW-300, NOW)


# --------------------------------------------------------------------------- #
# tiny ingest-shaped write helpers
# --------------------------------------------------------------------------- #
def _entity(repo: Repository, etype: EntityType, native: str, **kw) -> int:
    return repo.upsert_entity(
        Entity(entity_type=etype, native_id=native, site_id="default", **kw), ts=NOW
    )


def _samples(repo: Repository, eid: int, metric: str, points, *, gauge: bool = True) -> None:
    """Write ``(ts, value)`` samples. ``gauge`` forces verbatim storage so counter
    metrics land as the exact delta rows given (matches tests/.../sle/conftest)."""
    kind = MetricKind.GAUGE if gauge else None
    repo.record_samples(SampleReading(eid, metric, ts, val, kind=kind) for ts, val in points)


def _disconnect(repo: Repository, *, ts, native, entity_id=None, related=None, reason=None) -> None:
    repo.record_event(
        ts=ts,
        key="EVT_WU_Disconnected",
        entity_id=entity_id,
        related_entity_id=related,
        native_id=native,
        data={"reason": reason} if reason is not None else {},
    )


# --------------------------------------------------------------------------- #
# the synthetic week
# --------------------------------------------------------------------------- #
class Site:
    """Handles onto every seeded entity id, so assertions can name them."""

    def __init__(self, repo: Repository) -> None:
        self.repo = repo
        # --- the failing plane ---
        self.sw_bad = _entity(repo, EntityType.SWITCH, "sw-bad", name="sw-bad")
        self.port_bad = _entity(
            repo,
            EntityType.PORT,
            "sw-bad:5",
            name="port5",
            parent_id=self.sw_bad,
            meta={"max_speed": 1000},
        )
        self.ap_sat = _entity(repo, EntityType.AP, "ap-sat", name="ap-sat")
        self.radio_sat = _entity(
            repo, EntityType.RADIO, "radio-sat", name="radio-sat", parent_id=self.ap_sat
        )
        self.fw_ap = _entity(repo, EntityType.AP, "fw-ap", name="fw-ap", model="U6-Pro")
        self.client_flaky = _entity(
            repo, EntityType.CLIENT, "client-flaky", name="client-flaky", parent_id=self.ap_sat
        )
        self.client_sle = _entity(
            repo, EntityType.CLIENT, "client-sle", name="client-sle", parent_id=self.ap_sat
        )
        # --- the healthy parallel plane (false-positive guard) ---
        self.sw_ok = _entity(repo, EntityType.SWITCH, "sw-ok", name="sw-ok")
        self.port_ok = _entity(
            repo,
            EntityType.PORT,
            "sw-ok:5",
            name="port5",
            parent_id=self.sw_ok,
            meta={"max_speed": 1000},
        )
        self.ap_ok = _entity(repo, EntityType.AP, "ap-ok", name="ap-ok")
        self.radio_ok = _entity(
            repo, EntityType.RADIO, "radio-ok", name="radio-ok", parent_id=self.ap_ok
        )
        self.client_ok = _entity(
            repo, EntityType.CLIENT, "client-ok", name="client-ok", parent_id=self.ap_ok
        )
        self.healthy_ids = {
            self.sw_ok,
            self.port_ok,
            self.ap_ok,
            self.radio_ok,
            self.client_ok,
        }

    def poll_accounting(self) -> None:
        """One live poll every 60 s for the last 25 h: coverage clears 0.5 for every
        gated detector, and the baseline fold has live hours to trust."""
        with self.repo.transaction():
            for job in ("fast_device", "fast_sta"):
                for ts in range(NOW - 25 * 3600, NOW + 1, 60):
                    self.repo.record_poll_run(job=job, ok=True, ts=ts)

    def degrading_cable(self) -> None:
        # rx_errors ramp: a sparse low history, a dense high tail in the last 15 min.
        self._ramp_history()
        _samples(
            self.repo,
            self.port_bad,
            "rx_errors",
            [(NOW - 60 * i, 60.0) for i in range(1, 15)],
        )
        # broken-pair downshift: gigabit-capable port negotiated down to 100 Mbps.
        self.repo.record_state_change(self.port_bad, "speed", "1000", ts=NOW - 5 * DAY)
        self.repo.record_state_change(self.port_bad, "speed", "100", ts=NOW - 3600)
        # healthy peer port: gigabit, no errors -> clean.
        self.repo.record_state_change(self.port_ok, "speed", "1000", ts=NOW - 5 * DAY)

    def _ramp_history(self) -> None:
        # A visibly rising week (older = fewer errors) — narrative, not load-bearing.
        pts = [(NOW - d * DAY, float(max(0, 60 - 10 * d))) for d in range(7, 1, -1)]
        _samples(self.repo, self.port_bad, "rx_errors", pts)

    def flaky_client(self) -> None:
        for i in range(6):
            _disconnect(
                self.repo,
                ts=NOW - 300 * (i + 1),
                native=f"flap-{i}",
                entity_id=self.client_flaky,
                related=self.ap_sat,
                reason=1,  # pathological reason code
            )

    def firmware_regression(self) -> None:
        up_ts = NOW - 2 * DAY
        self.repo.record_state_change(self.fw_ap, "firmware", "6.6.50", ts=NOW - 5 * DAY)
        self.repo.record_state_change(self.fw_ap, "firmware", "6.6.55", ts=up_ts)
        # one disconnect before the upgrade, a surge after (attributed to fw_ap).
        _disconnect(self.repo, ts=up_ts - 43_200, native="fw-pre", related=self.fw_ap, reason=4)
        for k in range(20):
            _disconnect(
                self.repo,
                ts=up_ts + 3600 + k * 3600,
                native=f"fw-post-{k}",
                related=self.fw_ap,
                reason=4,
            )

    def saturated_radio(self) -> None:
        _samples(
            self.repo,
            self.radio_sat,
            "cu_total",
            [(NOW - 60 * i, 85.0) for i in range(1, 15)],
        )
        # healthy radio: quiet channel.
        _samples(self.repo, self.radio_ok, "cu_total", [(NOW - 60 * i, 10.0) for i in range(1, 15)])

    def sle_activity(self) -> None:
        """Bucket-local signals for the SLE accounting: an active weak-RSSI client on
        the saturated AP, and an active good-RSSI client on the healthy AP."""
        # weak-coverage client on ap-sat (few samples -> below coverage_hole floor).
        _samples(
            self.repo, self.client_sle, "rssi", [(BUCKET + 40 + 60 * i, -80.0) for i in range(4)]
        )
        _samples(
            self.repo,
            self.client_sle,
            "rx_bytes",
            [(BUCKET + 60, 30_000.0), (BUCKET + 180, 30_000.0)],
        )
        self.repo.record_state_change(self.client_sle, "ip", "192.168.1.50", ts=NOW - 3600)
        # healthy client on ap-ok.
        _samples(self.repo, self.client_ok, "rssi", [(BUCKET + 60, -55.0), (BUCKET + 180, -55.0)])
        _samples(
            self.repo,
            self.client_ok,
            "rx_bytes",
            [(BUCKET + 60, 30_000.0), (BUCKET + 180, 30_000.0)],
        )
        self.repo.record_state_change(self.client_ok, "ip", "192.168.1.60", ts=NOW - 3600)


# --------------------------------------------------------------------------- #
# fixtures / harness
# --------------------------------------------------------------------------- #
@pytest.fixture
def settings() -> Settings:
    # Shrink the firmware compare windows so the coverage the poll accounting
    # provides (25 h) covers them; the classifier logic is unchanged.
    return Settings(
        _env_file=None,
        thresholds={
            "net.firmware_regression": {
                "lookback_s": 3 * DAY,
                "compare_window_s": DAY,
                "settle_s": 3600,
            }
        },
    )


@pytest.fixture
def harness(tmp_db_path: Path, settings: Settings):
    repo = Repository.open(tmp_db_path, site_id="default")
    site = Site(repo)
    site.poll_accounting()
    site.degrading_cable()
    site.flaky_client()
    site.firmware_regression()
    site.saturated_radio()
    site.sle_activity()

    issue_engine = IssueEngine(StoreIssueRepository(repo))
    baselines = Baselines.for_repository(repo)
    engine = build_detector_engine(repo, issue_engine, settings=settings, baselines=baselines)
    yield repo, site, baselines, engine
    repo.close()


def _open_issues_by_key(repo: Repository) -> dict[str, list]:
    """Open issues grouped by detector_key, each as a dict with decoded evidence."""
    grouped: dict[str, list] = {}
    for row in repo.list_issues(open_only=True):
        d = dict(row)
        d["_evidence"] = json.loads(d.get("evidence") or "{}")
        grouped.setdefault(d["detector_key"], []).append(d)
    return grouped


def _one(rows: list, entity_id: int):
    matches = [r for r in rows if r["entity_id"] == entity_id]
    assert (
        len(matches) == 1
    ), f"expected exactly one issue for entity {entity_id}, got {len(matches)}"
    return matches[0]


# --------------------------------------------------------------------------- #
# the test
# --------------------------------------------------------------------------- #
def test_bad_week_opens_the_right_issues(harness) -> None:
    repo, site, baselines, engine = harness

    # 1) baselines fold (exercises the baseline wiring the daemon runs at 5 min).
    baselines.update_from_recent(NOW)

    # 2) the FAST tier finds nothing wrong (no down devices, controller reachable).
    fast = engine.run_fast(NOW)
    assert fast.findings == []

    # 3) WINDOW tier, pass 1: the three window issues are created PENDING (M honoured).
    engine.run_window(NOW)
    after_one = _open_issues_by_key(repo)
    cable_pending = _one(after_one["wired.bad_cable"], site.port_bad)
    assert cable_pending["state"] == IssueState.PENDING.value  # not yet confirmed

    # ... two more passes cross M=3 -> ACTIVE.
    engine.run_window(NOW)
    engine.run_window(NOW)

    # 4) DAILY tier x3 -> the firmware regression confirms to ACTIVE.
    for _ in range(3):
        engine.run_daily(NOW)

    issues = _open_issues_by_key(repo)

    # --- the degrading cable ---
    cable = _one(issues["wired.bad_cable"], site.port_bad)
    assert cable["severity"] == Severity.P2.value  # access port, not an uplink
    assert cable["state"] == IssueState.ACTIVE.value
    trail = cable["_evidence"]["confounders_checked"]
    assert "coverage_gated" in trail
    assert "counter_reset_handled" in trail
    assert "port_gigabit_capable" in trail  # the downshift confounder was tested
    assert set(cable["_evidence"]["signals"]) == {"error_rate", "speed_downshift"}

    # --- the flaky client ---
    flaky = _one(issues["client.flaky"], site.client_flaky)
    assert flaky["severity"] == Severity.P3.value
    assert flaky["state"] == IssueState.ACTIVE.value
    assert flaky["_evidence"]["attribution"] == "device_or_deadspot"

    # --- the firmware regression ---
    fw = _one(issues["net.firmware_regression"], site.fw_ap)
    assert fw["severity"] == Severity.P2.value  # single device, not fleet-wide
    assert fw["state"] == IssueState.ACTIVE.value
    assert fw["_evidence"]["version"] == "6.6.55"
    assert (
        fw["_evidence"]["post_disconnects_per_hour"] > fw["_evidence"]["pre_disconnects_per_hour"]
    )

    # --- the airtime-saturated radio ---
    air = _one(issues["wifi.airtime_saturation"], site.radio_sat)
    assert air["severity"] == Severity.P1.value  # critical
    assert air["state"] == IssueState.ACTIVE.value
    assert air["_evidence"]["level"] == "critical"

    # --- exactly these four, nothing else ---
    assert sum(len(v) for v in issues.values()) == 4

    # --- false-positive guard: nothing pinned on any healthy entity ---
    for rows in issues.values():
        for row in rows:
            assert row["entity_id"] not in site.healthy_ids


def test_bad_week_sle_attribution(harness) -> None:
    repo, site, baselines, engine = harness
    baselines.update_from_recent(NOW)

    job = SleMinutesJob(repo, baselines, settings=None)
    result = job.run_bucket(BUCKET)
    assert result.bucket_ts == bucket_of(NOW) - 300

    rows = repo.query_sle_minutes(
        BUCKET,
        BUCKET + 300,
        group_by=("sle", "classifier", "entity_id", "attributed_entity_id"),
    )
    index = {(r["sle"], r["classifier"], r["entity_id"]): r for r in rows}

    # coverage weak-signal minutes for the weak client attribute to its AP.
    cov = index[("coverage", "weak_signal", site.client_sle)]
    assert cov["attributed_entity_id"] == site.ap_sat
    assert cov["minutes"] > 0

    # capacity fail minutes for that client attribute to the saturated RADIO.
    cap = index[("capacity", "non_wifi_util", site.client_sle)]
    assert cap["attributed_entity_id"] == site.radio_sat
    assert cap["minutes"] > 0

    # false-positive guard: the healthy client has no failing SLE minute.
    healthy_fail = [r for r in rows if r["entity_id"] == site.client_ok and r["classifier"] != "ok"]
    assert healthy_fail == []
    # ...and it did contribute real ok minutes (it was active, not idle).
    healthy_ok = [r for r in rows if r["entity_id"] == site.client_ok and r["classifier"] == "ok"]
    assert healthy_ok, "healthy active client should book ok minutes"
