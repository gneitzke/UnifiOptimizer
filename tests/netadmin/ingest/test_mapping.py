"""Pure-mapping tests against recorded controller fixtures (ARCHITECTURE.md 5.2).

Mapping is pure, so these tests need no repository, no controller, no clock --
just parsed models in, dataclasses out.
"""

from __future__ import annotations

from netadmin.domain.types import EntityType
from netadmin.ingest import mapping as m
from netadmin.ingest.mapping import METRICS, map_clients, map_device, map_devices, map_health
from netadmin.store.metrics import MetricKind, metric_kind

from .conftest import make_client, make_device, make_health

TS = 1_784_700_000


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #
def test_every_metric_is_registered_with_declared_kind():
    for name, (kind, unit) in METRICS.items():
        assert unit  # unit is declared and non-empty
        assert metric_kind(name) is kind, name


def test_counter_and_gauge_kinds_are_correct():
    assert metric_kind("rx_errors") is MetricKind.COUNTER
    assert metric_kind("uplink_drops") is MetricKind.COUNTER
    assert metric_kind("poe_power") is MetricKind.GAUGE
    assert metric_kind("rssi") is MetricKind.GAUGE
    assert metric_kind("cu_total") is MetricKind.GAUGE


# --------------------------------------------------------------------------- #
# device inventory
# --------------------------------------------------------------------------- #
def test_sfp_switch_inventory_shape(sfp_devices):
    mapping = map_devices(sfp_devices, TS)
    by_type: dict[EntityType, list] = {}
    for rec in mapping.inventory:
        by_type.setdefault(rec.entity.entity_type, []).append(rec)

    assert len(by_type[EntityType.SWITCH]) == 1
    assert len(by_type[EntityType.PORT]) == 2

    switch = by_type[EntityType.SWITCH][0]
    assert switch.entity.native_id == "02:00:11:22:33:0a"
    assert switch.entity.model == "USAGGPRO"
    assert switch.parent_ref is None
    assert switch.tracked_attrs["firmware"] == "6.6.65"
    assert switch.tracked_attrs["state"] == 1


def test_port_native_ids_and_parentage(sfp_devices):
    mapping = map_device(sfp_devices[0], TS)
    ports = [r for r in mapping.inventory if r.entity.entity_type is EntityType.PORT]
    nids = {p.entity.native_id for p in ports}
    assert nids == {"02:00:11:22:33:0a:1", "02:00:11:22:33:0a:2"}
    for port in ports:
        assert port.parent_ref == (EntityType.SWITCH, "02:00:11:22:33:0a")


def test_port_tracked_speed_and_duplex(sfp_devices):
    mapping = map_device(sfp_devices[0], TS)
    port2 = next(r for r in mapping.inventory if r.entity.native_id == "02:00:11:22:33:0a:2")
    assert port2.tracked_attrs["speed"] == 100
    assert port2.tracked_attrs["full_duplex"] is False
    assert port2.tracked_attrs["up"] is True


# --------------------------------------------------------------------------- #
# device metrics
# --------------------------------------------------------------------------- #
def _samples_for(batch, native_id):
    return {s.metric: s for s in batch.samples if s.ref[1] == native_id}


def test_port_error_counter_and_gauges(sfp_devices):
    batch = map_device(sfp_devices[0], TS).batch
    assert batch.ts == TS

    port2 = _samples_for(batch, "02:00:11:22:33:0a:2")
    assert port2["rx_errors"].value == 1423.0
    assert port2["rx_errors"].unit == "packets"
    assert port2["tx_errors"].value == 12.0
    assert port2["poe_power"].value == 7.4
    assert port2["poe_power"].unit == "watts"

    port1 = _samples_for(batch, "02:00:11:22:33:0a:1")
    assert port1["sfp_rxpower"].value == -4.7
    assert port1["sfp_txpower"].value == -2.3
    assert port1["sfp_rxpower"].unit == "dbm"


