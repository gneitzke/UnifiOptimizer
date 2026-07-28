"""Pure mappers: UniFi client models -> store-shaped writes (ARCHITECTURE.md 5.2).

Everything here is a **pure function**: it takes parsed
:mod:`netadmin.ingest.unifi.models` objects and a timestamp, and returns plain
dataclasses describing what the collector should write. No I/O, no repository,
no SQL, no clock reads. That keeps the mapping exhaustively unit-testable
against recorded controller fixtures and keeps the collector (which owns the
repository and the event loop) thin.

Two kinds of output per poll:

* **Inventory** (:class:`EntityRecord`): the entity to upsert, the tracked
  discrete attributes to diff into ``state_changes`` (firmware, state, link
  speed/duplex, up/down, channel, uplink type, client ap_mac/ip), and a
  ``parent_ref`` naming the parent by ``(EntityType, native_id)`` -- resolved to
  a real ``parent_id`` by the collector, since the parent's row id is not known
  until it is upserted. Records are emitted parents-first.
* **Metrics** (:class:`SampleBatch` of :class:`MetricSample`): one batch per
  poll. Each sample names its entity by ``(EntityType, native_id)`` too; the
  collector resolves those to ``series`` via the repository. Counter vs gauge is
  declared once in :data:`METRICS` and registered with
  :mod:`netadmin.store.metrics` at import, so ``Repository.record_samples`` diffs
  counters into per-interval deltas automatically.

Timestamps are epoch seconds, UTC (the collector supplies them).

RSSI note: the canonical per-client RSSI in dBm is the controller's ``signal``
field (negative dBm), *not* its ``rssi`` field (a 0-based signal-quality index).
Detectors and the SLE model compare against dBm thresholds ("RSSI < -75"), so
the ``rssi`` metric here is sourced from ``Client.signal``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.ingest.unifi.models import Client, Device, HealthSubsystem
from netadmin.store.metrics import MetricKind, register_metric

__all__ = [
    "EntityRef",
    "EntityRecord",
    "MetricSample",
    "SampleBatch",
    "Mapping",
    "HealthMapping",
    "METRICS",
    "map_device",
    "map_devices",
    "map_clients",
    "map_health",
    "device_entity_type",
]

# Metric name -> (kind, unit). The single source of truth for every metric the
# collector emits. Registered with the store metric registry at import so the
# repository knows which readings to diff into deltas; the unit rides onto the
# interned series row.
METRICS: dict[str, tuple[MetricKind, str]] = {
    # --- per-port (stat/device port_table) ---
    "rx_errors": (MetricKind.COUNTER, "packets"),
    "tx_errors": (MetricKind.COUNTER, "packets"),
    "rx_dropped": (MetricKind.COUNTER, "packets"),
    "tx_dropped": (MetricKind.COUNTER, "packets"),
    "rx_bytes": (MetricKind.COUNTER, "bytes"),
    "tx_bytes": (MetricKind.COUNTER, "bytes"),
    "rx_broadcast": (MetricKind.COUNTER, "packets"),
    "tx_broadcast": (MetricKind.COUNTER, "packets"),
    "rx_multicast": (MetricKind.COUNTER, "packets"),
    "tx_multicast": (MetricKind.COUNTER, "packets"),
    "poe_power": (MetricKind.GAUGE, "watts"),
    "sfp_rxpower": (MetricKind.GAUGE, "dbm"),
    "sfp_txpower": (MetricKind.GAUGE, "dbm"),
    "sfp_temperature": (MetricKind.GAUGE, "celsius"),
    "sfp_voltage": (MetricKind.GAUGE, "volts"),
    "sfp_current": (MetricKind.GAUGE, "ma"),
    # --- per-radio (stat/device radio_table_stats) ---
    "cu_total": (MetricKind.GAUGE, "percent"),
    "cu_self_rx": (MetricKind.GAUGE, "percent"),
    "cu_self_tx": (MetricKind.GAUGE, "percent"),
    "tx_retries": (MetricKind.COUNTER, "packets"),
    "satisfaction": (MetricKind.GAUGE, "percent"),
    "num_sta": (MetricKind.GAUGE, "clients"),
    # --- switch PoE aggregate (stat/device switch fields) ---
    "total_max_power": (MetricKind.GAUGE, "watts"),
    "total_used_power": (MetricKind.GAUGE, "watts"),
    # --- device uplink ---
    "uplink_latency": (MetricKind.GAUGE, "ms"),
    "uplink_drops": (MetricKind.COUNTER, "packets"),
    # --- device wireless (mesh) uplink backhaul ---
    "uplink_rssi": (MetricKind.GAUGE, "dbm"),
    "uplink_tx_rate": (MetricKind.GAUGE, "kbps"),
    # --- device system stats ---
    "cpu": (MetricKind.GAUGE, "percent"),
    "mem": (MetricKind.GAUGE, "percent"),
    "temp": (MetricKind.GAUGE, "celsius"),
    "fan_level": (MetricKind.GAUGE, "level"),
    # Seconds since boot. A gauge, not a counter: it resets to ~0 on reboot, which
    # is precisely the signal infra.device_overheating reads to rule out a
    # post-boot thermal transient.
    "uptime": (MetricKind.GAUGE, "seconds"),
    # --- per-client (stat/sta) ---
    "rssi": (MetricKind.GAUGE, "dbm"),
    "noise": (MetricKind.GAUGE, "dbm"),
    "wifi_tx_attempts": (MetricKind.COUNTER, "packets"),
    "tx_rate": (MetricKind.GAUGE, "kbps"),
    "rx_rate": (MetricKind.GAUGE, "kbps"),
    "roam_count": (MetricKind.COUNTER, "events"),
    # --- WAN / www health (stat/health) ---
    "wan_latency": (MetricKind.GAUGE, "ms"),
    "www_latency": (MetricKind.GAUGE, "ms"),
    "wan_drops": (MetricKind.GAUGE, "packets"),
    "www_drops": (MetricKind.GAUGE, "packets"),
    "wan_xput_up": (MetricKind.GAUGE, "mbps"),
    "wan_xput_down": (MetricKind.GAUGE, "mbps"),
    "wan_uptime": (MetricKind.GAUGE, "seconds"),
}

# Declare every metric's kind in the store registry exactly once, at import.
for _name, (_kind, _unit) in METRICS.items():
    register_metric(_name, _kind)


EntityRef = tuple[EntityType, str]


@dataclass
class EntityRecord:
    """An entity to upsert plus its tracked state and (unresolved) parent.

    ``parent_ref`` names the parent by identity; the collector resolves it to a
    ``parent_id`` after the parent is upserted (or by looking it up in the store
    when the parent was created in an earlier job/cycle). ``tracked_attrs`` are
    the discrete attributes diffed into ``state_changes`` -- only values that
    actually changed produce a row.
    """

    entity: Entity
    tracked_attrs: dict[str, Any] = field(default_factory=dict)
    parent_ref: Optional[EntityRef] = None

    @property
    def ref(self) -> EntityRef:
        return (self.entity.entity_type, self.entity.native_id)


@dataclass
class MetricSample:
    """One metric reading for an entity named by ``ref`` (not yet a series_id)."""

    ref: EntityRef
    metric: str
    value: float
    unit: Optional[str] = None


@dataclass
class SampleBatch:
    """All metric samples produced by one poll, sharing a single timestamp."""

    ts: int
    samples: list[MetricSample] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.samples)


@dataclass
class Mapping:
    """Inventory + metrics produced from one poll of a device/client endpoint."""

    inventory: list[EntityRecord] = field(default_factory=list)
    batch: SampleBatch = field(default_factory=lambda: SampleBatch(ts=0))


@dataclass
class HealthMapping:
    """stat/health result: WAN metrics pinned on a gateway named by MAC.

    ``gateway_native_id`` is the gateway MAC the WAN/www metrics attach to, or
    ``None`` when the controller reports no gateway (subsystems ``unknown``). The
    collector resolves (or creates) that gateway entity before writing.
    """

    gateway_native_id: Optional[str] = None
    batch: SampleBatch = field(default_factory=lambda: SampleBatch(ts=0))


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
_DEVICE_TYPE_MAP: dict[str, EntityType] = {
    "uap": EntityType.AP,
    "usw": EntityType.SWITCH,
    "ugw": EntityType.GATEWAY,
    "udm": EntityType.GATEWAY,
    "uxg": EntityType.GATEWAY,
}


def device_entity_type(device: Device) -> EntityType:
    """Map a ``stat/device`` row's ``type`` to an :class:`EntityType`.

    Falls back to :attr:`EntityType.AP` when the device carries a radio table
    (some APs report unusual ``type`` strings) and otherwise to
    :attr:`EntityType.SWITCH` for anything unrecognized with ports.
    """
    dtype = (device.type or "").lower()
    if dtype in _DEVICE_TYPE_MAP:
        return _DEVICE_TYPE_MAP[dtype]
    if device.radio_table_stats:
        return EntityType.AP
    return EntityType.SWITCH


def _num(value: Any) -> Optional[float]:
    """Coerce a controller field to float, or None if absent/uncoercible."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _emit(samples: list[MetricSample], ref: EntityRef, metric: str, value: Any) -> None:
    """Append a sample for ``metric`` if ``value`` is present and numeric."""
    num = _num(value)
    if num is None:
        return
    _kind, unit = METRICS[metric]
    samples.append(MetricSample(ref, metric, num, unit))


