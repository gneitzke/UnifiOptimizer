"""Daemon component factory: subsystem assembly + naming-drift resolution.

Offline by construction. The controller client is built but never connected; the
probe runner's DNS/RTT probers are monkeypatched with canned samples so no
network, subprocess, or dnspython is touched.
"""

from __future__ import annotations

import asyncio

import pytest

import netadmin.ingest.factory as factory
from netadmin.config import ProbeConfig, Settings
from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.ingest.factory import ProbeRunner, SupervisorTask, build_components
from netadmin.ingest.probes import METRIC_DNS_ANCHOR_LATENCY, METRIC_GW_RTT, ProbeSample
from netadmin.store.repository import Repository

pytestmark = pytest.mark.asyncio


def _configured() -> Settings:
    return Settings(_env_file=None, unifi_host="https://ck.local", unifi_api_key="k")


async def test_build_components_wires_all_scheduler_jobs(repo: Repository) -> None:
    built = build_components(_configured(), repo)
    try:
        ids = {j.id for j in built.scheduler.get_jobs()}
        assert {
            "fast_device",
            "fast_sta",
            "fast_health",
            "events_catchup",
            "reports_5min",
            "retention_prune",
            # detection tiers + analysis jobs on the same scheduler (section 6 & 8)
            "detect_fast",
            "detect_window",
            "detect_daily",
            "baseline",
            "sle_minutes",
            # the correlation pass (section 17), offset after the detect passes
            "correlate",
        } <= ids
    finally:
        if built.scheduler.running:
            built.scheduler.shutdown(wait=False)
    # the collector's placeholder bodies are now wired to real machinery
    assert built.collector._event_catchup is not None
    assert built.collector._reports_backfill is not None
    # and the runtime subsystems match the lifespan's start/stop contract
    assert isinstance(built.ws_supervisor, SupervisorTask)
    assert isinstance(built.probes, ProbeRunner)


async def test_build_components_uses_injected_issue_engine(repo: Repository) -> None:
    from netadmin.issues.engine import IssueEngine
    from netadmin.issues.store_repository import StoreIssueRepository

    engine = IssueEngine(StoreIssueRepository(repo))
    built = build_components(_configured(), repo, issue_engine=engine)
    try:
        ids = {j.id for j in built.scheduler.get_jobs()}
        assert {"detect_fast", "baseline", "sle_minutes"} <= ids
    finally:
        if built.scheduler.running:
            built.scheduler.shutdown(wait=False)


async def test_correlation_job_absent_when_disabled(repo: Repository) -> None:
    from netadmin.config import CorrelateConfig

    settings = _configured()
    settings.correlate = CorrelateConfig(enabled=False)
    built = build_components(settings, repo)
    try:
        ids = {j.id for j in built.scheduler.get_jobs()}
        assert "correlate" not in ids
        # the other analysis jobs still land
        assert {"detect_fast", "baseline", "sle_minutes"} <= ids
    finally:
        if built.scheduler.running:
            built.scheduler.shutdown(wait=False)


async def test_build_components_raises_without_credentials(repo: Repository) -> None:
    with pytest.raises(RuntimeError):
        build_components(Settings(_env_file=None), repo)


async def test_probe_runner_noop_without_gateway(repo: Repository) -> None:
    # Both-null: no configured probe.gateway_ip AND no adopted gateway entity ->
    # a clean no-op. gateway_ip is forced None so the check is deterministic
    # regardless of any ambient data/config.yaml probe target.
    settings = Settings(_env_file=None, probe=ProbeConfig(gateway_ip=None))
    runner = ProbeRunner(repo, settings, interval_s=60)
    await runner._cycle()  # nothing measured, nothing raised, no entity fabricated
    assert repo.read_poll_runs("probe.dns.anchor", 0, 9_999_999_999) == []
    assert repo.read_poll_runs("probe.gw_rtt", 0, 9_999_999_999) == []
    assert repo.list_entities(EntityType.GATEWAY) == []


