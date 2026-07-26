"""Local-network discovery: scan the LAN for a UniFi console (first-run assist).

The first-run SetupFlow (ARCHITECTURE.md 18) asks the user for a controller
address. Most people do not know their console's IP off-hand, so this service
does the "look around my network for me" step: it probes the common UniFi HTTPS
ports (443 for UniFi OS consoles, 8443 for a legacy self-hosted controller)
across the machine's own private /24, then **confirms** each open host with the
read-only :func:`~netadmin.ingest.unifi.detect.detect_console` fingerprint so we
only ever pre-fill an address that actually answered as a UniFi console.

Ported from the pre-rebuild ``server/services/discovery.py`` (salvage map, §13)
with three hardenings:

* **RFC1918-guarded.** The scanner only ever touches addresses inside the
  machine's own private networks (10/8, 172.16/12, 192.168/16, link-local
  169.254/16). A host on a public IP scans nothing and returns an honest empty
  result -- this tool never sweeps a routable range.
* **Auto-detected subnet.** The old scanner hard-coded ``192.168.1``; this one
  derives the /24 from the host's own interface addresses, so it works on a
  ``10.x`` or ``172.x`` LAN without configuration.
* **Confirmed, not guessed.** An open 443/8443 port is only a candidate; each is
  run through the login-free console fingerprint and dropped unless it answers as
  a real UniFi console. No device-type guessing from the port number.

Every probe is a bare TCP connect (a SYN, immediately closed) plus the existing
read-only console fingerprint -- nothing here logs into, or mutates, anything.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Any, Optional

from netadmin.ingest.unifi.detect import (
    KIND_LEGACY_SOFTWARE,
    LEGACY_PORT,
    PLAYBOOK,
    ConsoleInfo,
    detect_console,
)
from netadmin.logging import get_logger

_log = get_logger("server.discovery")

# Common UniFi controller HTTPS ports: 443 = UniFi OS console (UDM/CloudKey/UCG),
# 8443 = legacy self-hosted Network controller. 8843 (old portal) is intentionally
# dropped -- it is not a console management port and only slows the sweep.
UNIFI_PORTS: tuple[int, ...] = (443, LEGACY_PORT)

# Bounds that keep a scan bare-TCP-cheap and time-boxed. A /24 is 254 hosts; we
# cap the number of /24s so a machine straddling several private networks cannot
# turn "scan for my controller" into a minutes-long sweep.
_MAX_NETWORKS = 3
_CONNECT_TIMEOUT_S = 0.6
_CONFIRM_TIMEOUT_S = 6.0
_PROBE_CONCURRENCY = 128
_CONFIRM_CONCURRENCY = 8


@dataclass(frozen=True)
class DiscoveredConsole:
    """A LAN host that answered the read-only UniFi console fingerprint.

    ``host`` is the ready-to-use address for the setup field (an explicit
    ``https://`` scheme, and the ``:8443`` port for a legacy controller so the
    connect probe reaches it). ``label`` is the friendly console name.
    """

    host: str
    port: int
    kind: str
    label: str
    model: Optional[str]
    api_key_status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "kind": self.kind,
            "label": self.label,
            "model": self.model,
            "api_key_status": self.api_key_status,
        }


@dataclass(frozen=True)
class DiscoveryResult:
    """The outcome of one LAN scan: which /24s were swept and what was confirmed."""

    scanned: list[str]
    candidates: list[DiscoveredConsole]

    def as_dict(self) -> dict[str, Any]:
        return {
            "scanned": list(self.scanned),
            "candidates": [c.as_dict() for c in self.candidates],
        }


# --------------------------------------------------------------------------- #
# Local network detection (RFC1918-guarded)
# --------------------------------------------------------------------------- #
def _local_ipv4_addresses() -> set[str]:
    """Best-effort set of this machine's own IPv4 addresses.

    Combines the primary outbound address (found via a UDP socket that only sets
    a route -- it sends no packets) with whatever ``getaddrinfo`` resolves for the
    hostname. Either source may be empty on an odd host; the union is filtered to
    private addresses by the caller, so a stray public/loopback entry is harmless.
    """
    addrs: set[str] = set()

    # Primary outbound interface: connecting a datagram socket to a documentation
    # address (RFC 5737, never routed) picks the default-route source IP without
    # emitting traffic.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        addrs.add(sock.getsockname()[0])
    except OSError:
        pass
    finally:
        sock.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except (OSError, socket.gaierror):
        pass

    return addrs


def local_private_networks(*, max_networks: int = _MAX_NETWORKS) -> list[ipaddress.IPv4Network]:
    """The machine's own private /24 networks -- the only ranges a scan may touch.

    Each local IPv4 address that is **private** (RFC1918 or link-local) contributes
    its /24. Loopback and any public address are excluded, so the scanner can never
    sweep a routable network. Capped at ``max_networks`` /24s so the sweep stays
    bounded. Result is sorted for a stable, testable order.
    """
    networks: dict[str, ipaddress.IPv4Network] = {}
    for raw in _local_ipv4_addresses():
        try:
            addr = ipaddress.IPv4Address(raw)
        except ipaddress.AddressValueError:
            continue
        # RFC1918-guard: only the host's own private space is ever scanned. A
        # link-local (169.254/16) address still describes a reachable L2 segment;
        # loopback and public addresses never scan.
        if not addr.is_private or addr.is_loopback:
            continue
        net = ipaddress.ip_network(f"{addr}/24", strict=False)
        networks[str(net)] = net  # type: ignore[assignment]
    ordered = sorted(networks.values(), key=lambda n: int(n.network_address))
    return ordered[:max_networks]


# --------------------------------------------------------------------------- #
# Port probing (bare TCP connect, read-only)
# --------------------------------------------------------------------------- #
async def _port_open(ip: str, port: int, timeout: float) -> bool:
    """Whether ``ip:port`` accepts a TCP connection (a SYN, immediately closed)."""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    try:
        writer.close()
        await writer.wait_closed()
    except OSError:
        pass
    return True


async def _scan_ports(
    networks: list[ipaddress.IPv4Network],
    *,
    ports: tuple[int, ...],
    timeout: float,
    concurrency: int,
) -> dict[str, int]:
    """Sweep every host in ``networks`` for an open UniFi port.

    Returns ``{ip: port}`` keeping one port per IP, preferring 443 (the UniFi OS
    console port) over 8443 so a console exposing both is confirmed as UniFi OS.
    Bounded by a semaphore so the sweep is a burst of cheap connects, not a flood.
    """
    sem = asyncio.Semaphore(concurrency)
    found: dict[str, int] = {}

    async def probe(ip: str, port: int) -> None:
        async with sem:
            if await _port_open(ip, port, timeout):
                # Keep the lowest-numbered open port per IP, so 443 (UniFi OS
                # console) wins over 8443 (legacy) when a host answers on both.
                existing = found.get(ip)
                if existing is None or port < existing:
                    found[ip] = port

    tasks: list[asyncio.Task[None]] = []
    for net in networks:
        for host in net.hosts():
            ip = str(host)
            for port in ports:
                tasks.append(asyncio.ensure_future(probe(ip, port)))
    if tasks:
        await asyncio.gather(*tasks)
    return found


# --------------------------------------------------------------------------- #
# Confirmation (read-only console fingerprint)
# --------------------------------------------------------------------------- #
def _candidate_host(ip: str, info: ConsoleInfo) -> str:
    """The address to pre-fill for a confirmed console (legacy keeps its :8443)."""
    if info.kind == KIND_LEGACY_SOFTWARE:
        return f"https://{ip}:{LEGACY_PORT}"
    return f"https://{ip}"


async def _confirm(ip: str, port: int, *, timeout: float) -> Optional[DiscoveredConsole]:
    """Confirm one open host is a UniFi console via the login-free fingerprint."""
    try:
        info = await detect_console(f"https://{ip}", timeout=timeout)
    except Exception:  # noqa: BLE001 - a scan candidate must never raise out
        _log.debug("console confirm failed for %s", ip, exc_info=True)
        return None
    if not info.reachable:
        return None
    play = PLAYBOOK.get(info.kind)
    label = play.label if play else "UniFi console"
    return DiscoveredConsole(
        host=_candidate_host(ip, info),
        port=port,
        kind=info.kind,
        label=label,
        model=info.model,
        api_key_status=info.api_key_status,
    )


async def _confirm_all(
    open_hosts: dict[str, int], *, timeout: float, concurrency: int
) -> list[DiscoveredConsole]:
    sem = asyncio.Semaphore(concurrency)

    async def run(ip: str, port: int) -> Optional[DiscoveredConsole]:
        async with sem:
            return await _confirm(ip, port, timeout=timeout)

    results = await asyncio.gather(*(run(ip, port) for ip, port in open_hosts.items()))
    confirmed = [c for c in results if c is not None]
    confirmed.sort(key=lambda c: ipaddress.IPv4Address(c.host.split("//", 1)[1].split(":", 1)[0]))
    return confirmed


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
async def discover_consoles(
    *,
    ports: tuple[int, ...] = UNIFI_PORTS,
    connect_timeout: float = _CONNECT_TIMEOUT_S,
    confirm_timeout: float = _CONFIRM_TIMEOUT_S,
    max_networks: int = _MAX_NETWORKS,
) -> DiscoveryResult:
    """Scan the machine's own private /24(s) for reachable UniFi consoles.

    Returns the networks that were swept and the confirmed consoles (each already a
    ready-to-use address). On a host with no private network -- or when nothing
    answers -- it returns an honest empty ``candidates`` list, never an error. The
    caller (the setup router) turns an empty result into "none found, enter it
    manually". Read-only throughout: bare TCP connects plus the login-free console
    fingerprint; it never authenticates to or mutates a controller.
    """
    networks = local_private_networks(max_networks=max_networks)
    scanned = [str(n) for n in networks]
    if not networks:
        return DiscoveryResult(scanned=[], candidates=[])

    open_hosts = await _scan_ports(
        networks, ports=ports, timeout=connect_timeout, concurrency=_PROBE_CONCURRENCY
    )
    if not open_hosts:
        return DiscoveryResult(scanned=scanned, candidates=[])

    candidates = await _confirm_all(
        open_hosts, timeout=confirm_timeout, concurrency=_CONFIRM_CONCURRENCY
    )
    return DiscoveryResult(scanned=scanned, candidates=candidates)


__all__ = [
    "DiscoveredConsole",
    "DiscoveryResult",
    "discover_consoles",
    "local_private_networks",
    "UNIFI_PORTS",
]