# --------------------------------------------------------------------------- #
# devices: stat/device -> inventory + metrics
# --------------------------------------------------------------------------- #
def map_device(device: Device, ts: int, *, site_id: str = "default") -> Mapping:
    """Map one ``stat/device`` row to inventory + a metric batch.

    Emits, parents-first: the device entity, then its port entities, then its
    radio entities. Metrics cover per-port error/dropped/byte/broadcast/multicast
    counters plus PoE draw and the full SFP DOM block (rx/tx power, module
    temperature, voltage, bias current), per-radio channel-utilization/retries/
    satisfaction/client-count, the device uplink latency/drops, and device
    cpu/mem/temperature/fan-level/uptime.
    """
    inv: list[EntityRecord] = []
    samples: list[MetricSample] = []

    if not device.mac:
        return Mapping(inventory=inv, batch=SampleBatch(ts=ts, samples=samples))

    etype = device_entity_type(device)
    mac = device.mac
    dev_ref: EntityRef = (etype, mac)

    dev_meta: dict[str, Any] = {"unifi_type": device.type}
    # Switch PoE budget: the aggregate the wired.poe_budget detector divides the
    # summed per-port draw by. It lives in meta (a near-static hardware/config
    # ceiling) and is also emitted as a gauge below for history. Absent on APs and
    # gateways (None) -- only recorded when the controller reports it.
    if device.total_max_power is not None:
        dev_meta["total_max_power"] = device.total_max_power
    if device.total_used_power is not None:
        dev_meta["total_used_power"] = device.total_used_power
    # Thermal hardware capability, recorded honestly so infra.device_overheating
    # can skip a device that has no sensor instead of reading its silence as a
    # cool chassis. Every AP on a typical UniFi site reports has_temperature=false.
    if device.has_temperature is not None:
        dev_meta["has_temperature"] = device.has_temperature
    if device.has_fan is not None:
        dev_meta["has_fan"] = device.has_fan
    dev_tracked: dict[str, Any] = {
        "firmware": device.version,
        "state": device.state,
    }
    # The controller's own overheating verdict, diffed into state_changes so the
    # moment it flips is recorded rather than only its current value.
    if device.overheating is not None:
        dev_tracked["overheating"] = device.overheating
    if device.uplink is not None and device.uplink.type is not None:
        dev_tracked["uplink_type"] = device.uplink.type
    inv.append(
        EntityRecord(
            entity=Entity(
                entity_type=etype,
                native_id=mac,
                site_id=site_id,
                name=device.name,
                model=device.model,
                first_seen_ts=ts,
                last_seen_ts=ts,
                meta=dev_meta,
            ),
            tracked_attrs=dev_tracked,
        )
    )

    # --- ports ---
    for port in device.port_table:
        if port.port_idx is None:
            continue
        pnid = f"{mac}:{port.port_idx}"
        pref: EntityRef = (EntityType.PORT, pnid)
        # Port link-capability into meta: the wired.bad_cable downshift arm reads
        # ``speed_caps`` (a UniFi autoneg-advertisement bitmask; see
        # ``_speed_caps_max`` in detect/detectors/wired.py) to assert a port is
        # gigabit-capable before flagging a 10/100 negotiation as a broken pair.
        # ``max_speed`` is honoured too when a firmware exposes it at port level.
        port_meta: dict[str, Any] = {"media": port.media, "is_uplink": port.is_uplink}
        if port.speed_caps is not None:
            port_meta["speed_caps"] = port.speed_caps
        port_max_speed = getattr(port, "max_speed", None)
        if port_max_speed is not None:
            port_meta["max_speed"] = port_max_speed
        inv.append(
            EntityRecord(
                entity=Entity(
                    entity_type=EntityType.PORT,
                    native_id=pnid,
                    site_id=site_id,
                    name=port.name,
                    first_seen_ts=ts,
                    last_seen_ts=ts,
                    meta=port_meta,
                ),
                tracked_attrs={
                    "speed": port.speed,
                    "full_duplex": port.full_duplex,
                    "up": port.up,
                    # SFP DOM fault latches. Discrete, not sampled: they belong in
                    # state_changes, and wired.sfp_degraded reads them through
                    # ``current_state``. ``None`` on a copper port (the collector
                    # drops None attrs), so a GE port never records them.
                    "sfp_rxfault": port.sfp_rxfault,
                    "sfp_txfault": port.sfp_txfault,
                },
                parent_ref=dev_ref,
            )
        )
        _emit(samples, pref, "rx_errors", port.rx_errors)
        _emit(samples, pref, "tx_errors", port.tx_errors)
        _emit(samples, pref, "rx_dropped", port.rx_dropped)
        _emit(samples, pref, "tx_dropped", port.tx_dropped)
        _emit(samples, pref, "rx_bytes", port.rx_bytes)
        _emit(samples, pref, "tx_bytes", port.tx_bytes)
        _emit(samples, pref, "rx_broadcast", port.rx_broadcast)
        _emit(samples, pref, "tx_broadcast", port.tx_broadcast)
        _emit(samples, pref, "rx_multicast", port.rx_multicast)
        _emit(samples, pref, "tx_multicast", port.tx_multicast)
        _emit(samples, pref, "poe_power", port.poe_power)
        _emit(samples, pref, "sfp_rxpower", port.sfp_rxpower)
        _emit(samples, pref, "sfp_txpower", port.sfp_txpower)
        _emit(samples, pref, "sfp_temperature", port.sfp_temperature)
        _emit(samples, pref, "sfp_voltage", port.sfp_voltage)
        _emit(samples, pref, "sfp_current", port.sfp_current)

    # --- radios ---
    for radio in device.radio_table_stats:
        band = radio.radio or radio.name
        if band is None:
            continue
        rnid = f"{mac}:{band}"
        rref: EntityRef = (EntityType.RADIO, rnid)
        inv.append(
            EntityRecord(
                entity=Entity(
                    entity_type=EntityType.RADIO,
                    native_id=rnid,
                    site_id=site_id,
                    name=radio.name,
                    first_seen_ts=ts,
                    last_seen_ts=ts,
                    meta={"band": radio.radio, "ht": radio.ht},
                ),
                tracked_attrs={"channel": radio.channel},
                parent_ref=dev_ref,
            )
        )
        _emit(samples, rref, "cu_total", radio.cu_total)
        _emit(samples, rref, "cu_self_rx", radio.cu_self_rx)
        _emit(samples, rref, "cu_self_tx", radio.cu_self_tx)
        _emit(samples, rref, "tx_retries", radio.tx_retries)
        _emit(samples, rref, "satisfaction", radio.satisfaction)
        _emit(samples, rref, "num_sta", radio.num_sta)

    # --- device-level: PoE budget (switch) + uplink + system stats ---
    _emit(samples, dev_ref, "total_max_power", device.total_max_power)
    _emit(samples, dev_ref, "total_used_power", device.total_used_power)
    if device.uplink is not None:
        _emit(samples, dev_ref, "uplink_latency", device.uplink.latency)
        _emit(samples, dev_ref, "uplink_drops", device.uplink.drops)
        # Wireless (mesh) backhaul: pin uplink RSSI (dBm, from `signal`, not the
        # 0-based `rssi` quality index) and the negotiated tx rate so
        # wifi.mesh_uplink can judge a meshed AP's backhaul. Wired uplinks report
        # no meaningful RSSI, so these are emitted only for a wireless uplink.
        if (device.uplink.type or "").lower() == "wireless":
            _emit(samples, dev_ref, "uplink_rssi", device.uplink.signal)
            _emit(samples, dev_ref, "uplink_tx_rate", device.uplink.tx_rate)
    if device.system_stats:
        _emit(samples, dev_ref, "cpu", device.system_stats.get("cpu"))
        _emit(samples, dev_ref, "mem", device.system_stats.get("mem"))
    # Device-level client load: parsed onto the model (satisfaction) or added to
    # it (num_sta) but never emitted -- an AP's own rollup (the devices list, its
    # detail page's chart) read these at device granularity and always saw
    # nothing, even though the controller reports both here in addition to each
    # radio's own per-band figures (Gitea #23).
    _emit(samples, dev_ref, "satisfaction", device.satisfaction)
    _emit(samples, dev_ref, "num_sta", device.num_sta)

    # Chassis temperature. The controller reports it at the device **top level**
    # (``general_temperature``); ``system-stats`` is only a fallback for firmware
    # that mirrors it there. Reading system-stats alone meant the ``temp`` series
    # never emitted on the hardware that actually has a sensor.
    temp = device.general_temperature
    if temp is None and device.system_stats:
        temp = device.system_stats.get("general_temperature") or device.system_stats.get(
            "temperature"
        )
    _emit(samples, dev_ref, "temp", temp)
    _emit(samples, dev_ref, "fan_level", device.fan_level)
    _emit(samples, dev_ref, "uptime", device.uptime)

    return Mapping(inventory=inv, batch=SampleBatch(ts=ts, samples=samples))


