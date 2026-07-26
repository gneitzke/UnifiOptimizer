"""Active probes: DNS timing, gateway RTT, and persistence, all mocked."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import netadmin.ingest.probes as probes_mod
from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.ingest.probes import (
    JOB_DNS,
    JOB_DNS_ANCHOR,
    METRIC_DNS_ANCHOR_LATENCY,
    METRIC_DNS_LATENCY,
    METRIC_GW_RTT,
    DnsProbeError,
    DnsProber,
    ProbeSample,
    RttProber,
    build_ping_args,
    parse_ping_rtt,
    persist_probe_samples,
)
from netadmin.store.repository import Repository

NOW = 1_900_000_000
GW_RESOLVER = "192.0.2.1"
ANCHOR = "1.1.1.1"


def _now() -> int:
    return NOW


# --------------------------------------------------------------------------- #
# build_ping_args: platform flag differences
# --------------------------------------------------------------------------- #
def test_ping_args_macos_uses_ms_wait_and_deadline():
    args = build_ping_args("10.0.0.1", 2.0, system="Darwin")
    assert args[:3] == ["ping", "-c", "1"]
    assert "-W" in args and args[args.index("-W") + 1] == "2000"  # ms on macOS
    assert "-t" in args and args[args.index("-t") + 1] == "2"  # seconds deadline
    assert args[-1] == "10.0.0.1"


def test_ping_args_linux_uses_seconds_wait():
    args = build_ping_args("10.0.0.1", 3.0, system="Linux")
    assert args[args.index("-W") + 1] == "3"  # seconds on Linux
    assert "-w" in args
    assert args[-1] == "10.0.0.1"


# --------------------------------------------------------------------------- #
# parse_ping_rtt
# --------------------------------------------------------------------------- #
def test_parse_ping_rtt_extracts_time():
    out = "64 bytes from 10.0.0.1: icmp_seq=0 ttl=64 time=1.234 ms"
    assert parse_ping_rtt(out) == 1.234


def test_parse_ping_rtt_sub_millisecond():
    assert parse_ping_rtt("... time<1 ms") == 1.0


def test_parse_ping_rtt_none_when_no_reply():
    assert parse_ping_rtt("Request timeout for icmp_seq 0") is None


# --------------------------------------------------------------------------- #
# _default_ping_runner: a timed-out child is killed AND reaped (no zombie)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_default_ping_runner_reaps_timed_out_process(monkeypatch):
    events: list[str] = []

    class FakeProc:
        returncode = None

        async def communicate(self):  # never resolves before the timeout
            await asyncio.sleep(100)
            return b"", b""  # pragma: no cover

        def kill(self):
            events.append("kill")

        async def wait(self):
            events.append("wait")
            return -9

    async def fake_exec(*_a, **_k):
        return FakeProc()

    async def fake_wait_for(coro, timeout):
        coro.close()  # avoid "coroutine was never awaited"
        raise asyncio.TimeoutError

    monkeypatch.setattr(probes_mod.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(probes_mod.asyncio, "wait_for", fake_wait_for)

    code, out = await probes_mod._default_ping_runner("10.0.0.1", 0.01)

    assert (code, out) == (1, "")
    # kill() alone leaves a zombie; the reap requires the following wait().
    assert events == ["kill", "wait"]


# --------------------------------------------------------------------------- #
# DnsProber
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_dns_probe_success_both_targets():
    async def query(name, nameserver, timeout):
        return None

    prober = DnsProber(gateway_resolver=GW_RESOLVER, query=query, now_fn=_now)
    samples = await prober.probe_once()

    assert {s.metric for s in samples} == {METRIC_DNS_LATENCY, METRIC_DNS_ANCHOR_LATENCY}
    assert all(s.ok and s.value is not None and s.value >= 0 for s in samples)
    assert all(s.ts == NOW for s in samples)


@pytest.mark.asyncio
async def test_dns_probe_without_gateway_resolver_only_anchor():
    async def query(name, nameserver, timeout):
        return None

    prober = DnsProber(gateway_resolver=None, query=query, now_fn=_now)
    samples = await prober.probe_once()
    assert len(samples) == 1
    assert samples[0].metric == METRIC_DNS_ANCHOR_LATENCY
    assert samples[0].target == ANCHOR


@pytest.mark.asyncio
async def test_dns_probe_classifies_servfail_on_gateway():
    async def query(name, nameserver, timeout):
        if nameserver == GW_RESOLVER:
            raise DnsProbeError("servfail")
        return None

    prober = DnsProber(gateway_resolver=GW_RESOLVER, query=query, now_fn=_now)
    samples = {s.metric: s for s in await prober.probe_once()}

    gw = samples[METRIC_DNS_LATENCY]
    assert gw.ok is False and gw.value is None and gw.failure == "servfail"
    assert samples[METRIC_DNS_ANCHOR_LATENCY].ok is True


@pytest.mark.asyncio
async def test_dns_probe_classifies_timeout():
    async def query(name, nameserver, timeout):
        raise DnsProbeError("timeout")

    prober = DnsProber(gateway_resolver=GW_RESOLVER, query=query, now_fn=_now)
    samples = await prober.probe_once()
    assert all(s.failure == "timeout" and not s.ok for s in samples)


@pytest.mark.asyncio
async def test_dns_probe_unexpected_exception_is_error():
    async def query(name, nameserver, timeout):
        raise RuntimeError("boom")

    prober = DnsProber(gateway_resolver=None, query=query, now_fn=_now)
    samples = await prober.probe_once()
    assert samples[0].failure == "error" and not samples[0].ok


@pytest.mark.asyncio
async def test_dns_probe_rotates_names():
    seen: list[str] = []

    async def query(name, nameserver, timeout):
        seen.append(name)
        return None

    prober = DnsProber(gateway_resolver=None, names=("a.test", "b.test"), query=query, now_fn=_now)
    await prober.probe_once()
    await prober.probe_once()
    assert seen == ["a.test", "b.test"]  # rotated between cycles


# --------------------------------------------------------------------------- #
# RttProber
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_rtt_icmp_success():
    async def ping(host, timeout):
        return 0, f"64 bytes from {host}: time=2.5 ms"

    prober = RttProber(gateway_ip="10.0.0.1", ping_runner=ping, now_fn=_now)
    s = await prober.probe_once()
    assert s.ok and s.value == 2.5 and s.detail["method"] == "icmp"


@pytest.mark.asyncio
async def test_rtt_falls_back_to_tcp_when_ping_fails():
    async def ping(host, timeout):
        return 1, ""  # non-zero exit

    async def tcp(host, port, timeout):
        return 7.0

    prober = RttProber(gateway_ip="10.0.0.1", ping_runner=ping, tcp_connector=tcp, now_fn=_now)
    s = await prober.probe_once()
    assert s.ok and s.value == 7.0 and s.detail["method"] == "tcp"


@pytest.mark.asyncio
async def test_rtt_falls_back_when_ping_answers_without_time():
    async def ping(host, timeout):
        return 0, "Request timeout for icmp_seq 0"

    async def tcp(host, port, timeout):
        return 3.3

    prober = RttProber(gateway_ip="10.0.0.1", ping_runner=ping, tcp_connector=tcp, now_fn=_now)
    s = await prober.probe_once()
    assert s.value == 3.3 and s.detail["method"] == "tcp"


@pytest.mark.asyncio
async def test_rtt_ping_spawn_error_falls_back():
    async def ping(host, timeout):
        raise OSError("no ping binary")

    async def tcp(host, port, timeout):
        return 1.1

    prober = RttProber(gateway_ip="10.0.0.1", ping_runner=ping, tcp_connector=tcp, now_fn=_now)
    s = await prober.probe_once()
    assert s.ok and s.value == 1.1


@pytest.mark.asyncio
async def test_rtt_unreachable_when_both_fail():
    async def ping(host, timeout):
        return 1, ""

    async def tcp(host, port, timeout):
        raise OSError("connection refused")

    prober = RttProber(gateway_ip="10.0.0.1", ping_runner=ping, tcp_connector=tcp, now_fn=_now)
    s = await prober.probe_once()
    assert not s.ok and s.value is None and s.failure == "unreachable"


# --------------------------------------------------------------------------- #
# persist_probe_samples
# --------------------------------------------------------------------------- #
@pytest.fixture
def repo(tmp_db_path: Path) -> Repository:
    r = Repository.open(tmp_db_path)
    yield r
    r.close()


def test_persist_writes_latency_gauges_and_failure_pollruns(repo: Repository):
    gw = repo.upsert_entity(
        Entity(entity_type=EntityType.GATEWAY, native_id="aa:bb:cc:dd:ee:ff", name="gw"), ts=NOW
    )
    samples = [
        ProbeSample(METRIC_DNS_LATENCY, NOW, 12.5, True, GW_RESOLVER, "dns"),
        ProbeSample(METRIC_DNS_ANCHOR_LATENCY, NOW, None, False, ANCHOR, "dns", failure="servfail"),
    ]
    written = persist_probe_samples(repo, gw, samples)
    assert written == 1

    series = repo.get_series(gw, METRIC_DNS_LATENCY)
    raw = repo.read_raw(series, NOW - 1, NOW + 1)
    assert raw[0]["value"] == 12.5

    # Success -> ok poll_run; failure -> failed poll_run with the classified reason.
    ok_runs = repo.read_poll_runs(JOB_DNS, NOW - 1, NOW + 1)
    fail_runs = repo.read_poll_runs(JOB_DNS_ANCHOR, NOW - 1, NOW + 1)
    assert ok_runs[0]["ok"] == 1
    assert fail_runs[0]["ok"] == 0 and fail_runs[0]["error"] == "servfail"


def test_persist_rtt_failure_records_no_sample(repo: Repository):
    gw = repo.upsert_entity(
        Entity(entity_type=EntityType.GATEWAY, native_id="aa:bb:cc:dd:ee:01", name="gw"), ts=NOW
    )
    samples = [
        ProbeSample(METRIC_GW_RTT, NOW, None, False, "10.0.0.1", "rtt", failure="unreachable"),
    ]
    written = persist_probe_samples(repo, gw, samples)
    assert written == 0
    assert repo.get_series(gw, METRIC_GW_RTT) is None


@pytest.mark.asyncio
async def test_tcp_connector_counts_a_refused_connection_as_a_measurement(monkeypatch) -> None:
    """A refused port still completed a round trip, so it is an RTT sample.

    Regression for a live deployment where the gateway refused 443 and answered
    every other port in under a millisecond. Treating the refusal as a miss left
    probe.gw_rtt with 5,329 consecutive failures against a healthy gateway.
    """
    from netadmin.ingest import probes as probes_mod

    async def _refuse(host, port):
        raise ConnectionRefusedError(61, "Connection refused")

    monkeypatch.setattr(probes_mod.asyncio, "open_connection", _refuse)
    elapsed = await probes_mod._default_tcp_connector("192.0.2.1", 443, 2.0)
    assert elapsed >= 0.0


@pytest.mark.asyncio
async def test_tcp_connector_still_raises_when_the_host_never_answers(monkeypatch) -> None:
    """A timeout is a genuine miss and must still propagate."""
    from netadmin.ingest import probes as probes_mod

    async def _hang(host, port):
        await asyncio.sleep(10)

    monkeypatch.setattr(probes_mod.asyncio, "open_connection", _hang)
    with pytest.raises(asyncio.TimeoutError):
        await probes_mod._default_tcp_connector("192.0.2.1", 443, 0.01)