async def test_probe_runner_persists_gateway_samples(repo: Repository, monkeypatch) -> None:
    gw = repo.upsert_entity(
        Entity(entity_type=EntityType.GATEWAY, native_id="aa:bb:cc:00:00:g1"), ts=1000
    )
    repo.sync_entity_state(gw, {"ip": "192.168.1.1"}, ts=1000)

    class FakeDns:
        def __init__(self, **_: object) -> None:
            pass

        async def probe_once(self) -> list[ProbeSample]:
            return [
                ProbeSample(
                    metric=METRIC_DNS_ANCHOR_LATENCY,
                    ts=1000,
                    value=12.0,
                    ok=True,
                    target="1.1.1.1",
                    kind="dns",
                )
            ]

    class FakeRtt:
        def __init__(self, **_: object) -> None:
            pass

        async def probe_once(self) -> ProbeSample:
            return ProbeSample(
                metric=METRIC_GW_RTT,
                ts=1000,
                value=3.0,
                ok=True,
                target="192.168.1.1",
                kind="rtt",
            )

    monkeypatch.setattr(factory, "DnsProber", FakeDns)
    monkeypatch.setattr(factory, "RttProber", FakeRtt)

    runner = ProbeRunner(repo, Settings(_env_file=None), interval_s=60)
    await runner._cycle()

    dns_series = repo.get_series(gw, METRIC_DNS_ANCHOR_LATENCY)
    rtt_series = repo.get_series(gw, METRIC_GW_RTT)
    assert dns_series is not None
    assert repo.read_raw(dns_series, 0, 9_999_999)[0]["value"] == 12.0
    assert rtt_series is not None
    assert repo.read_raw(rtt_series, 0, 9_999_999)[0]["value"] == 3.0


class _FakeDns:
    def __init__(self, **_: object) -> None:
        pass

    async def probe_once(self) -> list[ProbeSample]:
        return [
            ProbeSample(
                metric=METRIC_DNS_ANCHOR_LATENCY,
                ts=1000,
                value=12.0,
                ok=True,
                target="1.1.1.1",
                kind="dns",
            )
        ]


class _FakeRtt:
    def __init__(self, **_: object) -> None:
        pass

    async def probe_once(self) -> ProbeSample:
        return ProbeSample(
            metric=METRIC_GW_RTT, ts=1000, value=3.0, ok=True, target="192.168.1.1", kind="rtt"
        )


async def test_probe_runner_runs_on_gateway_ip_without_any_gateway_entity(
    repo: Repository, monkeypatch
) -> None:
    # A third-party gateway: probe.gateway_ip is set but the controller adopts no
    # gateway, so no GATEWAY entity exists. Probes must still run, against a
    # synthetic probe-only target -- the blind spot Phase-2 validation found.
    monkeypatch.setattr(factory, "DnsProber", _FakeDns)
    monkeypatch.setattr(factory, "RttProber", _FakeRtt)
    settings = Settings(_env_file=None, probe=ProbeConfig(gateway_ip="192.168.1.1"))

    runner = ProbeRunner(repo, settings, interval_s=60)
    await runner._cycle()

    gateways = repo.list_entities(EntityType.GATEWAY)
    assert len(gateways) == 1
    target = gateways[0]
    assert str(target["native_id"]).startswith("probe_target:")
    eid = int(target["entity_id"])

    # Probe series landed on the synthetic target...
    rtt_series = repo.get_series(eid, METRIC_GW_RTT)
    dns_series = repo.get_series(eid, METRIC_DNS_ANCHOR_LATENCY)
    assert rtt_series is not None
    assert repo.read_raw(rtt_series, 0, 9_999_999)[0]["value"] == 3.0
    assert dns_series is not None
    # ...but it is a probe-only gateway: no wan_latency, so it never masquerades as
    # a UniFi WAN-health gateway (client.dhcp / WAN detectors stay correct).
    assert repo.get_series(eid, "wan_latency") is None

    # Idempotent: a second cycle reuses the one synthetic entity, never a duplicate.
    await runner._cycle()
    assert len(repo.list_entities(EntityType.GATEWAY)) == 1


async def test_supervisor_task_start_and_stop() -> None:
    started = asyncio.Event()

    class FakeSupervisor:
        def __init__(self) -> None:
            self._stop = asyncio.Event()

        def stop(self) -> None:
            self._stop.set()

        async def run(self) -> None:
            started.set()
            await self._stop.wait()

    task = SupervisorTask(FakeSupervisor())
    assert task.state == "stopped"
    await task.start()
    await asyncio.wait_for(started.wait(), timeout=1.0)
    assert task.state == "running"
    await task.stop()
    assert task.state == "stopped"
