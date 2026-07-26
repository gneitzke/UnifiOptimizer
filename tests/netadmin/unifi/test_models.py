"""Model field coverage and extra-field tolerance (no controller)."""

from __future__ import annotations

from netadmin.ingest.unifi.models import Client, Device, HealthSubsystem, RadioTableStat, Uplink


def test_extra_fields_tolerated():
    # A future firmware adds an unknown field; parsing must not fail.
    dev = Device.model_validate({"mac": "02:00:00:00:00:01", "brand_new_field": 42})
    assert dev.mac == "02:00:00:00:00:01"
    assert dev.model_extra["brand_new_field"] == 42


def test_system_stats_alias():
    dev = Device.model_validate({"mac": "x", "system-stats": {"cpu": "3.2", "mem": "40"}})
    assert dev.system_stats == {"cpu": "3.2", "mem": "40"}


def test_radio_cu_split():
    radio = RadioTableStat.model_validate(
        {"radio": "na", "cu_total": 62, "cu_self_rx": 20, "cu_self_tx": 15, "satisfaction": 88}
    )
    assert radio.cu_total == 62
    assert radio.cu_self_rx == 20
    assert radio.cu_self_tx == 15


def test_uplink_wireless_fields():
    up = Uplink.model_validate({"type": "wireless", "rssi": -63, "latency": 4, "drops": 2})
    assert up.type == "wireless"
    assert up.rssi == -63


def test_health_wan_fields():
    h = HealthSubsystem.model_validate(
        {"subsystem": "wan", "status": "ok", "latency": 12, "xput_down": 480.5, "xput_up": 21.2}
    )
    assert h.subsystem == "wan"
    assert h.xput_down == 480.5


def test_client_wired_and_wireless_paths():
    wired = Client.model_validate(
        {"mac": "02:00:00:00:00:02", "is_wired": True, "sw_mac": "02:00:00:00:00:08", "sw_port": 5}
    )
    assert wired.is_wired is True
    assert wired.sw_port == 5

    wireless = Client.model_validate(
        {
            "mac": "02:00:00:00:00:03",
            "is_wired": False,
            "rssi": -58,
            "noise": -95,
            "tx_retries": 3,
            "wifi_tx_attempts": 120,
            "roam_count": 2,
            "anomalies": 0,
            "powersave_enabled": True,
        }
    )
    assert wireless.rssi == -58
    assert wireless.wifi_tx_attempts == 120
    assert wireless.powersave_enabled is True
