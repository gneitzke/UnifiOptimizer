"""LAN discovery service tests (netadmin/server/services/discovery.py), offline.

The scanner is exercised without touching the network: ``_local_ipv4_addresses``,
``_port_open`` and ``detect_console`` are monkeypatched, so every case asserts the
service's *logic* -- the RFC1918 guard, the /24 derivation, the 443-over-8443
dedupe, and that only confirmed UniFi consoles survive -- with zero real sockets.
"""

from __future__ import annotations

from typing import Any

import pytest

from netadmin.ingest.unifi.detect import (
    KIND_CLOUDKEY_GEN2_PLUS,
    KIND_LEGACY_SOFTWARE,
    KIND_UNREACHABLE,
    ConsoleInfo,
)
from netadmin.server.services import discovery


def _info(kind: str, *, reachable: bool = True, model: str | None = None) -> ConsoleInfo:
    return ConsoleInfo(
        kind=kind,
        model=model,
        is_unifi_os=kind != KIND_LEGACY_SOFTWARE,
        network_version="9.1.0",
        api_key_supported=True,
        recommended_auth="api_key",
        reachable=reachable,
    )


# --------------------------------------------------------------------------- #
# local_private_networks: the RFC1918 guard
# --------------------------------------------------------------------------- #
def test_local_networks_keeps_only_private_24s(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        discovery,
        "_local_ipv4_addresses",
        lambda: {"192.168.1.50", "10.0.5.9", "127.0.0.1", "8.8.8.8"},
    )
    nets = [str(n) for n in discovery.local_private_networks()]
    # Public (8.8.8.8, globally routable) and loopback (127.0.0.1) are excluded;
    # the two private addresses collapse to their /24s.
    assert "192.168.1.0/24" in nets
    assert "10.0.5.0/24" in nets
    assert all(not (n.startswith("8.") or n.startswith("127.")) for n in nets)


def test_local_networks_dedupes_same_subnet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        discovery,
        "_local_ipv4_addresses",
        lambda: {"192.168.1.10", "192.168.1.20", "192.168.1.30"},
    )
    nets = discovery.local_private_networks()
    assert [str(n) for n in nets] == ["192.168.1.0/24"]


def test_local_networks_public_only_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "_local_ipv4_addresses", lambda: {"8.8.8.8", "127.0.0.1"})
    assert discovery.local_private_networks() == []


def test_local_networks_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        discovery,
        "_local_ipv4_addresses",
        lambda: {"10.0.0.1", "10.0.1.1", "10.0.2.1", "10.0.3.1", "10.0.4.1"},
    )
    assert len(discovery.local_private_networks(max_networks=2)) == 2


# --------------------------------------------------------------------------- #
# discover_consoles: end to end (guard + scan + confirm), all faked
# --------------------------------------------------------------------------- #
def _patch_lan(monkeypatch: pytest.MonkeyPatch, addresses: set[str]) -> None:
    monkeypatch.setattr(discovery, "_local_ipv4_addresses", lambda: addresses)


def _patch_open_ports(
    monkeypatch: pytest.MonkeyPatch, open_map: dict[tuple[str, int], bool]
) -> None:
    async def _fake_open(ip: str, port: int, timeout: float) -> bool:
        return open_map.get((ip, port), False)

    monkeypatch.setattr(discovery, "_port_open", _fake_open)


def _patch_detect(monkeypatch: pytest.MonkeyPatch, by_host: dict[str, ConsoleInfo]) -> list[str]:
    seen: list[str] = []

    async def _fake_detect(host: str, *, timeout: float = 8.0, **_kw: Any) -> ConsoleInfo:
        seen.append(host)
        ip = host.split("//", 1)[1].split(":", 1)[0]
        return by_host.get(ip, _info(KIND_UNREACHABLE, reachable=False))

    monkeypatch.setattr(discovery, "detect_console", _fake_detect)
    return seen


@pytest.mark.asyncio
async def test_discover_finds_and_confirms_console(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_lan(monkeypatch, {"192.168.1.100"})
    _patch_open_ports(monkeypatch, {("192.168.1.1", 443): True})
    _patch_detect(monkeypatch, {"192.168.1.1": _info(KIND_CLOUDKEY_GEN2_PLUS, model="UCK-G2-Plus")})

    result = await discovery.discover_consoles(connect_timeout=0.01, confirm_timeout=0.01)

    assert result.scanned == ["192.168.1.0/24"]
    assert len(result.candidates) == 1
    cand = result.candidates[0]
    assert cand.host == "https://192.168.1.1"
    assert cand.kind == KIND_CLOUDKEY_GEN2_PLUS
    assert "CloudKey" in cand.label


@pytest.mark.asyncio
async def test_discover_prefers_443_over_8443(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_lan(monkeypatch, {"192.168.1.100"})
    _patch_open_ports(
        monkeypatch,
        {("192.168.1.1", 443): True, ("192.168.1.1", 8443): True},
    )
    seen = _patch_detect(monkeypatch, {"192.168.1.1": _info(KIND_CLOUDKEY_GEN2_PLUS)})

    result = await discovery.discover_consoles(connect_timeout=0.01, confirm_timeout=0.01)

    # One IP, one candidate, confirmed once -- the 443 hit wins the dedupe.
    assert len(result.candidates) == 1
    assert result.candidates[0].port == 443
    assert seen.count("https://192.168.1.1") == 1


@pytest.mark.asyncio
async def test_discover_legacy_keeps_8443_in_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_lan(monkeypatch, {"192.168.1.100"})
    _patch_open_ports(monkeypatch, {("192.168.1.9", 8443): True})
    _patch_detect(monkeypatch, {"192.168.1.9": _info(KIND_LEGACY_SOFTWARE)})

    result = await discovery.discover_consoles(connect_timeout=0.01, confirm_timeout=0.01)

    assert len(result.candidates) == 1
    assert result.candidates[0].host == "https://192.168.1.9:8443"


@pytest.mark.asyncio
async def test_discover_drops_unconfirmed_open_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    # An open 443 that does NOT fingerprint as a UniFi console (e.g. a random
    # HTTPS box) must not become a candidate -- confirmed, not guessed.
    _patch_lan(monkeypatch, {"192.168.1.100"})
    _patch_open_ports(monkeypatch, {("192.168.1.5", 443): True})
    _patch_detect(monkeypatch, {})  # everything answers "unreachable"

    result = await discovery.discover_consoles(connect_timeout=0.01, confirm_timeout=0.01)

    assert result.scanned == ["192.168.1.0/24"]
    assert result.candidates == []


@pytest.mark.asyncio
async def test_discover_no_private_network_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_lan(monkeypatch, {"8.8.8.8"})
    # If the guard did not stop us, this would still find nothing -- but assert the
    # guard short-circuits: no scan attempted, empty scanned list.
    calls: list[Any] = []

    async def _boom(*_a: Any, **_k: Any) -> bool:
        calls.append(1)
        return False

    monkeypatch.setattr(discovery, "_port_open", _boom)

    result = await discovery.discover_consoles(connect_timeout=0.01)

    assert result.scanned == []
    assert result.candidates == []
    assert calls == []  # the RFC1918 guard meant no probe ran at all