def map_devices(devices: list[Device], ts: int, *, site_id: str = "default") -> Mapping:
    """Map a whole ``stat/device`` response into one combined mapping."""
    inv: list[EntityRecord] = []
    samples: list[MetricSample] = []
    for device in devices:
        m = map_device(device, ts, site_id=site_id)
        inv.extend(m.inventory)
        samples.extend(m.batch.samples)
    return Mapping(inventory=inv, batch=SampleBatch(ts=ts, samples=samples))


# --------------------------------------------------------------------------- #
# clients: stat/sta -> inventory + metrics
# --------------------------------------------------------------------------- #
def map_clients(clients: list[Client], ts: int, *, site_id: str = "default") -> Mapping:
    """Map ``stat/sta`` rows to client inventory + per-client metrics.

    A client's parent is its current point of attachment: the AP (``ap_mac``) for
    a wireless client, or the switch (``sw_mac``) for a wired one. The collector
    resolves that ref to a ``parent_id``, searching device types when the exact
    type guess is wrong. Tracked discrete attrs are ``ap_mac`` (current
    attachment) and ``ip``.
    """
    inv: list[EntityRecord] = []
    samples: list[MetricSample] = []

    for client in clients:
        if not client.mac:
            continue
        cref: EntityRef = (EntityType.CLIENT, client.mac)

        if client.is_wired and client.sw_mac:
            parent_ref: Optional[EntityRef] = (EntityType.SWITCH, client.sw_mac)
            attach_mac = client.sw_mac
        elif client.ap_mac:
            parent_ref = (EntityType.AP, client.ap_mac)
            attach_mac = client.ap_mac
        else:
            parent_ref = None
            attach_mac = None

        inv.append(
            EntityRecord(
                entity=Entity(
                    entity_type=EntityType.CLIENT,
                    native_id=client.mac,
                    site_id=site_id,
                    name=client.name or client.hostname,
                    first_seen_ts=ts,
                    last_seen_ts=ts,
                    meta={
                        "oui": client.oui,
                        "is_wired": client.is_wired,
                        "essid": client.essid,
                        # Which switch port this client is on. The bad_cable
                        # downshift arm needs it to weigh the peer on THIS port
                        # rather than every client on the switch.
                        "sw_port": client.sw_port,
                    },
                ),
                tracked_attrs={"ap_mac": attach_mac, "ip": client.ip},
                parent_ref=parent_ref,
            )
        )

        # RSSI in dBm is the controller's `signal`, not `rssi` (see module docstring).
        _emit(samples, cref, "rssi", client.signal)
        _emit(samples, cref, "noise", client.noise)
        _emit(samples, cref, "satisfaction", client.satisfaction)
        _emit(samples, cref, "tx_retries", client.tx_retries)
        _emit(samples, cref, "wifi_tx_attempts", client.wifi_tx_attempts)
        _emit(samples, cref, "tx_rate", client.tx_rate)
        _emit(samples, cref, "rx_rate", client.rx_rate)
        _emit(samples, cref, "roam_count", client.roam_count)

    return Mapping(inventory=inv, batch=SampleBatch(ts=ts, samples=samples))


