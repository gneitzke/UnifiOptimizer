"""Catalog registry: metadata, cadence filtering, duplicate-key rejection."""

from __future__ import annotations

import pytest

from netadmin.detect.catalog import DEFAULT_CATALOG, Catalog, CatalogEntry, Detector, build_catalog
from netadmin.detect.detectors.infra import (
    KEY_CONTROLLER_DOWN,
    KEY_DEVICE_DOWN,
    ControllerDownDetector,
    DeviceDownDetector,
)
from netadmin.domain.types import Cadence, EntityType, Severity

# The full catalog-v1 registration (docs/ARCHITECTURE.md sections 6 & 17): 3 infra
# + 8 wired + 3 client + 5 wan + 2 net + 13 wifi. All detectors land here as their
# families were completed, so this is the shipped set the daemon runs. The 5th wan
# detector is wan.latency_shift (the Starlink-actionable CUSUM regime-change
# detector, the honest substitute for gateway-less bufferbloat); the 12th and 13th
# wifi detectors are the neighbour-scan pair, wifi.neighbor_density (crowded air,
# one issue per band) and wifi.rogue_ap (the security claims); the 3rd infra
# detector is infra.device_overheating (chassis thermal health).
CATALOG_V1_SIZE = 34


def test_default_catalog_registers_catalog_v1() -> None:
    keys = DEFAULT_CATALOG.keys
    assert len(keys) == len(set(keys)) == CATALOG_V1_SIZE  # unique, full set
    assert {KEY_CONTROLLER_DOWN, KEY_DEVICE_DOWN} <= set(keys)
    # a representative from every family is registered
    for key in (
        "wired.bad_cable",
        "client.flaky",
        "wan.isp_degraded",
        "net.firmware_regression",
        "wifi.airtime_saturation",
    ):
        assert key in keys


def test_infra_detectors_satisfy_the_protocol() -> None:
    # runtime_checkable Protocol: structural conformance of the shipped detectors.
    assert isinstance(ControllerDownDetector(), Detector)
    assert isinstance(DeviceDownDetector(), Detector)


def test_entry_derives_key_scope_cadence_from_detector() -> None:
    entry = DEFAULT_CATALOG.get(KEY_CONTROLLER_DOWN)
    assert entry.key == KEY_CONTROLLER_DOWN
    assert entry.cadence is Cadence.FAST
    assert entry.scope is EntityType.GATEWAY
    assert entry.severity_ceiling is Severity.P1


def test_duplicate_key_registration_is_an_error() -> None:
    d = ControllerDownDetector()
    with pytest.raises(ValueError, match="duplicate detector key"):
        build_catalog(
            [
                CatalogEntry(d, Severity.P1, "t"),
                CatalogEntry(d, Severity.P1, "t"),
            ]
        )


def test_by_cadence_returns_only_that_tier() -> None:
    fast_keys = {e.key for e in DEFAULT_CATALOG.by_cadence(Cadence.FAST)}
    # Every FAST entry is genuinely FAST; the infra pair are FAST members.
    assert {KEY_CONTROLLER_DOWN, KEY_DEVICE_DOWN} <= fast_keys
    assert all(e.cadence is Cadence.FAST for e in DEFAULT_CATALOG.by_cadence(Cadence.FAST))
    # DAILY is now populated (config audits, e.g. firmware regression).
    daily_keys = {e.key for e in DEFAULT_CATALOG.by_cadence(Cadence.DAILY)}
    assert "net.firmware_regression" in daily_keys
    # The tiers partition the catalog with no overlap or loss.
    total = sum(
        len(DEFAULT_CATALOG.by_cadence(c)) for c in (Cadence.FAST, Cadence.WINDOW, Cadence.DAILY)
    )
    assert total == len(DEFAULT_CATALOG)


def test_by_cadence_preserves_registration_order() -> None:
    # The two infra detectors are registered first, so they lead the FAST tier.
    keys = [e.key for e in DEFAULT_CATALOG.by_cadence(Cadence.FAST)]
    assert keys[:2] == [KEY_CONTROLLER_DOWN, KEY_DEVICE_DOWN]


def test_get_unknown_key_raises() -> None:
    with pytest.raises(KeyError):
        DEFAULT_CATALOG.get("nope.detector")


def test_by_scope_filters() -> None:
    switch_keys = {e.key for e in DEFAULT_CATALOG.by_scope(EntityType.SWITCH)}
    gateway_keys = {e.key for e in DEFAULT_CATALOG.by_scope(EntityType.GATEWAY)}
    assert KEY_DEVICE_DOWN in switch_keys
    assert KEY_CONTROLLER_DOWN in gateway_keys
    # by_scope returns only that scope's entries.
    assert all(e.scope is EntityType.SWITCH for e in DEFAULT_CATALOG.by_scope(EntityType.SWITCH))


def test_catalog_is_iterable_and_sized() -> None:
    cat = build_catalog(list(DEFAULT_CATALOG.entries))
    assert isinstance(cat, Catalog)
    assert len(list(cat)) == len(cat) == CATALOG_V1_SIZE
