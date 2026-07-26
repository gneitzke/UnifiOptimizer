"""Active probes (ARCHITECTURE.md 5.4).

The controller reports no DNS or path-latency timing at all, so a small prober
runs alongside the pollers and measures what the controller cannot:

* **DNS resolution timing** -- resolves a rotating set of test names against
  both the gateway's own resolver *and* one public anchor (1.1.1.1). Comparing
  the two separates a slow *local* resolver from slow *upstream* DNS
  (``wan.dns_slow`` in the catalog). Latencies are gauge samples; SERVFAILs and
  timeouts are recorded as failures, never as a fake latency.

* **Gateway RTT** -- one ICMP echo via ``ping -c1`` (flags differ on macOS vs
  Linux; both are handled), falling back to timing a TCP connect to
  ``gateway:443`` when ICMP is unavailable. Feeds ``wan.bufferbloat`` (loaded
  RTT minus idle) and reachability.

Everything here is async and needs no root: unprivileged ICMP works on modern
macOS/Linux, and the TCP fallback never needs privilege. The measurement core
is pure and dependency-injected -- the DNS query callable, the ping runner, and
the TCP connector are all overridable -- so the unit tests exercise every path
(fast, slow, SERVFAIL, timeout, unreachable, platform flag differences) with no
real network, no subprocess, and without requiring ``dnspython`` to be
installed. ``dnspython`` is imported lazily inside the default query callable
only; it is a runtime dependency of live probing (add ``dnspython>=2`` to the
project deps) but not of importing this module or running its tests.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional, Sequence

from netadmin.logging import get_logger

logger = get_logger("ingest.probes")

# Public resolver used as the "is it me or my ISP" anchor (section 5.4).
DEFAULT_ANCHOR = "1.1.1.1"

# Rotating probe names: real, stable, well-distributed authoritative infra so a
# single slow zone does not skew the signal. One is queried per cycle, rotated.
DEFAULT_DNS_NAMES: tuple[str, ...] = (
    "example.com",
    "cloudflare.com",
    "google.com",
    "wikipedia.org",
    "amazon.com",
    "microsoft.com",
)

# Stored metric names (all gauges, all on the gateway entity).
METRIC_DNS_LATENCY = "dns_latency_ms"  # gateway resolver
METRIC_DNS_ANCHOR_LATENCY = "dns_anchor_latency_ms"  # public anchor
METRIC_GW_RTT = "gw_rtt_ms"

# poll_runs job identities for probe failure accounting.
JOB_DNS = "probe.dns"
JOB_DNS_ANCHOR = "probe.dns.anchor"
JOB_GW_RTT = "probe.gw_rtt"


class DnsProbeError(Exception):
    """A DNS query that failed to produce a timing.

    ``kind`` is one of ``"timeout"``, ``"servfail"``, or ``"error"`` so failures
    are classified rather than lumped together (a resolver timing out and a
    resolver returning SERVFAIL are different faults).
    """

    def __init__(self, kind: str, message: str = "") -> None:
        super().__init__(message or kind)
        self.kind = kind


# A DNS query: resolve ``name`` via ``nameserver`` within ``timeout`` seconds,
# returning None on success and raising DnsProbeError on failure. Injectable so
# tests need neither dnspython nor a network.
DnsQuery = Callable[[str, str, float], Awaitable[None]]
# A ping runner: run one echo to ``host`` with ``timeout`` seconds, returning
# ``(returncode, combined_output)``.
PingRunner = Callable[[str, float], Awaitable[tuple[int, str]]]
# A TCP connector: time a connect to ``host:port``; return elapsed ms or raise.
TcpConnector = Callable[[str, int, float], Awaitable[float]]


@dataclass
class ProbeSample:
    """One probe measurement.

    ``value`` is milliseconds on success and ``None`` on failure; ``ok`` and
    ``failure`` (the classified reason) describe the outcome. ``metric`` is the
    store metric name the value belongs on (all on the gateway entity).
    """

    metric: str
    ts: int
    value: Optional[float]
    ok: bool
    target: str
    kind: str  # "dns" | "rtt"
    failure: Optional[str] = None  # timeout | servfail | unreachable | error
    detail: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Default (real) I/O implementations -- injected over in tests
# --------------------------------------------------------------------------- #
async def _default_dns_query(name: str, nameserver: str, timeout: float) -> None:
    """Resolve ``name`` via ``nameserver`` using dnspython (lazy import).

    Raises :class:`DnsProbeError` with a classified ``kind`` on timeout /
    SERVFAIL / other error. A successful lookup -- including NXDOMAIN, which
    still proves the resolver answered -- returns None.
    """
    try:
        import dns.asyncresolver  # noqa: WPS433 - lazy: optional runtime dep
        import dns.exception
        import dns.resolver
    except ImportError as exc:  # pragma: no cover - exercised only without dep
        raise DnsProbeError("error", "dnspython not installed") from exc

    resolver = dns.asyncresolver.Resolver(configure=False)
    resolver.nameservers = [nameserver]
    resolver.lifetime = timeout
    resolver.timeout = timeout
    try:
        await resolver.resolve(name, "A")
    except dns.resolver.NXDOMAIN:
        return  # resolver responded authoritatively; that is a timing success
    except dns.exception.Timeout as exc:
        raise DnsProbeError("timeout", str(exc)) from exc
    except dns.resolver.NoNameservers as exc:
        raise DnsProbeError("servfail", str(exc)) from exc
    except dns.exception.DNSException as exc:
        raise DnsProbeError("error", str(exc)) from exc


def build_ping_args(host: str, timeout_s: float, *, system: Optional[str] = None) -> list[str]:
    """Build ``ping`` argv for a single echo, handling macOS vs Linux flags.

    macOS ``ping`` takes ``-W`` in **milliseconds** (per-reply wait) and ``-t``
    as a total deadline in seconds; Linux ``ping`` takes ``-W`` in **seconds**
    (per-reply wait) and ``-w`` as a total deadline in seconds. Getting this
    wrong makes a 2-second timeout read as 2 milliseconds (macOS) or vice versa,
    so the flag units are branched explicitly.
    """
    import platform

    sysname = (system or platform.system()).lower()
    timeout_s = max(timeout_s, 0.001)
    if sysname == "darwin":
        wait_ms = str(int(round(timeout_s * 1000)))
        deadline_s = str(max(1, int(round(timeout_s))))
        return ["ping", "-c", "1", "-W", wait_ms, "-t", deadline_s, host]
    # Linux and other unixes: -W seconds per-reply, -w seconds deadline.
    wait_s = str(max(1, int(round(timeout_s))))
    return ["ping", "-c", "1", "-W", wait_s, "-w", wait_s, host]


def parse_ping_rtt(output: str) -> Optional[float]:
    """Extract the round-trip time in ms from ``ping`` output, or None.

    Both macOS and Linux print ``time=1.23 ms`` (or ``time<1 ms``); this pulls
    the first such figure. Returns None when no reply line is present (the host
    did not answer), which the caller treats as an ICMP miss.
    """
    import re

    match = re.search(r"time[=<]\s*([\d.]+)\s*ms", output)
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:  # pragma: no cover - regex already constrains this
        return None


async def _default_ping_runner(host: str, timeout: float) -> tuple[int, str]:
    """Run one ``ping`` echo as a subprocess; return (returncode, output)."""
    args = build_ping_args(host, timeout)
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout + 1.0)
    except asyncio.TimeoutError:
        # wait_for cancelled communicate(); the child is still alive. Kill it and
        # then REAP it: without an await on wait()/communicate() after kill() the
        # process stays a defunct zombie in the table until interpreter GC, and a
        # prober that times out every cycle would leak one per cycle.
        try:
            proc.kill()
        except ProcessLookupError:  # pragma: no cover - race: already exited
            pass
        finally:
            try:
                await proc.wait()
            except ProcessLookupError:  # pragma: no cover - reaped between kill/wait
                pass
        return 1, ""
    return proc.returncode or 0, stdout.decode("utf-8", "replace")


async def _default_tcp_connector(host: str, port: int, timeout: float) -> float:
    """Time a TCP connect to ``host:port``; return elapsed ms or raise OSError.

    A **refused** connection still measures RTT. The peer answered with an RST, so
    the packet completed a full round trip; only a timeout or an unreachable host
    is a real miss. This matters because plenty of gateways refuse 443 while
    answering instantly (a Starlink router does exactly that), and treating the
    refusal as a failure left the gateway-RTT probe reporting thousands of
    consecutive failures on a gateway that was replying in under a millisecond.
    """
    start = time.monotonic()
    try:
        fut = asyncio.open_connection(host, port)
        reader_writer = await asyncio.wait_for(fut, timeout=timeout)
    except ConnectionRefusedError:
        return (time.monotonic() - start) * 1000.0
    elapsed_ms = (time.monotonic() - start) * 1000.0
    writer = reader_writer[1]
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:  # noqa: BLE001 - close errors do not invalidate the timing
        pass
    return elapsed_ms


# --------------------------------------------------------------------------- #
# DNS prober
# --------------------------------------------------------------------------- #
class DnsProber:
    """Times DNS resolution against the gateway resolver and a public anchor."""

    def __init__(
        self,
        *,
        gateway_resolver: Optional[str],
        anchor: str = DEFAULT_ANCHOR,
        names: Sequence[str] = DEFAULT_DNS_NAMES,
        timeout: float = 2.0,
        query: Optional[DnsQuery] = None,
        clock: Callable[[], float] = time.monotonic,
        now_fn: Callable[[], int] = None,  # type: ignore[assignment]
    ) -> None:
        if not names:
            raise ValueError("names must be non-empty")
        self._gateway_resolver = gateway_resolver
        self._anchor = anchor
        self._names = tuple(names)
        self._timeout = timeout
        self._query = query or _default_dns_query
        self._clock = clock
        if now_fn is None:
            now_fn = lambda: int(time.time())  # noqa: E731
        self._now_fn = now_fn
        self._rotation = 0

    def _next_name(self) -> str:
        name = self._names[self._rotation % len(self._names)]
        self._rotation += 1
        return name

    async def probe_once(self) -> list[ProbeSample]:
        """Resolve the next rotating name against resolver + anchor.

        Returns up to two samples (one per target). The gateway-resolver target
        is skipped when no resolver address is configured.
        """
        name = self._next_name()
        ts = self._now_fn()
        targets: list[tuple[str, str]] = []
        if self._gateway_resolver:
            targets.append((METRIC_DNS_LATENCY, self._gateway_resolver))
        targets.append((METRIC_DNS_ANCHOR_LATENCY, self._anchor))

        async def measure(metric: str, nameserver: str) -> ProbeSample:
            start = self._clock()
            try:
                await self._query(name, nameserver, self._timeout)
            except DnsProbeError as exc:
                return ProbeSample(
                    metric=metric,
                    ts=ts,
                    value=None,
                    ok=False,
                    target=nameserver,
                    kind="dns",
                    failure=exc.kind,
                    detail={"name": name},
                )
            except Exception as exc:  # noqa: BLE001 - any resolver crash is a failure
                return ProbeSample(
                    metric=metric,
                    ts=ts,
                    value=None,
                    ok=False,
                    target=nameserver,
                    kind="dns",
                    failure="error",
                    detail={"name": name, "error": type(exc).__name__},
                )
            elapsed_ms = (self._clock() - start) * 1000.0
            return ProbeSample(
                metric=metric,
                ts=ts,
                value=elapsed_ms,
                ok=True,
                target=nameserver,
                kind="dns",
                detail={"name": name},
            )

        return await asyncio.gather(*(measure(m, ns) for m, ns in targets))


# --------------------------------------------------------------------------- #
# Gateway RTT prober
# --------------------------------------------------------------------------- #
class RttProber:
    """Measures gateway RTT via ICMP, falling back to a TCP-connect timing."""

    def __init__(
        self,
        *,
        gateway_ip: str,
        tcp_port: int = 443,
        timeout: float = 2.0,
        ping_runner: Optional[PingRunner] = None,
        tcp_connector: Optional[TcpConnector] = None,
        now_fn: Callable[[], int] = None,  # type: ignore[assignment]
    ) -> None:
        if not gateway_ip:
            raise ValueError("gateway_ip is required")
        self._gateway_ip = gateway_ip
        self._tcp_port = tcp_port
        self._timeout = timeout
        self._ping = ping_runner or _default_ping_runner
        self._tcp = tcp_connector or _default_tcp_connector
        if now_fn is None:
            now_fn = lambda: int(time.time())  # noqa: E731
        self._now_fn = now_fn

    async def probe_once(self) -> ProbeSample:
        """One RTT measurement: ICMP if it answers, else a TCP-connect timing."""
        ts = self._now_fn()
        rtt = await self._try_ping()
        if rtt is not None:
            return ProbeSample(
                metric=METRIC_GW_RTT,
                ts=ts,
                value=rtt,
                ok=True,
                target=self._gateway_ip,
                kind="rtt",
                detail={"method": "icmp"},
            )
        # ICMP unavailable or unanswered: fall back to TCP connect timing.
        try:
            elapsed_ms = await self._tcp(self._gateway_ip, self._tcp_port, self._timeout)
        except (OSError, asyncio.TimeoutError) as exc:
            return ProbeSample(
                metric=METRIC_GW_RTT,
                ts=ts,
                value=None,
                ok=False,
                target=self._gateway_ip,
                kind="rtt",
                failure="unreachable",
                detail={"method": "tcp", "error": type(exc).__name__},
            )
        return ProbeSample(
            metric=METRIC_GW_RTT,
            ts=ts,
            value=elapsed_ms,
            ok=True,
            target=self._gateway_ip,
            kind="rtt",
            detail={"method": "tcp", "port": self._tcp_port},
        )

    async def _try_ping(self) -> Optional[float]:
        """Return ICMP RTT in ms, or None if ping failed / did not answer."""
        try:
            code, output = await self._ping(self._gateway_ip, self._timeout)
        except Exception as exc:  # noqa: BLE001 - subprocess spawn failure -> fallback
            logger.debug("ping runner failed for %s: %s", self._gateway_ip, exc)
            return None
        if code != 0:
            return None
        return parse_ping_rtt(output)


# --------------------------------------------------------------------------- #
# Persistence helper
# --------------------------------------------------------------------------- #
def persist_probe_samples(
    repo,  # netadmin.store.repository.Repository (untyped to avoid import cost)
    gateway_entity_id: int,
    samples: Sequence[ProbeSample],
) -> int:
    """Write probe samples to the store against the gateway entity.

    Successful latencies become gauge samples on ``gateway_entity_id``.
    Failures are recorded as ``poll_runs`` failures (``ok=0``, ``source='live'``,
    the classified reason in ``error``) rather than fabricated latency values --
    the honest "we probed and it failed" signal the ``wan.dns_slow`` detector
    reads via ``coverage_breakdown``, never a zero or a guessed number
    (ARCHITECTURE.md section 4: never write zeros for unreachable). Returns the
    count of latency samples written.
    """
    from netadmin.store.repository import SampleReading

    readings: list[SampleReading] = []
    written = 0
    for s in samples:
        job = {
            METRIC_DNS_LATENCY: JOB_DNS,
            METRIC_DNS_ANCHOR_LATENCY: JOB_DNS_ANCHOR,
            METRIC_GW_RTT: JOB_GW_RTT,
        }.get(s.metric, f"probe.{s.metric}")
        if s.ok and s.value is not None:
            readings.append(
                SampleReading(
                    entity_id=gateway_entity_id,
                    metric=s.metric,
                    ts=s.ts,
                    value=float(s.value),
                    unit="ms",
                )
            )
            repo.record_poll_run(job=job, ok=True, ts=s.ts)
        else:
            repo.record_poll_run(job=job, ok=False, ts=s.ts, error=(s.failure or "error"))
    if readings:
        written = repo.record_samples(readings)
    return written


__all__ = [
    "DEFAULT_ANCHOR",
    "DEFAULT_DNS_NAMES",
    "METRIC_DNS_LATENCY",
    "METRIC_DNS_ANCHOR_LATENCY",
    "METRIC_GW_RTT",
    "JOB_DNS",
    "JOB_DNS_ANCHOR",
    "JOB_GW_RTT",
    "DnsProbeError",
    "DnsQuery",
    "PingRunner",
    "TcpConnector",
    "ProbeSample",
    "DnsProber",
    "RttProber",
    "build_ping_args",
    "parse_ping_rtt",
    "persist_probe_samples",
]
