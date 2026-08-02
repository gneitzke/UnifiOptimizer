"""Pydantic models for the UniFi controller read set (ARCHITECTURE.md 5.1).

Every model sets ``extra="allow"``: the controller's payloads are large,
version-dependent, and only partially documented, so we pin the fields the
detection layer needs and tolerate everything else. Field names that are not
valid Python identifiers (``system-stats``, ``1x_identity``) or that we rename
for clarity use aliases; ``populate_by_name=True`` keeps construction from
Python side ergonomic.

Nothing here validates strictly: fields are ``Optional`` because a given
firmware, device model, or client type may omit any of them. A missing field is
data ("this controller does not expose it"), never an error.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

# Envelope keys returned by the classic UniFi API: {"meta": {...}, "data": [...]}
_MODEL_CONFIG = ConfigDict(extra="allow", populate_by_name=True)


class _Base(BaseModel):
    model_config = _MODEL_CONFIG


class PortEntry(_Base):
    """One row of ``stat/device`` ``port_table``.

    Carries the wired-health signals the ``wired.*`` detectors consume:
    error counters, negotiated speed/duplex/autoneg, PoE draw, and SFP DOM.
    """

    port_idx: Optional[int] = None
    name: Optional[str] = None
    media: Optional[str] = None  # "GE", "SFP+", ...
    up: Optional[bool] = None
    enable: Optional[bool] = None
    is_uplink: Optional[bool] = None

    # Negotiated link
    speed: Optional[int] = None  # Mbps, negotiated
    speed_caps: Optional[int] = None  # capability bitmask
    full_duplex: Optional[bool] = None
    autoneg: Optional[bool] = None

    # Error / traffic counters (cumulative on the controller)
    rx_errors: Optional[int] = None
    tx_errors: Optional[int] = None
    rx_dropped: Optional[int] = None
    tx_dropped: Optional[int] = None
    rx_bytes: Optional[int] = None
    tx_bytes: Optional[int] = None
    rx_packets: Optional[int] = None
    tx_packets: Optional[int] = None
    rx_broadcast: Optional[int] = None
    tx_broadcast: Optional[int] = None
    rx_multicast: Optional[int] = None
    tx_multicast: Optional[int] = None
    bytes_r: Optional[float] = None  # instantaneous rate

    # PoE
    port_poe: Optional[bool] = None
    poe_enable: Optional[bool] = None
    poe_mode: Optional[str] = None
    poe_power: Optional[float] = None  # watts drawn
    poe_voltage: Optional[float] = None
    poe_current: Optional[float] = None
    poe_class: Optional[str] = None
    poe_good: Optional[bool] = None

    # SFP DOM (digital optical monitoring)
    sfp_found: Optional[bool] = None
    sfp_vendor: Optional[str] = None
    sfp_part: Optional[str] = None
    sfp_serial: Optional[str] = None
    sfp_temperature: Optional[float] = None
    sfp_voltage: Optional[float] = None
    sfp_current: Optional[float] = None
    sfp_rxpower: Optional[float] = None  # dBm
    sfp_txpower: Optional[float] = None  # dBm
    sfp_rxfault: Optional[bool] = None
    sfp_txfault: Optional[bool] = None

    satisfaction: Optional[float] = None
    stp_state: Optional[str] = None
    stp_pathcost: Optional[int] = None


class RadioTableStat(_Base):
    """One row of ``stat/device`` ``radio_table_stats``.

    Channel-utilization split (``cu_total`` / ``cu_self_*``) drives
    ``wifi.airtime_saturation``; retries and satisfaction corroborate.
    """

    name: Optional[str] = None  # "wifi0", "wifi1"
    radio: Optional[str] = None  # "ng", "na", "6e"
    channel: Optional[int] = None
    ht: Optional[int] = None  # channel width (MHz)
    tx_power: Optional[int] = None
    tx_power_mode: Optional[str] = None

    # Channel utilization (percent)
    cu_total: Optional[int] = None
    cu_self_rx: Optional[int] = None
    cu_self_tx: Optional[int] = None

    tx_retries: Optional[int] = None
    tx_packets: Optional[int] = None
    tx_bytes: Optional[int] = None
    rx_bytes: Optional[int] = None
    num_sta: Optional[int] = None
    user_num_sta: Optional[int] = None
    guest_num_sta: Optional[int] = None
    satisfaction: Optional[float] = None


class RadioTable(_Base):
    """One row of ``stat/device`` ``radio_table`` -- the *config* table, distinct
    from ``radio_table_stats`` (metrics). Carries the min-RSSI kick-roam setting
    (``wifi.min_rssi_misconfig``): ``min_rssi`` is a signed dBm floor, same unit
    as the detector's -70 dBm default, confirmed against a real controller.
    """

    name: Optional[str] = None  # "wifi0", "wifi1" -- joins to radio_table_stats
    radio: Optional[str] = None  # "ng", "na", "6e"
    min_rssi: Optional[int] = None
    min_rssi_enabled: Optional[bool] = None


class Uplink(_Base):
    """``stat/device`` ``uplink`` block: the device's path to its parent."""

    uplink_mac: Optional[str] = None
    uplink_remote_port: Optional[int] = None
    type: Optional[str] = None  # "wire" | "wireless"
    speed: Optional[int] = None
    full_duplex: Optional[bool] = None
    max_speed: Optional[int] = None
    rssi: Optional[int] = None  # wireless uplink: 0-based signal-quality index
    signal: Optional[int] = None  # wireless uplink: RSSI in dBm (negative)
    tx_rate: Optional[int] = None  # wireless uplink: negotiated tx rate (kbps)
    rx_rate: Optional[int] = None  # wireless uplink: negotiated rx rate (kbps)
    latency: Optional[int] = None
    drops: Optional[int] = None
    num_ports: Optional[int] = None
    up: Optional[bool] = None