# --------------------------------------------------------------------------- #
# health: stat/health -> WAN/www metrics on the gateway
# --------------------------------------------------------------------------- #
def map_health(
    subsystems: list[HealthSubsystem],
    ts: int,
    *,
    site_id: str = "default",
    gateway_native_id: Optional[str] = None,
) -> HealthMapping:
    """Map ``stat/health`` WAN/www subsystems to gateway metrics.

    Emits WAN/www latency and drops, plus WAN throughput and uptime, pinned on
    the gateway entity. The gateway MAC is taken from a subsystem's ``gw_mac``
    when present, else from ``gateway_native_id``. When neither exists (the
    controller reports the WAN subsystem as ``unknown`` with no gateway), no
    samples are emitted -- absence is data, never a zero.
    """
    samples: list[MetricSample] = []
    gw_native = gateway_native_id
    for sub in subsystems:
        if sub.gw_mac:
            gw_native = sub.gw_mac
            break

    if gw_native is None:
        return HealthMapping(gateway_native_id=None, batch=SampleBatch(ts=ts))

    gref: EntityRef = (EntityType.GATEWAY, gw_native)
    for sub in subsystems:
        name = (sub.subsystem or "").lower()
        if name == "wan":
            _emit(samples, gref, "wan_latency", sub.latency)
            _emit(samples, gref, "wan_drops", sub.drops)
            _emit(samples, gref, "wan_xput_up", sub.xput_up)
            _emit(samples, gref, "wan_xput_down", sub.xput_down)
            _emit(samples, gref, "wan_uptime", sub.uptime)
        elif name == "www":
            _emit(samples, gref, "www_latency", sub.latency)
            _emit(samples, gref, "www_drops", sub.drops)

    return HealthMapping(gateway_native_id=gw_native, batch=SampleBatch(ts=ts, samples=samples))
