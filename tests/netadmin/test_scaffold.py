"""Scaffold smoke tests: prove the package imports and fixtures resolve.

Builder agents replace / extend these with real suites in their subdirectories;
this file only guards that the Phase-0 skeleton stays importable.
"""

from __future__ import annotations

from pathlib import Path

import netadmin
from netadmin.domain.entities import Entity, Finding, Fix
from netadmin.domain.types import Cadence, EntityType, FixState, IssueState, Severity


def test_package_version() -> None:
    """__version__ is derived from installed package metadata (never a literal),
    so it can never again silently drift from pyproject.toml's [project].version."""
    from importlib.metadata import version

    assert netadmin.__version__ == version("unifioptimizer")


def test_enum_serialized_values() -> None:
    assert EntityType.AP.value == "ap"
    assert Severity.P1.value == "p1"
    assert Cadence.FAST.value == "fast"
    assert IssueState.PENDING.value == "pending"
    assert FixState.PROPOSED.value == "proposed"


def test_finding_construction() -> None:
    entity = Entity(entity_type=EntityType.PORT, native_id="aa:bb:cc:dd:ee:ff:5")
    finding = Finding(
        detector_key="wired.bad_cable",
        entity=entity,
        severity=Severity.P2,
        title="rx_errors climbing on port 5",
        dims={"port": "5"},
        evidence={"rx_err_rate_per_min": 42},
        confounders_checked=["counter_age", "unmanaged_hop"],
    )
    assert finding.proposed_fix is None
    assert finding.entity.site_id == "default"

    fix = Fix(action="port_cycle", entity=entity)
    assert fix.state is FixState.PROPOSED
    assert fix.requires_user_action is True


def test_sample_config_fixture(sample_config, tmp_db_path: Path) -> None:
    assert sample_config.unifi_host == "unifi.test.local"
    assert sample_config.db_path == tmp_db_path
    assert sample_config.unifi.is_configured is True
    assert sample_config.poll.device_s == 60
    assert sample_config.retention.raw_days == 30


def test_get_logger_is_namespaced() -> None:
    from netadmin.logging import get_logger

    logger = get_logger("scaffoldcheck")
    assert logger.name == "netadmin.scaffoldcheck"
