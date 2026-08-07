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


# ---------------------------------------------------------------------- #
# Presentation coverage for the wired downshift/flapping evidence.
#
# The issue page renders an evidence field only if the catalog declares it, and a
# confounder note only if the playbook has a closure for that key — both look up
# by string and skip silently on a miss. So a detector can emit new evidence and
# have it vanish from the UI with every test still green. These lock the keys the
# multi-gig downshift arm and the sustained-flapping tier actually emit.
# ---------------------------------------------------------------------- #
def _playbook(key: str):
    return DEFAULT_CATALOG.get(key).playbook


def test_bad_cable_playbook_renders_observed_speed_regression() -> None:
    pb = _playbook("wired.bad_cable")
    assert "observed_speed_max" in {f.key for f in pb.evidence_fields}

    evidence = {"negotiated_speed": 1000, "observed_speed_max": 2500, "port_capable_speed": 2500}
    note = pb.confounder_notes["observed_speed_regression"](evidence)
    assert note and "2500" in note and "1000" in note


def test_bad_cable_observed_note_is_silent_without_its_evidence() -> None:
    # The rated arm emits no observed_speed_max; the notes must not render a blank.
    pb = _playbook("wired.bad_cable")
    assert pb.confounder_notes["observed_speed_regression"]({"negotiated_speed": 100}) is None
    assert pb.confounder_notes["peer_predates_observed_speed"]({"negotiated_speed": 100}) is None


def test_bad_cable_playbook_renders_the_peer_age_guard() -> None:
    pb = _playbook("wired.bad_cable")
    note = pb.confounder_notes["peer_predates_observed_speed"]({"observed_speed_max": 2500})
    assert note and "2500" in note


def test_port_flapping_playbook_renders_the_sustained_tier() -> None:
    pb = _playbook("wired.port_flapping")
    keys = {f.key for f in pb.evidence_fields}
    assert {"transitions_sustained", "window_sustained_s"} <= keys

    evidence = {
        "transitions_short": 0,
        "transitions_long": 2,
        "transitions_sustained": 38,
        "window_short_s": 600,
        "window_long_s": 3600,
        "window_sustained_s": 86400,
    }
    note = pb.confounder_notes["sustained_transition_count"](evidence)
    assert note and "38 in 24 h" in note


def test_port_flapping_note_omits_the_sustained_clause_on_older_evidence() -> None:
    """Issues predating the sustained tier carry no such keys.

    A resolved port_flapping issue never gets its evidence refreshed, and the
    demo seed writes the pre-tier shape too, so an unguarded f-string renders
    "unknown in unknown" on the public demo forever.
    """
    pb = _playbook("wired.port_flapping")
    old = {
        "transitions_short": 6,
        "transitions_long": 9,
        "window_short_s": 600,
        "window_long_s": 3600,
    }
    note = pb.confounder_notes["sustained_transition_count"](old)
    assert note and "unknown" not in note
    assert note.endswith("9 in 1 h.")
