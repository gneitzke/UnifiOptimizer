"""Network discovery service — scan for UniFi controllers on the local network."""

import asyncio
import socket
import time
from typing import List

from server.models.schemas import DiscoveredDevice

# Common UniFi controller ports
UNIFI_PORTS = [443, 8443, 8843]

# Known device type signatures
DEVICE_SIGNATURES = {
    "CloudKey Gen2": ["UCK-G2", "UCK-G2-PLUS", "UniFi Cloud Key"],
    "Dream Machine": ["UDM", "UDM-SE"],
    "Dream Machine Pro": ["UDM-Pro", "UDMPRO", "UDM-Pro-SE"],
    "Self-hosted": [],
}


async def scan_network(
    subnet: str = "192.168.1",
    port_list: List[int] = None,
    timeout: float = 1.0,
    progress_callback=None,
) -> List[DiscoveredDevice]:
    """Scan the local subnet for UniFi controller devices.

    Args:
        subnet: First 3 octets of the subnet to scan (e.g., '192.168.1').
        port_list: Ports to probe (default: common UniFi ports).
        timeout: Socket timeout per probe in seconds.
        progress_callback: Optional async callback(progress_pct, message).

    Returns:
        List of discovered devices (deduplicated by IP, preferring port 443).
    """
    if port_list is None:
        port_list = UNIFI_PORTS

    raw_devices = []
    total = 254 * len(port_list)
    checked = 0

    # Scan common gateway IPs first (1, 254), then the rest
    priority_hosts = [1, 254]
    all_hosts = priority_hosts + [i for i in range(2, 254) if i not in priority_hosts]

    tasks = []
    for host_octet in all_hosts:
        ip = f"{subnet}.{host_octet}"
        for port in port_list:
            tasks.append(_probe_host(ip, port, timeout))

    # Run probes concurrently in batches
    batch_size = 50
    for i in range(0, len(tasks), batch_size):
        batch = tasks[i : i + batch_size]
        results = await asyncio.gather(*batch, return_exceptions=True)

        for result in results:
            checked += 1
            if isinstance(result, DiscoveredDevice):
                raw_devices.append(result)

        if progress_callback:
            pct = int((checked / total) * 100)
            await progress_callback(pct, f"Scanned {checked}/{total} endpoints")

    # Deduplicate: keep one entry per IP, prefer port 443
    ip_map: dict[str, DiscoveredDevice] = {}
    for d in raw_devices:
        clean = d.host.replace("https://", "").replace("http://", "")
        if clean not in ip_map or d.port == 443:
            ip_map[clean] = d
    return list(ip_map.values())


async def _probe_host(ip: str, port: int, timeout: float):
    """Probe a single host:port for a UniFi controller."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port), timeout=timeout
        )
        writer.close()
        await writer.wait_closed()

        device_type = _guess_device_type(port)
        hostname = await _reverse_lookup(ip)
        return DiscoveredDevice(
            host=f"https://{ip}",
            port=port,
            device_type=device_type,
            name=f"UniFi Controller ({ip}:{port})",
            hostname=f"https://{hostname}" if hostname else None,
        )
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
        return None


async def _reverse_lookup(ip: str) -> str | None:
    """Attempt reverse DNS lookup; return hostname or None."""
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, socket.gethostbyaddr, ip),
            timeout=1.0,
        )
        return result[0]
    except (socket.herror, socket.gaierror, asyncio.TimeoutError, OSError):
        return None


def _guess_device_type(port: int) -> str:
    """Guess device type from port number."""
    if port == 443:
        return "dream_machine"  # UDM/UDM-Pro uses 443
    if port == 8443:
        return "self_hosted"  # Traditional controller port
    return "unknown"


async def probe_single_host(host: str, port: int = 443, timeout: float = 3.0):
    """Probe a specific host to verify it's a UniFi controller.

    Returns DiscoveredDevice or None.
    """
    for p in ([port] if port else UNIFI_PORTS):
        # Strip protocol for socket probe
        clean_host = host.replace("https://", "").replace("http://", "").split(":")[0]
        result = await _probe_host(clean_host, p, timeout)
        if result:
            result.host = f"https://{clean_host}"
            return result
    return None
