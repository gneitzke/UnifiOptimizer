"""Fixtures for the tech-visit suite.

A :class:`FakeController` stands in for the read-only :class:`Endpoints` facade:
it returns pydantic controller models from an in-memory fixture, records every
call, and — critically — never touches a network. Every test drives the *real*
visit pipeline (collector jobs, catch-up, backfill, baselines, SLE, detectors)
against it; nothing here can reach a live controller.

The seeded network is small but deliberately faulty: an AP whose 2.4 GHz radio
sits on channel 3 (off the 1/6/11 grid) and a client suffering a pathological
disconnect storm on that AP — so a full detector pass fires a known, checkable
pair of issues.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from netadmin.config import Settings
from netadmin.ingest.unifi.models import (
    Client,
    Device,
    Event,
    HealthSubsystem,
    RadioTableStat,
    ReportRow,
    RogueAp,
    Wlan,
)
from netadmin.store.repository import Repository

# A bucket-aligned "now" (multiple of 300) so SLE buckets land cleanly.
NOW = 1_900_000_200
DAY = 86_400

AP_MAC = "aa:bb:cc:00:00:01"
GW_MAC = "aa:bb:cc:00:00:03"
CLIENT_FLAKY = "11:22:33:44:55:01"
CLIENT_OK = "11:22:33:44:55:02"


class FakeController:
    """A read-only, network-free stand-in for the ``Endpoints`` facade."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._devices = self._build_devices()
        self._clients = self._build_clients()
        self._health = self._build_health()
        self._rogues = self._build_rogues()
        self._wlans = self._build_wlans()
        self._events = self._build_events()
        self._report_rows = self._build_report_rows()

    # --- read set (every method here is a GET; none can mutate) ----------- #
    async def stat_device(self) -> list[Device]:
        self.calls.append("stat_device")
        return list(self._devices)

    async def stat_sta(self) -> list[Client]:
        self.calls.append("stat_sta")
        return list(self._clients)

    async def stat_health(self) -> list[HealthSubsystem]:
        self.calls.append("stat_health")
        return list(self._health)

    async def stat_rogueap(self, *, within_hours: int = 24) -> list[RogueAp]:
        self.calls.append("stat_rogueap")
        return list(self._rogues)

    async def rest_wlanconf(self) -> list[Wlan]:
        self.calls.append("rest_wlanconf")
        return list(self._wlans)

    async def stat_event(self, *, within_hours=None, max_events=None) -> list[Event]:
        self.calls.append("stat_event")
        return list(self._events)

    async def stat_report(self, interval, scope, *, start_ms, end_ms, attrs) -> list[ReportRow]:
        self.calls.append(f"stat_report:{interval}:{scope}")
        rows = self._report_rows.get(scope, [])
        return [r for r in rows if start_ms <= int(r.time) < end_ms]

    # --- fixture builders ------------------------------------------------- #
    @staticmethod
    def _build_devices() -> list[Device]:
        ap = Device(
            mac=AP_MAC,
            type="uap",
            model="U6-Pro",
            name="ap-office",
            version="6.6.55",
            state=1,
            radio_table_stats=[
                # 2.4 GHz radio parked on channel 3 -> off the 1/6/11 grid.
                RadioTableStat(name="wifi0", radio="ng", channel=3, ht=20, cu_total=20),
                RadioTableStat(name="wifi1", radio="na", channel=36, ht=80, cu_total=15),
            ],
        )
        gw = Device(mac=GW_MAC, type="ugw", name="gateway", version="6.6.55", state=1)
        return [ap, gw]

    @staticmethod
    def _build_clients() -> list[Client]:
        return [
            Client(
                mac=CLIENT_FLAKY,
                hostname="flaky-phone",
                name="flaky-phone",
                ap_mac=AP_MAC,
                is_wired=False,
                signal=-60,
                rssi=30,
                ip="192.168.1.50",
            ),
            Client(
                mac=CLIENT_OK,
                hostname="desktop",
                name="desktop",
                ap_mac=AP_MAC,
                is_wired=False,
                signal=-55,
                rssi=40,
                ip="192.168.1.60",
            ),
        ]

    @staticmethod
    def _build_health() -> list[HealthSubsystem]:
        return [
            HealthSubsystem(subsystem="wan", status="ok", gw_mac=GW_MAC, latency=12, drops=0),
            HealthSubsystem(subsystem="wlan", status="ok", num_user=2),
        ]

    @staticmethod
    def _build_rogues() -> list[RogueAp]:
        return [
            RogueAp(bssid="de:ad:be:ef:00:01", essid="neighbor", channel=6, rssi=-70, band="ng"),
        ]

    @staticmethod
    def _build_wlans() -> list[Wlan]:
        return [Wlan(_id="w1", name="HomeNet", enabled=True, security="wpapsk")]

    @staticmethod
    def _build_events() -> list[Event]:
        # Six pathological (reason=1) disconnects for one client on the AP within
        # the last half hour -> client.flaky, attributed one-client/one-AP.
        events: list[Event] = []
        for i in range(6):
            events.append(
                Event(
                    key="EVT_WU_Disconnected",
                    time=(NOW - 300 * (i + 1)) * 1000,
                    user=CLIENT_FLAKY,
                    ap=AP_MAC,
                    msg=f"disconnect {i}",
                    reason=1,
                )
            )
        return events

    @staticmethod
    def _build_report_rows() -> dict[str, list[ReportRow]]:
        """Hourly history for the last ~2 days so backfill + SLE have data."""
        ap_rows: list[ReportRow] = []
        user_rows: list[ReportRow] = []
        gw_rows: list[ReportRow] = []
        # Align to 5-minute buckets near "now" so at least a few land in SLE buckets.
        for i in range(1, 288):  # ~24h at 5-min spacing
            ts_ms = (NOW - 300 * i) * 1000
            ap_rows.append(
                ReportRow(
                    time=ts_ms,
                    oid=AP_MAC,
                    rx_bytes=500_000,
                    tx_bytes=200_000,
                    num_sta=2,
                    satisfaction=95,
                )
            )
            for mac, sig in ((CLIENT_FLAKY, -60), (CLIENT_OK, -55)):
                user_rows.append(
                    ReportRow(
                        time=ts_ms,
                        oid=mac,
                        rx_bytes=120_000,
                        tx_bytes=60_000,
                        signal=sig,
                        satisfaction=95,
                    )
                )
            gw_rows.append(
                ReportRow(
                    time=ts_ms,
                    oid=GW_MAC,
                    **{"wan-rx_bytes": 900_000, "wan-tx_bytes": 300_000},
                )
            )
        return {"ap": ap_rows, "user": user_rows, "gw": gw_rows}


@pytest.fixture
def fake_controller() -> FakeController:
    return FakeController()


@pytest.fixture
def visit_settings(tmp_db_path: Path) -> Settings:
    return Settings(_env_file=None, db_path=tmp_db_path, site_id="default")


@pytest.fixture
def visit_store(tmp_db_path: Path) -> Repository:
    store = Repository.open(tmp_db_path, site_id="default")
    yield store
    store.close()
