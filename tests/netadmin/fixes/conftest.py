"""Shared fixtures for the fix-engine suite.

Everything here is offline. The controller is never contacted: writes go through a
:class:`~netadmin.fixes.writer.FakeControllerWriter` that records calls, and the
one test that exercises :class:`RealControllerWriter` mocks HTTP with ``respx``.
Device "snapshots" are hand-built dicts shaped like a raw controller ``stat/device``
object (``_id``, ``mac``, ``radio_table``, ``port_table``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pytest

from netadmin.domain.entities import Entity, Finding
from netadmin.domain.types import EntityType, Severity
from netadmin.store.repository import Repository

AP_MAC = "aa:bb:cc:00:00:01"
SW_MAC = "aa:bb:cc:00:00:02"
AP_ID = "60a1b2c3d4e5f60000000001"
SW_ID = "60a1b2c3d4e5f60000000002"


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
@pytest.fixture
def store(tmp_path: Path) -> Repository:
    """A migrated, isolated on-disk repository (never the production DB)."""
    repo = Repository.open(tmp_path / "fixes_test.db")
    try:
        yield repo
    finally:
        repo.close()


# --------------------------------------------------------------------------- #
# Device snapshots (raw controller shapes)
# --------------------------------------------------------------------------- #
def make_ap_device(
    *,
    device_id: str = AP_ID,
    mac: str = AP_MAC,
    radios: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    if radios is None:
        radios = [
            {
                "radio": "ng",
                "channel": 3,
                "ht": 20,
                "tx_power_mode": "high",
                "min_rssi_enabled": True,
                "min_rssi": -75,
            },
            {
                "radio": "na",
                "channel": 36,
                "ht": 80,
                "tx_power_mode": "auto",
                "min_rssi_enabled": False,
                "min_rssi": 0,
            },
        ]
    return {"_id": device_id, "mac": mac, "type": "uap", "radio_table": radios}


def make_switch_device(
    *,
    device_id: str = SW_ID,
    mac: str = SW_MAC,
    ports: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    if ports is None:
        ports = [{"port_idx": 5, "poe_mode": "auto", "name": "Camera"}]
    return {"_id": device_id, "mac": mac, "type": "usw", "port_table": ports}


@pytest.fixture
def ap_device() -> dict[str, Any]:
    return make_ap_device()


@pytest.fixture
def switch_device() -> dict[str, Any]:
    return make_switch_device()


# --------------------------------------------------------------------------- #
# Entities + findings
# --------------------------------------------------------------------------- #
def radio_entity(band: str = "ng", *, mac: str = AP_MAC, name: str = "Office AP") -> Entity:
    return Entity(
        entity_type=EntityType.RADIO,
        native_id=f"{mac}:{band}",
        name=f"{name} {band}",
        meta={"band": band},
    )


def rf_entity(band: str = "2.4") -> Entity:
    """The site-scoped ``rf_env`` pseudo-entity a per-band issue is anchored on.

    Deliberately built exactly as the detector and the fix service build it: an
    entity type outside :class:`EntityType`, no ``entity_id``, and an ``rf:<band>``
    native id that is the whole entity component of the fingerprint.
    """
    return Entity(
        entity_type="rf_env",  # type: ignore[arg-type]
        native_id=f"rf:{band}",
        name=f"{band} GHz RF environment",
    )


def port_entity(port_idx: int = 5, *, mac: str = SW_MAC, name: str = "Port 5") -> Entity:
    return Entity(
        entity_type=EntityType.PORT,
        native_id=f"{mac}:{port_idx}",
        name=name,
    )


def make_finding(
    detector_key: str,
    entity: Entity,
    *,
    severity: Severity = Severity.P3,
    title: str = "test finding",
    dims: Optional[dict[str, str]] = None,
    evidence: Optional[dict[str, Any]] = None,
) -> Finding:
    return Finding(
        detector_key=detector_key,
        entity=entity,
        severity=severity,
        title=title,
        dims=dims or {},
        evidence=evidence or {},
    )


@pytest.fixture
def finding_factory():
    return make_finding
