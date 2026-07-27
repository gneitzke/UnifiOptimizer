"""Deterministic demo-dataset generator (``netadmin demo-seed``).

Writes a fully-populated demo SQLite database for a **fictional** small
home/prosumer network -- realistic enough for screenshots and a live demo, with
none of the owner's real data. Every value here is fabricated:

* MACs come from the locally-administered ``02:...`` space (the U/L bit is set on
  the first octet, so they can never collide with a real vendor OUI).
* IPs come only from the RFC 5737 documentation ranges ``192.0.2.0/24`` and
  ``198.51.100.0/24`` -- never ``192.168.x`` / ``10.x`` / ``172.16.x``.
* Names are generic (rooms: Living Room, Office, ...; devices: Laptop, iPhone,
  Thermostat, ...). No personal hostnames or place names.

The generator is **pure data**: it opens a fresh database through
:class:`netadmin.store.repository.Repository` and writes through the repository
seam exactly as ingest would, so the result is schema-valid by construction and
the rollups are maintained at write time. It never touches a controller, the
network, or MQTT.

Determinism: all randomness is seeded from :data:`DEMO_SEED`, and all timestamps
are anchored to :data:`DEFAULT_NOW` (a fixed baseline, overridable via ``now``)
rather than the wall clock, so regenerating the demo is stable. A live demo that
wants "current" timestamps passes ``now=<current epoch>``; the default stays
fixed so a committed/screenshotted demo does not churn.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType, FixState, IssueState, Severity
from netadmin.issues.models import EventKind
from netadmin.sle.classifiers import (
    CLS_CLIENT_LOAD,
    CLS_DHCP,
    CLS_ISP_LATENCY,
    CLS_NON_WIFI_UTIL,
    CLS_PINGPONG,
    CLS_RESTART_LOOP,
    CLS_STICKY,
    CLS_WEAK_SIGNAL,
    OK,
    SLE_CAPACITY,
    SLE_CONNECT,
    SLE_COVERAGE,
    SLE_INFRA,
    SLE_ROAMING,
    SLE_WAN,
)
from netadmin.store.metrics import MetricKind
from netadmin.store.repository import Repository, SampleReading

__all__ = [
    "seed_demo",
    "DEFAULT_NOW",
    "DEMO_SEED",
    "DEFAULT_HISTORY_DAYS",
    "DemoStats",
]

# --------------------------------------------------------------------------- #
# Fixed anchors (never the wall clock, so regenerated demos are stable)
# --------------------------------------------------------------------------- #
DEMO_SEED = 20260722
# 2030-01-07 12:00:00 UTC. Aligned to the 300 s SLE grid and to the UTC day so
# rollups and 5-minute buckets fall on clean boundaries.
DEFAULT_NOW = 1_894_017_600
# >= 8 days so the report's fixed 7-day window (assembler.py DEFAULT_WINDOW_S)
# always sits fully inside the seeded data -- a settled, fully-observed week
# rather than a window that runs off the front of a freshly-started demo.
DEFAULT_HISTORY_DAYS = 8

SITE_ID = "default"
SSID_MAIN = "DemoNet"
SSID_IOT = "DemoNet-IoT"

MINUTE = 60
HOUR = 3600
DAY = 86_400

# Bulk history is sampled every 15 min (smooth enough for a 6-day chart, cheap to
# store); issue-evidence series get a denser recent tail so the detail charts read
# as live data.
BASE_STEP = 900
FINE_STEP = 300

# The default demo DB filename; the real production DB name is refused as an out
# target so a demo-seed can never clobber a live install.
DEFAULT_OUT = "data/netadmin-demo.db"
_PROTECTED_BASENAMES = frozenset({"netadmin.db"})


@dataclass
class DemoStats:
    """Row counts of the generated demo database (returned by :func:`seed_demo`)."""

    db_path: str
    now: int
    entities_total: int = 0
    entities_by_type: dict[str, int] = field(default_factory=dict)
    series: int = 0
    samples: int = 0
    events: int = 0
    poll_runs: int = 0
    issues_total: int = 0
    issues_by_state: dict[str, int] = field(default_factory=dict)
    issues_by_severity: dict[str, int] = field(default_factory=dict)
    issue_events: int = 0
    changes: int = 0
    investigations: int = 0
    sle_minutes: int = 0
    baselines: int = 0
    sle_headline: Optional[float] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "db_path": self.db_path,
            "now": self.now,
            "entities": {"total": self.entities_total, "by_type": dict(self.entities_by_type)},
            "series": self.series,
            "samples": self.samples,
            "events": self.events,
            "poll_runs": self.poll_runs,
            "issues": {
                "total": self.issues_total,
                "by_state": dict(self.issues_by_state),
                "by_severity": dict(self.issues_by_severity),
            },
            "issue_events": self.issue_events,
            "changes": self.changes,
            "investigations": self.investigations,
            "sle_minutes": self.sle_minutes,
            "baselines": self.baselines,
            "sle_headline": self.sle_headline,
        }


# --------------------------------------------------------------------------- #
# Small pure helpers
# --------------------------------------------------------------------------- #
def _fingerprint(detector_key: str, native_id: str, dims: dict[str, str]) -> str:
    """``sha1(detector_key | site | native_id | sorted(dims))`` -- the same shape
    the issue engine computes, so seeded issues carry a realistic fingerprint."""
    parts = [detector_key, SITE_ID, native_id]
    for key in sorted(dims):
        parts.append(f"{key}={dims[key]}")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class _MacPool:
    """Deterministic locally-administered MAC allocator (first octet ``02``)."""

    def __init__(self) -> None:
        self._n = 0

    def next(self, group: int) -> str:
        self._n += 1
        n = self._n
        return "02:{:02x}:{:02x}:{:02x}:{:02x}:{:02x}".format(
            group & 0xFF,
            (n >> 16) & 0xFF,
            (n >> 8) & 0xFF,
            n & 0xFF,
            (group * 7 + 0x11) & 0xFF,
        )


# Device inventory specs -------------------------------------------------- #
_AP_SPECS: tuple[dict[str, Any], ...] = (
    {"name": "Living Room", "model": "U6-Pro", "ng": 6, "na": 44, "mesh": False},
    {"name": "Office", "model": "U6-Pro", "ng": 11, "na": 36, "mesh": False},
    {"name": "Kitchen", "model": "U6-Lite", "ng": 11, "na": 149, "mesh": False},
    {"name": "Garage", "model": "U6-Lite", "ng": 1, "na": 157, "mesh": False},
    {"name": "Primary Bedroom", "model": "U6-Pro", "ng": 11, "na": 36, "mesh": False},
    {"name": "Basement", "model": "U6-Lite", "ng": 1, "na": 44, "mesh": False},
    {"name": "Back Porch", "model": "U6-Mesh", "ng": 6, "na": 149, "mesh": True},
    {"name": "Studio", "model": "UAP-AC-Pro", "ng": 6, "na": 36, "mesh": False},
)

_SWITCH_SPECS: tuple[dict[str, Any], ...] = (
    {"name": "Office Switch", "model": "USW-24-PoE", "poe_budget": 95.0},
    {"name": "Rack Switch", "model": "USW-Lite-8-PoE", "poe_budget": 52.0},
    {"name": "Studio Switch", "model": "USW-Flex-Mini", "poe_budget": None},
)

# (name, wired?, iot?) for the client roster (padded to ~48 below).
_CLIENT_BASE: tuple[tuple[str, bool, bool], ...] = (
    ("Laptop", False, False),
    ("Work Laptop", False, False),
    ("iPhone", False, False),
    ("iPad", False, False),
    ("Android Phone", False, False),
    ("Smart TV", False, False),
    ("Media Player", False, False),
    ("Game Console", False, False),
    ("eReader", False, False),
    ("Guest Phone", False, False),
    ("Office Printer", False, True),
    ("Front Door Camera", False, True),
    ("Backyard Camera", False, True),
    ("Doorbell", False, True),
    ("Thermostat", False, True),
    ("Smart Speaker", False, True),
    ("Robot Vacuum", False, True),
    ("Garage Opener", False, True),
    ("Weather Station", False, True),
    ("Baby Monitor", False, True),
    ("Office Desktop", True, False),
    ("NAS", True, False),
    ("Home Server", True, False),
)


# --------------------------------------------------------------------------- #
# The generator
# --------------------------------------------------------------------------- #
class _Seeder:
    """Builds one deterministic demo database against an open repository."""

    def __init__(self, repo: Repository, *, now: int, seed: int, history_days: int) -> None:
        self.repo = repo
        self.now = now
        self.rng = random.Random(seed)
        self.history_days = history_days
        self.start = now - history_days * DAY
        self.macs = _MacPool()

        # Two deliberate collection gaps so the UI's gap rendering has something to
        # draw (no samples + no poll_runs in these windows).
        self.gaps: tuple[tuple[int, int], ...] = (
            (now - 3 * DAY - 30 * MINUTE, now - 3 * DAY),
            (now - 26 * HOUR, now - 26 * HOUR + 40 * MINUTE),
        )

        # Base sample grid (gaps removed), reused for every bulk series.
        self.grid = [ts for ts in range(self.start, now, BASE_STEP) if not self._in_gap(ts)]

        # Handles filled during inventory build, referenced by issues/SLE.
        self.gw_id: int = 0
        self.gw_mac: str = ""
        self.aps: dict[str, dict[str, Any]] = {}
        self.switches: dict[str, dict[str, Any]] = {}
        self.clients: list[dict[str, Any]] = []
        self.bad_cable_port_id: int = 0
        self.bad_cable_port_nid: str = ""
        self.flap_port_id: int = 0
        self.flap_port_nid: str = ""
        self.duplex_port_id: int = 0
        self.duplex_port_nid: str = ""
        self.sticky_client: dict[str, Any] = {}
        self.pingpong_client: dict[str, Any] = {}
        self.flaky_client: dict[str, Any] = {}
        self._series_units: dict[str, str] = {}

    # -- time helpers -------------------------------------------------------- #
    def _in_gap(self, ts: int) -> bool:
        return any(lo <= ts < hi for lo, hi in self.gaps)

    @staticmethod
    def _hour(ts: int) -> float:
        return (ts % DAY) / HOUR

    def _diurnal(self, ts: int, low: float, high: float, peak_hour: float = 20.0) -> float:
        frac = 0.5 + 0.5 * math.cos(2 * math.pi * (self._hour(ts) - peak_hour) / 24.0)
        return low + (high - low) * frac

    def _jit(self, spread: float) -> float:
        return self.rng.uniform(-spread, spread)

    # -- inventory ----------------------------------------------------------- #
    def _entity(
        self,
        etype: EntityType,
        native_id: str,
        *,
        name: Optional[str] = None,
        model: Optional[str] = None,
        parent_id: Optional[int] = None,
        meta: Optional[dict[str, Any]] = None,
        first_seen: Optional[int] = None,
    ) -> int:
        ent = Entity(
            entity_type=etype,
            native_id=native_id,
            site_id=SITE_ID,
            name=name,
            model=model,
            parent_id=parent_id,
            meta=meta or {},
            first_seen_ts=first_seen or self.start,
            last_seen_ts=self.now,
        )
        return self.repo.upsert_entity(ent, ts=self.now)

    def _state(self, eid: int, attr: str, value: Any, ts: int) -> None:
        self.repo.record_state_change(eid, attr, value, ts=ts)

    def build_inventory(self) -> None:
        with self.repo.transaction():
            self._build_gateway()
            self._build_switches()
            self._build_aps()
            self._build_clients()

    def _build_gateway(self) -> None:
        mac = self.macs.next(0x6B)
        self.gw_mac = mac
        gid = self._entity(
            EntityType.GATEWAY,
            mac,
            name="Gateway",
            model="UDM-Pro",
            meta={"unifi_type": "udm", "ip": "198.51.100.1"},
        )
        self.gw_id = gid
        self._state(gid, "firmware", "3.2.12", self.start)
        self._state(gid, "state", "1", self.start)
        self._state(gid, "uplink_type", "wire", self.start)

    def _build_switches(self) -> None:
        for i, spec in enumerate(_SWITCH_SPECS):
            mac = self.macs.next(0x5C)
            meta: dict[str, Any] = {"unifi_type": "usw"}
            if spec["poe_budget"] is not None:
                meta["total_max_power"] = spec["poe_budget"]
            sid = self._entity(
                EntityType.SWITCH, mac, name=spec["name"], model=spec["model"], meta=meta
            )
            self._state(sid, "firmware", "6.6.55", self.start)
            self._state(sid, "state", "1", self.start)
            ports = self._build_ports(sid, mac, spec, index=i)
            self.switches[spec["name"]] = {
                "id": sid,
                "mac": mac,
                "budget": spec["poe_budget"],
                "ports": ports,
            }

    def _build_ports(
        self, sid: int, sw_mac: str, spec: dict[str, Any], *, index: int
    ) -> list[dict[str, Any]]:
        ports: list[dict[str, Any]] = []
        # Every switch has an uplink port (idx 1) plus a few access ports.
        layout = [(1, True), (2, True), (3, False), (4, False), (5, False), (6, False)]
        for idx, is_uplink in layout:
            nid = f"{sw_mac}:{idx}"
            meta = {
                "media": "GE",
                "is_uplink": is_uplink,
                "max_speed": 1000,
                "speed_caps": 0x3E,  # advertises up to 1000-full
            }
            pid = self._entity(EntityType.PORT, nid, name=f"Port {idx}", parent_id=sid, meta=meta)
            self._state(pid, "up", "true", self.start)
            self._state(pid, "full_duplex", "true", self.start)
            self._state(pid, "speed", "1000", self.start)
            ports.append({"id": pid, "nid": nid, "idx": idx, "is_uplink": is_uplink})

        # Wire the specific fault ports on the first two switches.
        if index == 0:  # Office Switch
            self.flap_port_id = ports[1]["id"]  # idx 2, uplink -> Garage AP
            self.flap_port_nid = ports[1]["nid"]
            self.bad_cable_port_id = ports[5]["id"]  # idx 6, access -> Office Desktop
            self.bad_cable_port_nid = ports[5]["nid"]
        if index == 1:  # Rack Switch
            self.duplex_port_id = ports[3]["id"]  # idx 4, access
            self.duplex_port_nid = ports[3]["nid"]
        return ports

    def _build_aps(self) -> None:
        for spec in _AP_SPECS:
            mac = self.macs.next(0xA9)
            uplink = "wireless" if spec["mesh"] else "wire"
            aid = self._entity(
                EntityType.AP,
                mac,
                name=spec["name"],
                model=spec["model"],
                meta={"unifi_type": "uap", "mesh": spec["mesh"], "ip": self._infra_ip()},
            )
            fw = "5.43.56" if spec["model"] == "UAP-AC-Pro" else "6.6.55"
            self._state(aid, "firmware", fw, self.start)
            self._state(aid, "state", "1", self.start)
            self._state(aid, "uplink_type", uplink, self.start)
            radios = {}
            for band, chan in (("ng", spec["ng"]), ("na", spec["na"])):
                rnid = f"{mac}:{band}"
                ht = 20 if band == "ng" else 80
                rid = self._entity(
                    EntityType.RADIO,
                    rnid,
                    name=f"{spec['name']} {'2.4G' if band == 'ng' else '5G'}",
                    parent_id=aid,
                    meta={"band": band, "ht": ht},
                )
                self._state(rid, "channel", str(chan), self.start)
                radios[band] = {"id": rid, "nid": rnid, "channel": chan, "ht": ht}
            self.aps[spec["name"]] = {"id": aid, "mac": mac, "radios": radios, "spec": spec}

    def _build_clients(self) -> None:
        ap_names = list(self.aps)
        wireless_aps = [n for n in ap_names]
        roster = self._client_roster()
        ip_host = 10
        for i, (name, wired, iot) in enumerate(roster):
            group = 0xC1 if not iot else 0xD2
            mac = self.macs.next(group)
            if wired:
                # Attach wired clients to a switch.
                sw = list(self.switches.values())[i % len(self.switches)]
                parent_id = sw["id"]
                meta = {"oui": "DemoCorp", "is_wired": True, "essid": None}
            else:
                ap = self.aps[wireless_aps[i % len(wireless_aps)]]
                parent_id = ap["id"]
                meta = {
                    "oui": "DemoCorp" if not iot else "DemoIoT",
                    "is_wired": False,
                    "essid": SSID_IOT if iot else SSID_MAIN,
                }
            cid = self._entity(EntityType.CLIENT, mac, name=name, parent_id=parent_id, meta=meta)
            ip = f"192.0.2.{ip_host}"
            ip_host += 1
            self._state(cid, "ip", ip, self.start)
            if not wired:
                ap = self.aps[wireless_aps[i % len(wireless_aps)]]
                self._state(cid, "ap_mac", ap["mac"], self.start)
            self.clients.append(
                {
                    "id": cid,
                    "mac": mac,
                    "name": name,
                    "wired": wired,
                    "iot": iot,
                    "parent_id": parent_id,
                    "base_rssi": self.rng.randint(-68, -48) if not wired else None,
                }
            )

    def _client_roster(self) -> list[tuple[str, bool, bool]]:
        roster = list(_CLIENT_BASE)
        # Pad to ~48 with numbered IoT devices so the site reads as prosumer-scale.
        extra = [
            ("Smart Bulb", True),
            ("Smart Plug", True),
            ("Motion Sensor", True),
            ("Air Monitor", True),
        ]
        n = 1
        while len(roster) < 48:
            base, iot = extra[(len(roster)) % len(extra)]
            roster.append((f"{base} {n}", False, iot))
            if (len(roster)) % len(extra) == 0:
                n += 1
        return roster

    def _infra_ip(self) -> str:
        # Stable per-call address in the 198.51.100.0/24 doc range for infra.
        self._infra_octet = getattr(self, "_infra_octet", 1) + 1
        return f"198.51.100.{self._infra_octet}"

    # -- metric series ------------------------------------------------------- #
    def _write(self, eid: int, metric: str, points: list[tuple[int, float]], unit: str) -> None:
        if not points:
            return
        self._series_units[metric] = unit
        self.repo.record_samples(
            SampleReading(eid, metric, ts, float(val), unit=unit, kind=MetricKind.GAUGE)
            for ts, val in points
        )

    def build_series(self) -> None:
        with self.repo.transaction():
            self._series_gateway()
            self._series_switches()
            self._series_aps()
            self._series_clients()
            self._series_issue_overrides()

    def _series_gateway(self) -> None:
        gid = self.gw_id
        wan = []
        www = []
        drops = []
        rtt = []
        dns = []
        dns_anchor = []
        xput_down = []
        xput_up = []
        cpu = []
        mem = []
        deg_start = self.now - 6 * HOUR  # isp_degraded window
        dns_deg = (self.now - 30 * HOUR, self.now - 20 * HOUR)  # dns_slow, now recovering
        for ts in self.grid:
            base_lat = 17.0 + 6.0 * (1 - math.cos(2 * math.pi * self._hour(ts) / 24)) / 2
            elevated = ts >= deg_start
            lat = base_lat + (34.0 if elevated else 0.0) + self._jit(2.5)
            wan.append((ts, _clamp(lat, 8, 90)))
            www.append((ts, _clamp(lat + self._jit(3), 8, 95)))
            drops.append((ts, max(0.0, self._jit(1.5) + (2.0 if elevated else 0.0))))
            rtt.append((ts, _clamp(base_lat * 0.7 + self._jit(1.5), 3, 60)))
            in_dns = dns_deg[0] <= ts < dns_deg[1]
            dns.append((ts, _clamp(28 + (190 if in_dns else 0) + self._jit(8), 10, 260)))
            dns_anchor.append((ts, _clamp(24 + self._jit(5), 8, 60)))
            xput_down.append((ts, _clamp(self._diurnal(ts, 60, 480, 21) + self._jit(30), 20, 600)))
            xput_up.append((ts, _clamp(self._diurnal(ts, 8, 42, 21) + self._jit(4), 3, 60)))
            cpu.append((ts, _clamp(self._diurnal(ts, 6, 28, 21) + self._jit(3), 2, 70)))
            mem.append((ts, _clamp(41 + self._jit(3), 20, 80)))
        self._write(gid, "wan_latency", wan, "ms")
        self._write(gid, "www_latency", www, "ms")
        self._write(gid, "wan_drops", drops, "packets")
        self._write(gid, "gw_rtt_ms", rtt, "ms")
        self._write(gid, "dns_latency_ms", dns, "ms")
        self._write(gid, "dns_anchor_latency_ms", dns_anchor, "ms")
        self._write(gid, "wan_xput_down", xput_down, "mbps")
        self._write(gid, "wan_xput_up", xput_up, "mbps")
        self._write(gid, "cpu", cpu, "percent")
        self._write(gid, "mem", mem, "percent")

    def _series_switches(self) -> None:
        for sw in self.switches.values():
            cpu = [
                (ts, _clamp(self._diurnal(ts, 5, 22, 20) + self._jit(3), 2, 60)) for ts in self.grid
            ]
            mem = [(ts, _clamp(38 + self._jit(3), 20, 80)) for ts in self.grid]
            self._write(sw["id"], "cpu", cpu, "percent")
            self._write(sw["id"], "mem", mem, "percent")
            if sw["budget"] is not None:
                used = [
                    (ts, _clamp(self._diurnal(ts, 22, 46, 20) + self._jit(3), 10, sw["budget"]))
                    for ts in self.grid
                ]
                self._write(sw["id"], "total_used_power", used, "watts")
                self._write(
                    sw["id"], "total_max_power", [(ts, sw["budget"]) for ts in self.grid], "watts"
                )
            # A couple of access ports carry real byte counters for chart depth.
            for port in sw["ports"][:3]:
                rxb = [
                    (
                        ts,
                        _clamp(
                            self._diurnal(ts, 2e5, 6e6, 21) * self.rng.uniform(0.6, 1.4), 0, 2e7
                        ),
                    )
                    for ts in self.grid
                ]
                txb = [(ts, val * self.rng.uniform(0.2, 0.6)) for ts, val in rxb]
                self._write(port["id"], "rx_bytes", rxb, "bytes")
                self._write(port["id"], "tx_bytes", txb, "bytes")

    def _series_aps(self) -> None:
        for name, ap in self.aps.items():
            cpu = [
                (ts, _clamp(self._diurnal(ts, 8, 34, 21) + self._jit(4), 3, 80)) for ts in self.grid
            ]
            mem = [(ts, _clamp(44 + self._jit(4), 20, 85)) for ts in self.grid]
            temp = [
                (ts, _clamp(46 + self._diurnal(ts, 0, 6, 15) + self._jit(1.5), 30, 75))
                for ts in self.grid
            ]
            self._write(ap["id"], "cpu", cpu, "percent")
            self._write(ap["id"], "mem", mem, "percent")
            self._write(ap["id"], "temp", temp, "celsius")
            # Device-level client-count/satisfaction accumulators: the real
            # controller reports `num_sta`/`satisfaction` on the device itself
            # (stat/device top level -- confirmed against
            # tests/netadmin/unifi/fixtures/stat_device.json), not just per radio,
            # and mapping.py now emits both there (Gitea #23). num_sta sums
            # across bands; satisfaction is the client-count-weighted mean, since
            # an idle radio's satisfaction should not out-vote the band actually
            # carrying load.
            dev_nsta = [0.0] * len(self.grid)
            dev_sat_weighted = [0.0] * len(self.grid)
            for band, radio in ap["radios"].items():
                saturated = name == "Living Room" and band == "ng"
                cu = []
                nsta = []
                sat = []
                retr = []
                for i, ts in enumerate(self.grid):
                    if saturated:
                        base_cu = self._diurnal(ts, 40, 70, 21) + self._jit(4)
                    else:
                        base_cu = self._diurnal(ts, 6, 30, 21) + self._jit(4)
                    cu.append((ts, _clamp(base_cu, 1, 95)))
                    n = round(_clamp(self._diurnal(ts, 1, 9, 21) + self._jit(1), 0, 20))
                    nsta.append((ts, n))
                    s = _clamp((70 if saturated else 94) + self._jit(4), 30, 100)
                    sat.append((ts, s))
                    retr.append(
                        (ts, max(0.0, self._diurnal(ts, 20, 400, 21) * self.rng.uniform(0.5, 1.5)))
                    )
                    dev_nsta[i] += n
                    dev_sat_weighted[i] += s * n
                self._write(radio["id"], "cu_total", cu, "percent")
                self._write(radio["id"], "num_sta", nsta, "clients")
                self._write(radio["id"], "satisfaction", sat, "percent")
                self._write(radio["id"], "tx_retries", retr, "packets")
                if saturated:
                    self._write(
                        radio["id"],
                        "cu_self_rx",
                        [(ts, _clamp(v * 0.15, 0, 40)) for ts, v in cu],
                        "percent",
                    )
                    self._write(
                        radio["id"],
                        "cu_self_tx",
                        [(ts, _clamp(v * 0.12, 0, 40)) for ts, v in cu],
                        "percent",
                    )
            dev_sat = [(w / n) if n > 0 else 100.0 for n, w in zip(dev_nsta, dev_sat_weighted)]
            self._write(ap["id"], "num_sta", list(zip(self.grid, dev_nsta)), "clients")
            self._write(ap["id"], "satisfaction", list(zip(self.grid, dev_sat)), "percent")

    def _series_clients(self) -> None:
        # Give ~15 clients byte counters for chart depth; all wireless get rssi/sat.
        for i, client in enumerate(self.clients):
            cid = client["id"]
            if not client["wired"]:
                base = client["base_rssi"]
                rssi = [(ts, _clamp(base + self._jit(3), -90, -30)) for ts in self.grid]
                sat = [(ts, _clamp(_client_sat(base) + self._jit(5), 20, 100)) for ts in self.grid]
                self._write(cid, "rssi", rssi, "dbm")
                self._write(cid, "satisfaction", sat, "percent")
            if i % 3 == 0 or client["wired"]:
                peak = 21 if not client["iot"] else 12
                lo, hi = (2e5, 4e6) if not client["iot"] else (2e4, 2e5)
                rxb = [
                    (
                        ts,
                        _clamp(
                            self._diurnal(ts, lo, hi, peak) * self.rng.uniform(0.5, 1.5), 0, 1e7
                        ),
                    )
                    for ts in self.grid
                ]
                txb = [(ts, val * self.rng.uniform(0.2, 0.7)) for ts, val in rxb]
                self._write(cid, "rx_bytes", rxb, "bytes")
                self._write(cid, "tx_bytes", txb, "bytes")

    def _series_issue_overrides(self) -> None:
        """Dense, issue-consistent recent tails so evidence charts read as real."""
        # Bad cable: rx/tx_errors ramp over the last ~4 h on a gigabit-capable port
        # negotiated at 100. Honest enough that wired.bad_cable would fire if run.
        tail = [
            ts for ts in range(self.now - 4 * HOUR, self.now, FINE_STEP) if not self._in_gap(ts)
        ]
        rxerr = []
        txerr = []
        for k, ts in enumerate(tail):
            ramp = 6 + k * 5  # rising errors per interval
            rxerr.append((ts, float(ramp)))
            txerr.append((ts, float(ramp * 0.4)))
        self._write(self.bad_cable_port_id, "rx_errors", rxerr, "packets")
        self._write(self.bad_cable_port_id, "tx_errors", txerr, "packets")
        self._write(
            self.bad_cable_port_id,
            "rx_packets",
            [(ts, 90_000.0 + self._jit(5000)) for ts in tail],
            "packets",
        )
        # The bad-cable downshift: gigabit -> 100 an hour ago.
        self._state(self.bad_cable_port_id, "speed", "100", self.now - HOUR)

        # PoE reboot loop under the flapping Garage-AP uplink port: draw dips to 0.
        poe = []
        for k, ts in enumerate(tail):
            poe.append((ts, 0.0 if k % 4 == 0 else 8.4 + self._jit(0.3)))
        self._write(self.flap_port_id, "poe_power", poe, "watts")

        # DNS resolver recovery tail (issue is now RESOLVING).
        dtail = [
            ts for ts in range(self.now - 4 * HOUR, self.now, FINE_STEP) if not self._in_gap(ts)
        ]
        self._write(
            self.gw_id,
            "dns_latency_ms",
            [(ts, _clamp(70 - i * 3 + self._jit(6), 20, 220)) for i, ts in enumerate(dtail)],
            "ms",
        )

    # -- events / poll_runs -------------------------------------------------- #
    def build_events(self) -> None:
        events: list[dict[str, Any]] = []
        rng = self.rng
        # Ambient connect/disconnect churn across the week.
        wireless = [c for c in self.clients if not c["wired"]]
        for _ in range(180):
            ts = rng.randint(self.start, self.now)
            if self._in_gap(ts):
                continue
            client = rng.choice(wireless)
            ap = self.aps[list(self.aps)[rng.randrange(len(self.aps))]]
            key = "EVT_WU_Connected" if rng.random() < 0.55 else "EVT_WU_Disconnected"
            data = {"reason": rng.choice([1, 4, 8, 8, 8])} if key.endswith("Disconnected") else {}
            events.append(
                {
                    "ts": ts,
                    "key": key,
                    "entity_id": client["id"],
                    "related_entity_id": ap["id"],
                    "msg": f"{client['name']} {'connected to' if 'Connected' in key else 'disconnected from'} {ap['spec']['name']}",
                    "data": data,
                }
            )
        # Ping-pong roam burst for the roaming client, last hour, two APs.
        living = self.aps["Living Room"]
        kitchen = self.aps["Kitchen"]
        for k in range(7):
            ts = self.now - (55 - k * 7) * MINUTE
            frm, to = (kitchen, living) if k % 2 == 0 else (living, kitchen)
            events.append(
                {
                    "ts": ts,
                    "key": "EVT_WU_RoamRadio" if k % 2 == 0 else "EVT_WU_Roam",
                    "entity_id": self.pingpong_client["id"],
                    "related_entity_id": frm["id"],
                    "msg": f"{self.pingpong_client['name']} roamed {frm['spec']['name']} -> {to['spec']['name']}",
                    "data": {"to_ap": to["mac"]},
                }
            )
        # Post-firmware disconnect surge on the Studio AP.
        studio = self.aps["Studio"]
        up_ts = self.now - 2 * DAY
        for k in range(16):
            events.append(
                {
                    "ts": up_ts + HOUR + k * 90 * MINUTE,
                    "key": "EVT_WU_Disconnected",
                    "entity_id": None,
                    "related_entity_id": studio["id"],
                    "msg": "client disconnected (post-upgrade)",
                    "data": {"reason": 4},
                }
            )
        # Port-flap transitions on the Garage uplink port.
        for k in range(9):
            events.append(
                {
                    "ts": self.now - (50 - k * 5) * MINUTE,
                    "key": "EVT_SW_PortDown" if k % 2 == 0 else "EVT_SW_PortUp",
                    "entity_id": self.flap_port_id,
                    "related_entity_id": self.switches["Office Switch"]["id"],
                    "msg": f"Port 2 link {'down' if k % 2 == 0 else 'up'}",
                    "data": {},
                }
            )
        # WAN transition + a DFS radar hit for timeline color.
        events.append(
            {
                "ts": self.now - 30 * HOUR,
                "key": "EVT_GW_WANTransition",
                "entity_id": self.gw_id,
                "related_entity_id": None,
                "msg": "WAN link state changed",
                "data": {},
            }
        )
        events.append(
            {
                "ts": self.now - 20 * HOUR,
                "key": "EVT_AP_RadarDetected",
                "entity_id": self.aps["Back Porch"]["id"],
                "related_entity_id": None,
                "msg": "Radar detected on DFS channel",
                "data": {},
            }
        )

        events.sort(key=lambda e: e["ts"])
        with self.repo.transaction():
            for i, ev in enumerate(events):
                self.repo.record_event(
                    ts=ev["ts"],
                    key=ev["key"],
                    entity_id=ev["entity_id"],
                    related_entity_id=ev["related_entity_id"],
                    native_id=f"demo-evt-{i}",
                    msg=ev["msg"],
                    data=ev["data"],
                )

    # The 14 canonical (job, cadence_seconds) pairs the health/dashboard surface
    # reports on -- mirrors netadmin/server/runtime.py `_JOB_CADENCE`. Hardcoded
    # here (not imported) so the demo package never couples to the server package.
    _JOB_CADENCE_S: tuple[tuple[str, int], ...] = (
        ("fast_device", 60),
        ("fast_sta", 60),
        ("fast_health", 60),
        ("events_catchup", 300),
        ("reports_5min", 21_600),
        ("probe.dns", 60),
        ("probe.dns.anchor", 60),
        ("probe.gw_rtt", 60),
        ("detect_fast", 60),
        ("detect_window", 900),
        ("detect_daily", 86_400),
        ("baseline", 300),
        ("sle_minutes", 300),
        ("correlate", 60),
    )
    # The slow cron-style jobs: a bare ok=True is plausible enough, no per-run
    # duration reading (nothing "sub-minute" or collector-shaped about them).
    _SLOW_JOBS = frozenset({"detect_daily", "reports_5min"})

    def build_poll_runs(self) -> None:
        # Live cadence for every job across the FULL history window (feeds
        # /api/health + per-job coverage), minus the two gap windows so a gap
        # still reads as a real collection outage rather than being smoothed over.
        with self.repo.transaction():
            for job, cadence in self._JOB_CADENCE_S:
                for ts in range(self.start, self.now + 1, cadence):
                    if self._in_gap(ts):
                        continue
                    duration_ms = None if job in self._SLOW_JOBS else self.rng.randint(40, 260)
                    self.repo.record_poll_run(job=job, ok=True, ts=ts, duration_ms=duration_ms)

    # -- baselines ----------------------------------------------------------- #
    def build_baselines(self) -> None:
        with self.repo.transaction():
            # WAN latency + every radio's cu_total/num_sta get an 'all' baseline
            # (mean/var/percentiles) computed honestly from the generated samples.
            self._baseline_for(self.gw_id, "wan_latency")
            for ap in self.aps.values():
                for radio in ap["radios"].values():
                    self._baseline_for(radio["id"], "cu_total")
                    self._baseline_for(radio["id"], "num_sta")

    def _baseline_for(self, eid: int, metric: str) -> None:
        series_id = self.repo.get_series(eid, metric)
        if series_id is None:
            return
        rows = self.repo.read_raw(series_id, self.start, self.now)
        values = [r["value"] for r in rows]
        if len(values) < 4:
            return
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / len(values)
        ordered = sorted(values)
        for stat, val in (
            ("ewma_mean", mean),
            ("ewma_var", var),
            ("p05", _percentile(ordered, 0.05)),
            ("p50", _percentile(ordered, 0.50)),
            ("p95", _percentile(ordered, 0.95)),
        ):
            self.repo.upsert_baseline(series_id, "all", stat, float(val), ts=self.now)

    # -- issues -------------------------------------------------------------- #
    def build_issues(self) -> None:
        with self.repo.transaction():
            self._issue_bad_cable()
            self._issue_port_flapping()
            self._issue_airtime()
            self._issue_sticky()
            self._issue_pingpong()
            self._issue_isp_degraded()
            self._issue_firmware()
            self._issue_min_rssi()
            self._issue_dns_slow_resolving()
            self._issue_duplex_resolved()
            self._issue_channel_plan_fix_verified()
            # The flagship correlation story (section 17): one weak mesh backhaul
            # that is the root of a coverage hole, a saturated radio, and a
            # dropping client — all on/under the Back Porch mesh AP.
            self._issue_mesh_incident()
            # The RF neighbourhood: one site-scoped issue per band, so the demo
            # shows crowded air the way the report does -- as context, never as
            # one alarm per neighbour BSS.
            self._issue_neighbor_density()

    def _insert(
        self,
        *,
        detector_key: str,
        native_id: str,
        entity_id: Optional[int],
        severity: Severity,
        state: IssueState,
        title: str,
        first_seen: int,
        last_seen: int,
        evidence: dict[str, Any],
        confounders: list[str],
        dims: Optional[dict[str, str]] = None,
        resolved_ts: Optional[int] = None,
        clear_streak: int = 0,
        occurrences: int = 1,
        ack_ts: Optional[int] = None,
        fix_state: Optional[FixState] = None,
    ) -> int:
        ev = dict(evidence)
        ev["confounders_checked"] = list(confounders)
        return self.repo.insert_issue(
            fingerprint=_fingerprint(detector_key, native_id, dims or {}),
            detector_key=detector_key,
            severity=severity.value,
            state=state.value,
            first_seen_ts=first_seen,
            last_seen_ts=last_seen,
            title=title,
            entity_id=entity_id,
            evidence=ev,
            clear_streak=clear_streak,
            occurrences=occurrences,
            resolved_ts=resolved_ts,
            ack_ts=ack_ts,
            fix_state=fix_state.value if fix_state else None,
        )

    def _detected(self, iid: int, ts: int, severity: Severity) -> None:
        self.repo.record_issue_event(
            iid, EventKind.DETECTED, ts=ts, detail={"severity": severity.value}
        )

    def _escalated(self, iid: int, ts: int, occurrences: int, m: int = 3) -> None:
        self.repo.record_issue_event(
            iid,
            EventKind.ESCALATED,
            ts=ts,
            detail={"reason": "m_reached", "m": m, "occurrences": occurrences},
        )

    def _issue_bad_cable(self) -> None:
        first = self.now - 4 * DAY
        iid = self._insert(
            detector_key="wired.bad_cable",
            native_id=self.bad_cable_port_nid,
            entity_id=self.bad_cable_port_id,
            severity=Severity.P2,
            state=IssueState.ACTIVE,
            title=f"Cable/link fault on port {self.bad_cable_port_nid}",
            first_seen=first,
            last_seen=self.now - FINE_STEP,
            evidence={
                "errors_per_min": 63.0,
                "errors_per_min_threshold": 10.0,
                "error_packet_fraction": 0.0012,
                "negotiated_speed": 100,
                "port_capable_speed": 1000,
                "signals": ["error_rate", "speed_downshift"],
            },
            confounders=[
                "coverage_gated",
                "counter_reset_handled",
                "packet_volume_normalized",
                "known_100mbps_device_class",
                "port_gigabit_capable",
            ],
            occurrences=41,
        )
        self._detected(iid, first, Severity.P2)
        self._escalated(iid, first + 3 * 900, 3)
        # A worked LLM investigation thread (manual provider, answered).
        inv = self.repo.insert_investigation(
            issue_id=iid,
            provider="manual",
            dossier_md="# Cable/link fault dossier\n\nRising rx_errors on a gigabit port negotiated at 100 Mbps.",
            status="answered",
            ts=first + 6 * HOUR,
            response_md=(
                "Root cause: a damaged twisted pair forced the link to renegotiate to 100 Mbps and is "
                "driving CRC errors. Reseat/replace the patch cable; if errors persist, swap the port."
            ),
        )
        self.repo.record_issue_event(
            iid,
            EventKind.INVESTIGATED,
            ts=first + 6 * HOUR,
            detail={"investigation_id": inv, "provider": "manual"},
        )

    def _issue_port_flapping(self) -> None:
        first = self.now - 20 * HOUR
        iid = self._insert(
            detector_key="wired.port_flapping",
            native_id=self.flap_port_nid,
            entity_id=self.flap_port_id,
            severity=Severity.P1,  # uplink / infra port
            state=IssueState.ACTIVE,
            title=f"Port flapping: Port 2 (6 transitions/10m)",
            first_seen=first,
            last_seen=self.now - 2 * MINUTE,
            evidence={
                "transitions_short": 6,
                "transitions_long": 9,
                "window_short_s": 600,
                "window_long_s": 3600,
                "poe_reboot_loop": True,
                "poe_min_w": 0.0,
                "poe_max_w": 8.5,
            },
            confounders=["coverage_gated", "sustained_transition_count", "poe_reboot_correlated"],
            occurrences=9,
        )
        # Also record the up-state flap history the detector counts from.
        for k in range(9):
            self._state(
                self.flap_port_id,
                "up",
                "true" if k % 2 else "false",
                self.now - (50 - k * 5) * MINUTE,
            )
        self._detected(iid, first, Severity.P1)
        self._escalated(iid, first + 3 * 900, 3)

    def _issue_airtime(self) -> None:
        radio = self.aps["Living Room"]["radios"]["ng"]
        first = self.now - 3 * DAY
        iid = self._insert(
            detector_key="wifi.airtime_saturation",
            native_id=radio["nid"],
            entity_id=radio["id"],
            severity=Severity.P2,
            state=IssueState.ACTIVE,
            title="Airtime saturation (degraded) on Living Room 2.4G",
            first_seen=first,
            last_seen=self.now - FINE_STEP,
            evidence={
                "cu_total_median": 66.0,
                "cu_self": 17.0,
                "cu_non_self": 49.0,
                "dominant_source": "non_self",
                "level": "degraded",
            },
            confounders=["sustained_not_burst", "self_vs_non_self_split"],
            dims={"band": "2.4"},
            occurrences=58,
        )
        self._detected(iid, first, Severity.P2)
        self._escalated(iid, first + 3 * 900, 3)

    def _issue_sticky(self) -> None:
        client = self.sticky_client
        living_mac = self.aps["Living Room"]["mac"]
        first = self.now - 2 * DAY
        iid = self._insert(
            detector_key="wifi.sticky_client",
            native_id=client["mac"],
            entity_id=client["id"],
            severity=Severity.P3,
            state=IssueState.ACTIVE,
            title=f"Sticky client {client['name']} on far AP",
            first_seen=first,
            last_seen=self.now - FINE_STEP,
            evidence={
                "ap": living_mac,
                "rssi": -57,
                "median_tx_rate_mbps": 6.0,
                "low_rate_corroborated": True,
                "clustered_on_ap": False,
            },
            confounders=["better_ap_exists", "sustained_not_transient", "low_rate_corroborated"],
            dims={"ap": living_mac},
            occurrences=22,
        )
        # ap_mac history: was on Living Room, now stuck on Basement.
        self._state(client["id"], "ap_mac", living_mac, self.now - 5 * DAY)
        self._state(client["id"], "ap_mac", self.aps["Basement"]["mac"], self.now - 2 * DAY)
        self._detected(iid, first, Severity.P3)
        self._escalated(iid, first + 3 * 900, 3)

    def _issue_pingpong(self) -> None:
        client = self.pingpong_client
        first = self.now - 30 * HOUR
        iid = self._insert(
            detector_key="wifi.pingpong_roamer",
            native_id=client["mac"],
            entity_id=client["id"],
            severity=Severity.P3,
            state=IssueState.ACTIVE,
            title=f"Ping-pong roamer {client['name']}",
            first_seen=first,
            last_seen=self.now - 5 * MINUTE,
            evidence={
                "roams": 7,
                "distinct_aps": 2,
                "burst_run": 3,
                "burst_max_gap_s": 10,
                "roams_per_hour": 6.8,
                "reason": "rate_suspicious",
            },
            confounders=["sustained_rate_over_window"],
            occurrences=7,
        )
        self._detected(iid, first, Severity.P3)
        self._escalated(iid, first + 3 * 900, 3)

    def _issue_isp_degraded(self) -> None:
        first = self.now - 6 * HOUR
        iid = self._insert(
            detector_key="wan.isp_degraded",
            native_id=self.gw_mac,
            entity_id=self.gw_id,
            severity=Severity.P2,
            state=IssueState.ACTIVE,
            title="WAN degraded (latency p50 52 ms vs 18 ms baseline, sustained ≥3 windows)",
            first_seen=first,
            last_seen=self.now - MINUTE,
            evidence={
                "latency_metric": "www_latency",
                "latency_fired": True,
                "window_p50_ms": 52.0,
                "baseline_p50_ms": 18.0,
                "ratio": 2.89,
                "loss_fired": False,
                "loss_fraction": 0.002,
                "baseline_loss_fraction": 0.0004,
                "sustained_windows_required": 3,
            },
            confounders=[
                "rolling_window_p50_robust_to_handoff_spikes",
                "trend_vs_own_7d_baseline_not_absolute",
                "absolute_hold_floor_prevents_baseline_drift_autoresolve",
                "sustained_multi_window_required",
            ],
            occurrences=12,
        )
        self._detected(iid, first, Severity.P2)
        self._escalated(iid, first + 3 * 900, 3)

    def _issue_firmware(self) -> None:
        studio = self.aps["Studio"]
        up_ts = self.now - 2 * DAY
        first = up_ts + 3 * HOUR
        self._state(studio["id"], "firmware", "5.43.35", self.now - 6 * DAY)
        self._state(studio["id"], "firmware", "5.43.56", up_ts)
        iid = self._insert(
            detector_key="net.firmware_regression",
            native_id=studio["mac"],
            entity_id=studio["id"],
            severity=Severity.P2,
            state=IssueState.ACTIVE,
            title="Firmware regression on Studio (v5.43.56)",
            first_seen=first,
            last_seen=self.now - HOUR,
            evidence={
                "version": "5.43.56",
                "model": "UAP-AC-Pro",
                "upgrade_ts": up_ts,
                "pre_disconnects_per_hour": 0.4,
                "post_disconnects_per_hour": 3.1,
                "pre_port_errors": 0.0,
                "post_port_errors": 0.0,
                "fleet_devices_regressed": 1,
                "fleet_wide": False,
            },
            confounders=[
                "settle_window_excluded_2h",
                "pre_post_same_device_baseline",
                "device_coverage_gated",
            ],
            occurrences=4,
        )
        self._detected(iid, first, Severity.P2)
        self._escalated(iid, first + 3 * 86400 // 24, 3)

    def _issue_min_rssi(self) -> None:
        radio = self.aps["Primary Bedroom"]["radios"]["na"]
        first = self.now - 3 * DAY
        iid = self._insert(
            detector_key="wifi.min_rssi_misconfig",
            native_id=radio["nid"],
            entity_id=radio["id"],
            severity=Severity.P3,
            state=IssueState.ACTIVE,
            title="min-RSSI misconfigured on Primary Bedroom 5G",
            first_seen=first,
            last_seen=self.now - 2 * HOUR,
            evidence={
                "min_rssi_dbm": -75,
                "reason": "stricter_than_floor",
                "ap_count": 8,
                "on_mesh_ap": False,
                "strict_floor_dbm": -70,
            },
            confounders=["mesh_uplink_checked", "single_ap_site_checked"],
            dims={"band": "5"},
            occurrences=16,
            ack_ts=self.now - 12 * HOUR,
        )
        self._detected(iid, first, Severity.P3)
        self._escalated(iid, first + 3 * 900, 3)
        self.repo.record_issue_event(iid, EventKind.ACKED, ts=self.now - 12 * HOUR, detail={})

    def _issue_dns_slow_resolving(self) -> None:
        first = self.now - 30 * HOUR
        iid = self._insert(
            detector_key="wan.dns_slow",
            native_id=self.gw_mac,
            entity_id=self.gw_id,
            severity=Severity.P2,
            state=IssueState.RESOLVING,
            title="DNS resolution slow (212 ms, local)",
            first_seen=first,
            last_seen=self.now - 3 * HOUR,
            evidence={
                "resolver_p50_ms": 212.0,
                "anchor_p50_ms": 25.0,
                "localised": "local",
                "severity_tier": "warn",
            },
            confounders=["anchor_comparison_localises_fault", "probe_coverage_gated"],
            clear_streak=3,
            occurrences=18,
        )
        self._detected(iid, first, Severity.P2)
        self._escalated(iid, first + 3 * 900, 3)
        # First clean check after the last fire (Gitea #23/#26): this is the one
        # event that explains the Resolving pill on the issue detail's trail. The
        # issue's own `clear_streak` (3, set on `_insert` above) is live and keeps
        # advancing past this event without a new row per check; the trail pairs
        # this event's `k` with that live count to show "N of K" progress.
        self.repo.record_issue_event(
            iid,
            EventKind.RESOLVING,
            ts=self.now - 3 * HOUR + 900,
            detail={"clear_streak": 1, "k": 6},
        )

    def _issue_duplex_resolved(self) -> None:
        first = self.now - 5 * DAY
        resolved = self.now - 3 * DAY
        iid = self._insert(
            detector_key="wired.duplex_mismatch",
            native_id=self.duplex_port_nid,
            entity_id=self.duplex_port_id,
            severity=Severity.P2,
            state=IssueState.RESOLVED,
            title=f"Half-duplex on modern link: port {self.duplex_port_nid}",
            first_seen=first,
            last_seen=resolved,
            evidence={"full_duplex": False, "speed": 1000, "modern_speed_min": 1000},
            confounders=["coverage_gated", "link_up_checked", "modern_speed_link"],
            resolved_ts=resolved,
            occurrences=14,
        )
        # duplex state history: was half, fixed to full at resolution.
        self._state(self.duplex_port_id, "full_duplex", "false", first)
        self._state(self.duplex_port_id, "full_duplex", "true", resolved)
        self._detected(iid, first, Severity.P2)
        self._escalated(iid, first + 3 * 900, 3)
        self.repo.record_issue_event(
            iid, EventKind.RESOLVED, ts=resolved, detail={"clear_streak": 6}
        )

    def _issue_channel_plan_fix_verified(self) -> None:
        radio = self.aps["Office"]["radios"]["ng"]
        first = self.now - 4 * DAY
        proposed = self.now - 65 * HOUR
        applied = self.now - 60 * HOUR
        resolved = self.now - 48 * HOUR
        iid = self._insert(
            detector_key="wifi.channel_plan",
            native_id=radio["nid"],
            entity_id=radio["id"],
            severity=Severity.P3,
            state=IssueState.RESOLVED,
            title="Channel-plan issue (co_channel_reuse) on Office 2.4G",
            first_seen=first,
            last_seen=resolved,
            evidence={"subtype": "co_channel_reuse", "band": "2.4", "channel": 6, "ht_mhz": 20},
            confounders=["own_radio_config_read"],
            dims={"subtype": "co_channel_reuse", "band": "2.4"},
            resolved_ts=resolved,
            fix_state=FixState.VERIFIED,
            occurrences=9,
        )
        # The fix moved the radio off the congested co-channel (6 -> 11).
        self._state(radio["id"], "channel", "6", first)
        self._state(radio["id"], "channel", "11", applied)
        change_id = self.repo.insert_change(
            action="set_radio_channel",
            before={"channel": 6},
            after={"channel": 11},
            status="applied",
            ts=applied,
            issue_id=iid,
            entity_id=radio["id"],
        )
        self._detected(iid, first, Severity.P3)
        self._escalated(iid, first + 3 * 900, 3)
        self.repo.record_issue_event(
            iid,
            EventKind.FIX_PROPOSED,
            ts=proposed,
            detail={"action": "set_radio_channel", "from": 6, "to": 11},
        )
        self.repo.record_issue_event(
            iid, EventKind.FIX_APPLIED, ts=applied, detail={"change_id": change_id}
        )
        self.repo.record_issue_event(
            iid, EventKind.RESOLVED, ts=resolved, detail={"clear_streak": 6}
        )
        self.repo.record_issue_event(iid, EventKind.FIX_VERIFIED, ts=resolved, detail={})

    def _issue_mesh_incident(self) -> None:
        """The Back Porch mesh cluster: one root + three symptoms (section 17).

        Timed so every symptom's ``first_seen`` follows the root's, well inside the
        temporal guard, so the correlation engine attributes them (a symptom cannot
        predate its cause). The relations are concrete: coverage hole on the *same*
        AP, saturated radio and dropping client *under* it.
        """
        ap = self.aps["Back Porch"]
        ap_id, ap_mac = ap["id"], ap["mac"]
        radio = ap["radios"]["ng"]
        cam = self.flaky_client
        root_first = self.now - 3 * DAY

        # Root: weak wireless backhaul on the mesh AP.
        root = self._insert(
            detector_key="wifi.mesh_uplink",
            native_id=ap_mac,
            entity_id=ap_id,
            severity=Severity.P2,
            state=IssueState.ACTIVE,
            title="Weak mesh backhaul on Back Porch (uplink −78 dBm, 8 reconnects/24h)",
            first_seen=root_first,
            last_seen=self.now - FINE_STEP,
            evidence={
                "uplink_rssi_dbm": -78,
                "uplink_rssi_threshold_dbm": -70,
                "hops": 2,
                "reconnects_24h": 8,
                "uplink_type": "wireless",
            },
            confounders=["sustained_over_window", "wireless_uplink_confirmed"],
            occurrences=61,
        )
        self._detected(root, root_first, Severity.P2)
        self._escalated(root, root_first + 3 * 900, 3)

        # Symptom 1: coverage hole in that same cell.
        cov_first = root_first + 2 * HOUR
        cov = self._insert(
            detector_key="net.coverage_hole",
            native_id=ap_mac,
            entity_id=ap_id,
            severity=Severity.P2,
            state=IssueState.ACTIVE,
            title="Coverage hole on Back Porch (p25 RSSI −79 dBm, 27% client-hours < −80)",
            first_seen=cov_first,
            last_seen=self.now - FINE_STEP,
            evidence={
                "p25_rssi_dbm": -79,
                "client_hours_below_80_pct": 27.0,
                "no_better_ap_in_history": True,
            },
            confounders=["no_better_ap_available", "sustained_over_window"],
            occurrences=44,
        )
        self._detected(cov, cov_first, Severity.P2)
        self._escalated(cov, cov_first + 3 * 900, 3)

        # Symptom 2: the AP's 2.4 GHz radio saturating as it retries over a bad link.
        air_first = root_first + 3 * HOUR
        air = self._insert(
            detector_key="wifi.airtime_saturation",
            native_id=radio["nid"],
            entity_id=radio["id"],
            severity=Severity.P2,
            state=IssueState.ACTIVE,
            title="Airtime saturation (degraded) on Back Porch 2.4G",
            first_seen=air_first,
            last_seen=self.now - FINE_STEP,
            evidence={
                "cu_total_median": 71.0,
                "cu_self": 38.0,
                "cu_non_self": 33.0,
                "dominant_source": "self",
                "level": "degraded",
            },
            confounders=["sustained_not_burst", "self_vs_non_self_split"],
            dims={"band": "2.4"},
            occurrences=52,
        )
        self._detected(air, air_first, Severity.P2)
        self._escalated(air, air_first + 3 * 900, 3)

        # Symptom 3: a client on that AP keeps dropping (device-or-deadspot).
        flap_first = self.now - 2 * DAY
        flaky = self._insert(
            detector_key="client.flaky",
            native_id=cam["mac"],
            entity_id=cam["id"],
            severity=Severity.P3,
            state=IssueState.ACTIVE,
            title=f"{cam['name']} dropping on Back Porch (18 disconnects/24h)",
            first_seen=flap_first,
            last_seen=self.now - 4 * MINUTE,
            evidence={
                "disconnects_24h": 18,
                "reason_codes": {"1": 11, "3": 7},
                "attribution": "one_client_one_ap_deadspot",
                "ap": ap_mac,
            },
            confounders=["reason_code_weighted", "attribution_matrix_applied"],
            occurrences=18,
        )
        self._detected(flaky, flap_first, Severity.P3)
        self._escalated(flaky, flap_first + 3 * 900, 3)

    def _issue_neighbor_density(self) -> None:
        """Four strong neighbour networks sharing our 2.4 GHz channels.

        One site-scoped issue for the whole band (entity ``rf:2.4``, no stored
        entity row), so the demo shows the aggregate the report reads rather than
        one alarm per BSSID. Fabricated BSSIDs (``02:`` space) and generic SSIDs —
        never a real neighbour's network name.
        """
        radio = self.aps["Kitchen"]["radios"]["ng"]
        first = self.now - 4 * DAY
        offenders = [
            {"bssid": "02:00:5e:99:14:22", "essid": "NEIGHBOR-2G4", "channel": 11, "rssi_dbm": -68},
            {"bssid": "02:00:5e:99:14:31", "essid": "NEIGHBOR-B", "channel": 11, "rssi_dbm": -71},
            {"bssid": "02:00:5e:99:14:47", "essid": "NEIGHBOR-C", "channel": 6, "rssi_dbm": -73},
            {"bssid": "02:00:5e:99:14:52", "essid": "NEIGHBOR-D", "channel": 1, "rssi_dbm": -74},
        ]
        iid = self._insert(
            detector_key="wifi.neighbor_density",
            native_id="rf:2.4",
            entity_id=None,
            severity=Severity.P3,
            state=IssueState.ACTIVE,
            title="4 neighbouring networks share our 2.4 GHz channels",
            first_seen=first,
            last_seen=self.now - HOUR,
            evidence={
                "band": "2.4",
                "qualifying_count": 4,
                "total_seen": 9,
                "per_channel": {"1": 1, "6": 1, "11": 2},
                "top_offenders": [
                    dict(o, seen_by_ap=radio["nid"], scan_count=4) for o in offenders
                ],
                "overlapping_radios": [radio["nid"]],
                "congested_overlap_radios": [],
                "materially_congested": False,
            },
            confounders=[
                "known_bssid_allowlist_checked",
                "own_ubnt_hardware_excluded",
                "transient_single_scan_excluded",
                "weak_neighbor_excluded",
                "own_radio_channel_overlap",
                "density_floor_applied",
            ],
            dims={"band": "2.4"},
            occurrences=4,
        )
        self._detected(iid, first, Severity.P3)
        self._escalated(iid, first + 3 * 900, 3)

    # -- SLE minutes --------------------------------------------------------- #
    def _pick_special_clients(self) -> None:
        # Deterministically choose the sticky (on Basement) and ping-pong (on
        # Living Room) clients from the roster.
        basement = self.aps["Basement"]["id"]
        living = self.aps["Living Room"]["id"]
        for c in self.clients:
            if not c["wired"] and c["name"] == "Work Laptop":
                self.sticky_client = c
                c["parent_id"] = basement
            if not c["wired"] and c["name"] == "iPhone":
                self.pingpong_client = c
                c["parent_id"] = living
        # The flaky client for the Back Porch mesh incident: the Backyard Camera,
        # dropping out because the outdoor mesh AP's wireless backhaul is failing.
        # Reparent it onto Back Porch in the DB (upsert updates parent_id) so the
        # correlation engine sees the concrete parent/child edge the mesh_uplink ->
        # client.flaky rule requires (a symptom is only attributed on a real edge).
        backporch = self.aps["Back Porch"]["id"]
        for c in self.clients:
            if not c["wired"] and c["name"] == "Backyard Camera":
                self.flaky_client = c
                c["parent_id"] = backporch
                self._entity(
                    EntityType.CLIENT,
                    c["mac"],
                    name="Backyard Camera",
                    parent_id=backporch,
                    meta={"oui": "DemoIoT", "is_wired": False, "essid": SSID_IOT},
                )
        # Fallbacks (roster is fixed, but stay safe).
        if not self.sticky_client:
            self.sticky_client = next(c for c in self.clients if not c["wired"])
        if not self.pingpong_client:
            self.pingpong_client = next(c for c in self.clients if not c["wired"])
        if not self.flaky_client:
            self.flaky_client = next(
                c for c in self.clients if not c["wired"] and c["id"] != self.sticky_client["id"]
            )

    def build_sle(self) -> None:
        basement = self.aps["Basement"]["id"]
        backporch = self.aps["Back Porch"]["id"]
        living_2g = self.aps["Living Room"]["radios"]["ng"]["id"]
        kitchen_5g = self.aps["Kitchen"]["radios"]["na"]["id"]
        living_ap = self.aps["Living Room"]["id"]

        wireless = [c for c in self.clients if not c["wired"]]
        active = wireless[:12]
        roamers = {self.sticky_client["id"], self.pingpong_client["id"]}
        devices = (
            [self.gw_id]
            + [s["id"] for s in self.switches.values()]
            + [a["id"] for a in self.aps.values()]
        )

        # Full history window, not just a recent slice, so the week reads as a
        # settled, fully-observed deployment; the degraded-incident windows below
        # (deg_wan + the per-bucket random rates) stay recent, so they still show
        # up as real incidents against an otherwise-healthy week.
        bucket_start = self.start
        buckets = list(range(bucket_start, self.now, 300))
        deg_wan = self.now - 6 * HOUR
        rng = self.rng
        n = 0
        with self.repo.transaction():
            for bts in buckets:
                for c in active:
                    cid = c["id"]
                    cov_off = (
                        basement
                        if cid == self.sticky_client["id"]
                        else rng.choice([basement, backporch])
                    )
                    cap_off = living_2g if rng.random() < 0.8 else kitchen_5g
                    # coverage
                    if rng.random() < 0.10:
                        self._sle(bts, SLE_COVERAGE, CLS_WEAK_SIGNAL, cid, 5.0, cov_off)
                    else:
                        self._sle(bts, SLE_COVERAGE, OK, cid, 5.0, None)
                    n += 1
                    # capacity
                    if rng.random() < 0.18:
                        cls = CLS_NON_WIFI_UTIL if rng.random() < 0.6 else CLS_CLIENT_LOAD
                        self._sle(bts, SLE_CAPACITY, cls, cid, 5.0, cap_off)
                    else:
                        self._sle(bts, SLE_CAPACITY, OK, cid, 5.0, None)
                    n += 1
                    # connect
                    if rng.random() < 0.05:
                        self._sle(bts, SLE_CONNECT, CLS_DHCP, cid, 5.0, self.gw_id)
                    else:
                        self._sle(bts, SLE_CONNECT, OK, cid, 5.0, None)
                    n += 1
                    # wan (shared uplink; degraded window recently)
                    p_wan = 0.5 if bts >= deg_wan else 0.06
                    if rng.random() < p_wan:
                        self._sle(bts, SLE_WAN, CLS_ISP_LATENCY, cid, 5.0, self.gw_id)
                    else:
                        self._sle(bts, SLE_WAN, OK, cid, 5.0, None)
                    n += 1
                    # roaming (only the roamers are exposed)
                    if cid in roamers:
                        if rng.random() < 0.20:
                            cls = CLS_PINGPONG if cid == self.pingpong_client["id"] else CLS_STICKY
                            self._sle(bts, SLE_ROAMING, cls, cid, 5.0, living_ap)
                        else:
                            self._sle(bts, SLE_ROAMING, OK, cid, 5.0, None)
                        n += 1
                # infra device-minutes
                for did in devices:
                    if did == self.aps["Studio"]["id"] and rng.random() < 0.03:
                        self._sle(bts, SLE_INFRA, CLS_RESTART_LOOP, did, 5.0, did)
                    else:
                        self._sle(bts, SLE_INFRA, OK, did, 5.0, None)
                    n += 1

    def _sle(
        self,
        bts: int,
        sle: str,
        classifier: str,
        entity_id: int,
        minutes: float,
        attributed: Optional[int],
    ) -> None:
        self.repo.upsert_sle_minute(
            bucket_ts=bts,
            sle=sle,
            classifier=classifier,
            entity_id=entity_id,
            minutes=minutes,
            attributed_entity_id=attributed,
        )

    def correlate(self) -> None:
        """Run one real correlation pass so the demo DB ships with incidents.

        The production daemon runs this on a scheduler (section 17); the demo has
        no scheduler, so we run the same engine once here — the demo then serves
        ``/api/incidents`` with the Back Porch cluster grouped and every other open
        issue as its own incident-of-one, exactly as a live system would. Anchored
        to ``self.now`` so incident timestamps stay deterministic.
        """
        from netadmin.correlate.engine import CorrelationEngine
        from netadmin.correlate.models import CorrelationConfig
        from netadmin.correlate.store_repository import StoreCorrelationRepository

        engine = CorrelationEngine(
            StoreCorrelationRepository(self.repo),
            config=CorrelationConfig(temporal_slack_s=900),
        )
        engine.run(self.now)

    # -- orchestration ------------------------------------------------------- #
    def run(self) -> None:
        self.build_inventory()
        self._pick_special_clients()
        self.build_series()
        self.build_events()
        self.build_poll_runs()
        self.build_baselines()
        self.build_issues()
        self.build_sle()
        self.correlate()


def _percentile(ordered: list[float], q: float) -> float:
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def _client_sat(rssi: int) -> float:
    """A plausible client-satisfaction gauge from RSSI (better signal -> higher)."""
    return _clamp(100 + (rssi + 50) * 1.4, 30, 99)


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #
def _collect_stats(repo: Repository, db_path: str, now: int) -> DemoStats:
    conn = repo.connection

    def _count(sql: str, params: tuple = ()) -> int:
        return int(conn.execute(sql, params).fetchone()[0])

    stats = DemoStats(db_path=db_path, now=now)
    stats.entities_total = _count("SELECT COUNT(*) FROM entities")
    for row in conn.execute("SELECT entity_type, COUNT(*) c FROM entities GROUP BY entity_type"):
        stats.entities_by_type[str(row["entity_type"])] = int(row["c"])
    stats.series = _count("SELECT COUNT(*) FROM series")
    stats.samples = _count("SELECT COUNT(*) FROM samples")
    stats.events = _count("SELECT COUNT(*) FROM events")
    stats.poll_runs = _count("SELECT COUNT(*) FROM poll_runs")
    stats.issues_total = _count("SELECT COUNT(*) FROM issues")
    for row in conn.execute("SELECT state, COUNT(*) c FROM issues GROUP BY state"):
        stats.issues_by_state[str(row["state"])] = int(row["c"])
    for row in conn.execute("SELECT severity, COUNT(*) c FROM issues GROUP BY severity"):
        stats.issues_by_severity[str(row["severity"])] = int(row["c"])
    stats.issue_events = _count("SELECT COUNT(*) FROM issue_events")
    stats.changes = _count("SELECT COUNT(*) FROM changes")
    stats.investigations = _count("SELECT COUNT(*) FROM investigations")
    stats.sle_minutes = _count("SELECT COUNT(*) FROM sle_minutes")
    stats.baselines = _count("SELECT COUNT(*) FROM baselines")

    from netadmin.sle.scores import sle_scores

    report = sle_scores(repo, now - DAY, now)
    stats.sle_headline = report.headline
    return stats


def seed_demo(
    out_path: str | Path = DEFAULT_OUT,
    *,
    now: int = DEFAULT_NOW,
    seed: int = DEMO_SEED,
    history_days: int = DEFAULT_HISTORY_DAYS,
    overwrite: bool = True,
) -> DemoStats:
    """Generate a fresh, fully-populated demo database at ``out_path``.

    Deterministic given ``now`` / ``seed`` / ``history_days``. Refuses to write to
    the production database name (``netadmin.db``) so a demo-seed can never clobber
    a live install. Returns a :class:`DemoStats` with the produced row counts.
    """
    path = Path(out_path)
    if path.name in _PROTECTED_BASENAMES:
        raise ValueError(
            f"refusing to write demo data to protected db name {path.name!r}; "
            "choose a different --out (e.g. data/netadmin-demo.db)"
        )
    if now % 300 != 0:
        raise ValueError("`now` must be aligned to the 300 s SLE bucket grid")

    if overwrite:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(path) + suffix)
            if candidate.exists():
                candidate.unlink()

    repo = Repository.open(path, site_id=SITE_ID)
    try:
        _Seeder(repo, now=now, seed=seed, history_days=history_days).run()
        stats = _collect_stats(repo, str(path), now)
    finally:
        repo.close()
    return stats