class Device(_Base):
    """A row of ``stat/device`` (AP / switch / gateway)."""

    mac: Optional[str] = None
    model: Optional[str] = None
    type: Optional[str] = None  # "uap" | "usw" | "ugw" | "udm"
    name: Optional[str] = None
    ip: Optional[str] = None
    version: Optional[str] = None  # firmware
    adopted: Optional[bool] = None
    state: Optional[int] = None
    uptime: Optional[int] = None
    last_seen: Optional[int] = None
    satisfaction: Optional[float] = None
    # Device-level client count (AP only): the controller reports this at
    # ``stat/device`` top level, alongside (not instead of) each
    # ``radio_table_stats`` entry's own per-band ``num_sta`` -- confirmed against
    # a real recorded payload (tests/netadmin/unifi/fixtures/stat_device.json).
    num_sta: Optional[int] = None

    port_table: list[PortEntry] = Field(default_factory=list)
    radio_table_stats: list[RadioTableStat] = Field(default_factory=list)
    radio_table: list[RadioTable] = Field(default_factory=list)
    uplink: Optional[Uplink] = None

    # Aggregate PoE budget on switches
    total_max_power: Optional[float] = None
    total_used_power: Optional[float] = None

    # Chassis thermals. ``general_temperature`` is a **top-level** device field on
    # switch firmware, not a ``system-stats`` key -- recorded payloads and a live
    # US-16-150W both put it here. ``has_temperature`` / ``has_fan`` are hardware
    # capability flags: every AP reports ``has_temperature: false`` and carries no
    # sensor at all, so a detector must read the flag rather than treat silence as
    # "cool". ``overheating`` is the controller's own authoritative verdict.
    general_temperature: Optional[float] = None
    has_temperature: Optional[bool] = None
    overheating: Optional[bool] = None
    has_fan: Optional[bool] = None
    fan_level: Optional[int] = None

    system_stats: Optional[dict[str, Any]] = Field(default=None, alias="system-stats")


class Client(_Base):
    """A row of ``stat/sta`` (a connected client, wired or wireless)."""

    mac: Optional[str] = None
    hostname: Optional[str] = None
    name: Optional[str] = None
    ip: Optional[str] = None
    oui: Optional[str] = None
    is_wired: Optional[bool] = None

    # Association / placement
    ap_mac: Optional[str] = None
    essid: Optional[str] = None
    bssid: Optional[str] = None
    channel: Optional[int] = None
    radio: Optional[str] = None
    radio_proto: Optional[str] = None

    # Wired path
    sw_mac: Optional[str] = None
    sw_port: Optional[int] = None

    # Wireless health
    rssi: Optional[int] = None
    signal: Optional[int] = None
    noise: Optional[int] = None
    satisfaction: Optional[float] = None
    tx_rate: Optional[int] = None
    rx_rate: Optional[int] = None
    tx_retries: Optional[int] = None
    wifi_tx_attempts: Optional[int] = None
    tx_power: Optional[int] = None
    idletime: Optional[int] = None
    powersave_enabled: Optional[bool] = None
    anomalies: Optional[int] = None

    roam_count: Optional[int] = None
    assoc_time: Optional[int] = None
    latest_assoc_time: Optional[int] = None
    uptime: Optional[int] = None
    is_11r: Optional[bool] = None
    authorized: Optional[bool] = None