def test_radio_and_system_metrics_from_stat_device(stat_devices):
    # First device in the recorded stat/device is an AP with two radios + sys stats.
    ap = stat_devices[0]
    mapping = map_device(ap, TS)
    assert any(r.entity.entity_type is EntityType.RADIO for r in mapping.inventory)
    radios = [r for r in mapping.inventory if r.entity.entity_type is EntityType.RADIO]
    assert {r.entity.native_id.split(":")[-1] for r in radios} == {"ng", "na"}

    ng = _samples_for(mapping.batch, ap.mac + ":ng")
    assert ng["cu_total"].value == 40.0
    assert ng["cu_self_rx"].value == 29.0
    assert ng["tx_retries"].value == 60.0
    assert ng["num_sta"].value == 3.0
    assert ng["satisfaction"].value == 99.0

    dev = _samples_for(mapping.batch, ap.mac)
    assert dev["cpu"].value == 10.8
    assert dev["mem"].value == 46.1
    # Device-level client load (Gitea #23): the controller reports these at
    # stat/device top level too, distinct from each radio's own per-band figure.
    assert dev["num_sta"].value == 3.0
    assert dev["satisfaction"].value == 99.0

    ng_radio = next(r for r in radios if r.entity.native_id.endswith(":ng"))
    assert ng_radio.parent_ref == (EntityType.AP, ap.mac)
    assert ng_radio.tracked_attrs["channel"] == 6


def test_uplink_type_tracked_when_present(stat_devices):
    ap = stat_devices[0]
    rec = map_device(ap, TS).inventory[0]
    assert rec.tracked_attrs["uplink_type"] == "wireless"


def test_port_speed_caps_mapped_into_meta(stat_devices):
    # The recorded GE ports advertise speed_caps=1048623 (gigabit-capable); the
    # detector's downshift arm reads it from port meta -- so ingest must persist it.
    ap = stat_devices[0]
    ports = [r for r in map_device(ap, TS).inventory if r.entity.entity_type is EntityType.PORT]
    assert ports
    assert all(p.entity.meta["speed_caps"] == 1048623 for p in ports)


def test_port_without_speed_caps_omits_it(sfp_devices):
    # device_with_sfp ports carry no speed_caps -> meta must not fabricate the key.
    ports = [
        r
        for r in map_device(sfp_devices[0], TS).inventory
        if r.entity.entity_type is EntityType.PORT
    ]
    assert ports
    assert all("speed_caps" not in p.entity.meta for p in ports)


def test_switch_poe_budget_mapped_to_meta_and_gauges(sfp_devices):
    # total_max_power/total_used_power feed wired.poe_budget's percentage math via
    # switch meta, and are emitted as gauges for history.
    mapping = map_device(sfp_devices[0], TS)
    switch = next(r for r in mapping.inventory if r.entity.entity_type is EntityType.SWITCH)
    assert switch.entity.meta["total_max_power"] == 0
    assert switch.entity.meta["total_used_power"] == 0
    dev = _samples_for(mapping.batch, switch.entity.native_id)
    assert dev["total_max_power"].value == 0.0
    assert dev["total_max_power"].unit == "watts"
    assert dev["total_used_power"].value == 0.0


def test_wireless_uplink_rssi_and_tx_rate_mapped_as_gauges(stat_devices):
    # A meshed AP (uplink.type == 'wireless'): uplink RSSI comes from dBm `signal`
    # (-69), not the 0-based `rssi` index (27); tx rate rides alongside. These feed
    # wifi.mesh_uplink, which had no backhaul signal before.
    ap = stat_devices[0]
    assert (ap.uplink.type or "").lower() == "wireless"
    dev = _samples_for(map_device(ap, TS).batch, ap.mac)
    assert dev["uplink_rssi"].value == -69.0
    assert dev["uplink_rssi"].unit == "dbm"
    assert dev["uplink_tx_rate"].value == 117000.0
    assert dev["uplink_tx_rate"].unit == "kbps"


def test_wired_uplink_emits_no_uplink_rssi(sfp_devices):
    # The SFP switch has no wireless uplink -> no uplink_rssi/uplink_tx_rate samples.
    dev = _samples_for(map_device(sfp_devices[0], TS).batch, sfp_devices[0].mac)
    assert "uplink_rssi" not in dev
    assert "uplink_tx_rate" not in dev


# --------------------------------------------------------------------------- #
# device thermals (top-level general_temperature, fan, capability flags)
# --------------------------------------------------------------------------- #
def _device_by_model(devices, model):
    return next(d for d in devices if d.model == model)


def test_temp_emitted_from_top_level_general_temperature(stat_devices):
    # Regression: general_temperature is a TOP-LEVEL device field, not a
    # system-stats key. Sourcing it from system-stats alone meant the temp series
    # never emitted on the only hardware here that has a sensor.
    switch = _device_by_model(stat_devices, "US16P150")
    assert switch.general_temperature == 63
    assert "general_temperature" not in (switch.system_stats or {})

    dev = _samples_for(map_device(switch, TS).batch, switch.mac)
    assert dev["temp"].value == 63.0
    assert dev["temp"].unit == "celsius"


def test_temp_falls_back_to_system_stats_when_top_level_absent():
    device = make_device(
        mac="02:00:11:22:33:ff",
        type="usw",
        **{"system-stats": {"cpu": "5.0", "general_temperature": "58"}},
    )
    dev = _samples_for(map_device(device, TS).batch, device.mac)
    assert dev["temp"].value == 58.0


def test_sensorless_device_emits_no_temp(stat_devices):
    # Every AP reports has_temperature=false and carries no thermal field at all.
    # Absence is data: no sample is emitted, and none is fabricated as 0.
    ap = _device_by_model(stat_devices, "U7PG2")
    assert ap.has_temperature is False
    dev = _samples_for(map_device(ap, TS).batch, ap.mac)
    assert "temp" not in dev
    assert "fan_level" not in dev


def test_thermal_capability_flags_land_in_device_meta(stat_devices):
    switch = _device_by_model(stat_devices, "US16P150")
    ap = _device_by_model(stat_devices, "U7PG2")

    sw_rec = map_device(switch, TS).inventory[0]
    assert sw_rec.entity.meta["has_temperature"] is True
    assert sw_rec.entity.meta["has_fan"] is True

    ap_rec = map_device(ap, TS).inventory[0]
    assert ap_rec.entity.meta["has_temperature"] is False
    assert ap_rec.entity.meta["has_fan"] is False


def test_overheating_flag_tracked_on_device(stat_devices):
    switch = _device_by_model(stat_devices, "US16P150")
    rec = map_device(switch, TS).inventory[0]
    assert rec.tracked_attrs["overheating"] is False

    # An AP never reports the flag, so the key is simply absent (never faked False).
    ap_rec = map_device(_device_by_model(stat_devices, "U7PG2"), TS).inventory[0]
    assert "overheating" not in ap_rec.tracked_attrs


def test_fan_level_and_uptime_emitted_as_gauges(stat_devices):
    switch = _device_by_model(stat_devices, "US16P150")
    dev = _samples_for(map_device(switch, TS).batch, switch.mac)
    assert dev["fan_level"].value == 0.0
    assert dev["fan_level"].unit == "level"
    assert dev["uptime"].value == 3115051.0
    assert dev["uptime"].unit == "seconds"


def test_uptime_is_a_gauge_not_a_counter():
    # It resets to ~0 on reboot; diffing it as a counter would produce a garbage
    # negative delta exactly when the reboot signal matters most.
    assert metric_kind("uptime") is MetricKind.GAUGE


# --------------------------------------------------------------------------- #
# SFP digital optical monitoring
# --------------------------------------------------------------------------- #
def test_sfp_dom_block_emitted_as_gauges(sfp_devices):
    port1 = _samples_for(map_device(sfp_devices[0], TS).batch, "02:00:11:22:33:0a:1")
    assert port1["sfp_temperature"].value == 41.2
    assert port1["sfp_temperature"].unit == "celsius"
    assert port1["sfp_voltage"].value == 3.28
    assert port1["sfp_voltage"].unit == "volts"
    assert port1["sfp_current"].value == 6.1
    assert port1["sfp_current"].unit == "ma"


def test_sfp_fault_flags_tracked_on_the_port(sfp_devices):
    # Regression: wired.sfp_degraded reads sfp_rxfault/sfp_txfault through
    # current_state, but mapping never recorded them -- the fault arm was dead code.
    ports = {
        r.entity.native_id: r
        for r in map_device(sfp_devices[0], TS).inventory
        if r.entity.entity_type is EntityType.PORT
    }
    optic = ports["02:00:11:22:33:0a:1"]
    assert optic.tracked_attrs["sfp_rxfault"] is False
    assert optic.tracked_attrs["sfp_txfault"] is False

    # A copper port reports no DOM at all; the collector drops None attrs, so the
    # port never records a fault state it cannot observe.
    copper = ports["02:00:11:22:33:0a:2"]
    assert copper.tracked_attrs["sfp_rxfault"] is None
    assert copper.tracked_attrs["sfp_txfault"] is None


def test_empty_sfp_cage_emits_no_dom_samples(stat_devices):
    # The live US-16-150W has two SFP cages with no optic installed
    # (sfp_found=false, no DOM fields). Nothing is emitted for them.
    switch = _device_by_model(stat_devices, "US16P150")
    cage = _samples_for(map_device(switch, TS).batch, f"{switch.mac}:17")
    for metric in ("sfp_rxpower", "sfp_txpower", "sfp_temperature", "sfp_voltage", "sfp_current"):
        assert metric not in cage