class HealthSubsystem(_Base):
    """A row of ``stat/health``: one controller subsystem's status."""

    subsystem: Optional[str] = None  # "wan" | "wlan" | "lan" | "www" | "vpn"
    status: Optional[str] = None  # "ok" | "warning" | "error" | "unknown"

    # WAN / www timing
    latency: Optional[int] = None  # ms
    xput_up: Optional[float] = None  # Mbps
    xput_down: Optional[float] = None
    speedtest_ping: Optional[float] = None
    uptime: Optional[int] = None
    drops: Optional[int] = None
    gw_mac: Optional[str] = None

    num_user: Optional[int] = None
    num_guest: Optional[int] = None
    num_ap: Optional[int] = None
    num_sw: Optional[int] = None
    num_gw: Optional[int] = None
    num_adopted: Optional[int] = None
    num_disconnected: Optional[int] = None


class Event(_Base):
    """A normalized ``stat/event`` / WebSocket event row."""

    id: Optional[str] = Field(default=None, alias="_id")
    key: Optional[str] = None  # "EVT_WU_Roam", "EVT_SW_PoeOverload", ...
    time: Optional[int] = None  # epoch ms
    datetime: Optional[str] = None
    msg: Optional[str] = None
    subsystem: Optional[str] = None

    ap: Optional[str] = None
    sw: Optional[str] = None
    gw: Optional[str] = None
    user: Optional[str] = None  # client MAC
    client: Optional[str] = None
    ssid: Optional[str] = None
    channel: Optional[int] = None


class ReportRow(_Base):
    """A ``stat/report/{5minutes,hourly,daily}.{ap,user,gw,site}`` sample.

    Shape depends entirely on the requested ``attrs``; only ``time`` (bucket
    start, epoch ms) is universal. Everything else rides on ``extra="allow"``.
    """

    time: Optional[int] = None
    o: Optional[str] = None  # object type echoed back by some controllers
    oid: Optional[str] = None  # object id (mac / user mac / site)


class Session(_Base):
    """A ``stat/session`` row: one client connection with roaming sub-sessions."""

    mac: Optional[str] = None
    assoc_time: Optional[int] = None  # epoch seconds
    duration: Optional[int] = None
    ap_mac: Optional[str] = None
    essid: Optional[str] = None
    ip: Optional[str] = None
    rx_bytes: Optional[int] = None
    tx_bytes: Optional[int] = None


class RogueAp(_Base):
    """A ``stat/rogueap`` neighbor BSS row (CCI / coverage context)."""

    bssid: Optional[str] = None
    essid: Optional[str] = None
    channel: Optional[int] = None
    rssi: Optional[int] = None  # 0-based signal-quality index, NOT dBm
    signal: Optional[int] = None  # RSSI in dBm (negative); absent on older polls
    band: Optional[str] = None
    security: Optional[str] = None
    is_rogue: Optional[bool] = None
    is_ubnt: Optional[bool] = None  # controller flag: the neighbor BSS is Ubiquiti hardware
    last_seen: Optional[int] = None
    ap_mac: Optional[str] = None


class Wlan(_Base):
    """A ``rest/wlanconf`` row: one configured SSID.

    Read so the detection layer knows which SSIDs are *ours* — the fact
    ``wifi.rogue_ap`` needs before it can call a neighbour BSS an evil twin.
    ``name`` is the SSID as broadcast; ``security`` is the configured mode
    (``open`` / ``wpapsk`` / ``wpaeap`` / …), which turns an open twin of a
    secured SSID into recorded corroboration rather than a guess.
    """

    id: Optional[str] = Field(default=None, alias="_id")
    name: Optional[str] = None  # the SSID
    enabled: Optional[bool] = None
    security: Optional[str] = None
    wpa_mode: Optional[str] = None
    is_guest: Optional[bool] = None


class Alarm(_Base):
    """A ``list/alarm`` row."""

    id: Optional[str] = Field(default=None, alias="_id")
    key: Optional[str] = None
    time: Optional[int] = None
    datetime: Optional[str] = None
    msg: Optional[str] = None
    archived: Optional[bool] = None


class Anomaly(_Base):
    """A ``stat/anomalies`` row (controller-side anomaly signal)."""

    mac: Optional[str] = None
    anomaly: Optional[str] = None
    start_time: Optional[int] = None
    end_time: Optional[int] = None


__all__ = [
    "PortEntry",
    "RadioTableStat",
    "Uplink",
    "Device",
    "Client",
    "HealthSubsystem",
    "Event",
    "ReportRow",
    "Session",
    "RogueAp",
    "Wlan",
    "Alarm",
    "Anomaly",
]