# --------------------------------------------------------------------------- #
# clients
# --------------------------------------------------------------------------- #
def test_wireless_client_mapping():
    client = make_client(
        mac="02:00:aa:00:00:01",
        ap_mac="02:00:ap:00:00:09",
        is_wired=False,
        signal=-72,
        noise=-95,
        satisfaction=88,
        tx_retries=1200,
        wifi_tx_attempts=8000,
        tx_rate=144000,
        rx_rate=72000,
        roam_count=4,
        ip="10.0.0.5",
        oui="Acme",
    )
    mapping = map_clients([client], TS)
    rec = mapping.inventory[0]
    assert rec.entity.entity_type is EntityType.CLIENT
    assert rec.parent_ref == (EntityType.AP, "02:00:ap:00:00:09")
    assert rec.tracked_attrs["ap_mac"] == "02:00:ap:00:00:09"
    assert rec.tracked_attrs["ip"] == "10.0.0.5"

    s = _samples_for(mapping.batch, "02:00:aa:00:00:01")
    # rssi is sourced from the dBm `signal`, not the quality `rssi` field.
    assert s["rssi"].value == -72.0
    assert s["rssi"].unit == "dbm"
    assert s["noise"].value == -95.0
    assert s["tx_retries"].value == 1200.0
    assert s["roam_count"].value == 4.0


def test_wired_client_parent_is_switch():
    client = make_client(
        mac="02:00:bb:00:00:02",
        is_wired=True,
        sw_mac="02:00:sw:00:00:0a",
        sw_port=5,
        ip="10.0.0.9",
    )
    rec = map_clients([client], TS).inventory[0]
    assert rec.parent_ref == (EntityType.SWITCH, "02:00:sw:00:00:0a")
    assert rec.tracked_attrs["ap_mac"] == "02:00:sw:00:00:0a"


def test_client_without_mac_is_skipped():
    assert map_clients([make_client(signal=-60)], TS).inventory == []


def test_recorded_sta_fixture_maps_without_error():
    # Sanitized fixture MACs collide, but mapping is pure and must not raise;
    # every wireless row yields a client record.
    from netadmin.ingest.unifi.models import Client

    from .conftest import load_data

    clients = [Client.model_validate(r) for r in load_data("stat_sta.json")]
    mapping = map_clients(clients, TS)
    assert len(mapping.inventory) == len(clients)


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #
def test_health_all_unknown_emits_nothing(health_subsystems):
    # The recorded stat/health has wan/www 'unknown' with no gateway.
    result = map_health(health_subsystems, TS)
    assert result.gateway_native_id is None
    assert result.batch.samples == []


def test_health_with_wan_pins_metrics_on_gateway():
    subs = [
        make_health(
            subsystem="wan",
            status="ok",
            gw_mac="02:00:gw:00:00:01",
            latency=12,
            drops=3,
            xput_up=25.0,
            xput_down=300.0,
            uptime=99999,
        ),
        make_health(subsystem="www", status="ok", latency=18, drops=1),
    ]
    result = map_health(subs, TS)
    assert result.gateway_native_id == "02:00:gw:00:00:01"
    s = _samples_for(result.batch, "02:00:gw:00:00:01")
    assert s["wan_latency"].value == 12.0
    assert s["wan_xput_down"].value == 300.0
    assert s["wan_uptime"].value == 99999.0
    assert s["www_latency"].value == 18.0


def test_health_gateway_native_id_fallback():
    subs = [make_health(subsystem="wan", status="ok", latency=20)]
    result = map_health(subs, TS, gateway_native_id="02:00:gw:00:00:02")
    assert result.gateway_native_id == "02:00:gw:00:00:02"
    assert _samples_for(result.batch, "02:00:gw:00:00:02")["wan_latency"].value == 20.0


# --------------------------------------------------------------------------- #
# purity
# --------------------------------------------------------------------------- #
def test_mapping_is_deterministic(sfp_devices):
    a = map_devices(sfp_devices, TS)
    b = map_devices(sfp_devices, TS)
    assert [r.entity.native_id for r in a.inventory] == [r.entity.native_id for r in b.inventory]
    assert [(s.ref, s.metric, s.value) for s in a.batch.samples] == [
        (s.ref, s.metric, s.value) for s in b.batch.samples
    ]


def test_empty_device_is_safe():
    from .conftest import make_device

    mapping = map_device(make_device(), TS)
    assert mapping.inventory == []
    assert mapping.batch.samples == []


def test_module_exports_sample_batch():
    assert hasattr(m, "SampleBatch")
    assert hasattr(m, "Mapping")
