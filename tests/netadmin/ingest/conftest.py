"""Fixtures for the ingest (collector + mapping) test suite.

Offline by construction: recorded controller payloads live under the unifi
suite's ``fixtures/`` directory (sanitized MACs/hostnames). No test here touches
a real controller.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from netadmin.ingest.unifi.models import Client, Device, HealthSubsystem
from netadmin.store.repository import Repository

FIXTURES_DIR = Path(__file__).parent.parent / "unifi" / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text())


def load_data(name: str) -> list[dict[str, Any]]:
    return load_fixture(name).get("data", [])


@pytest.fixture
def fixture() -> Any:
    return load_fixture


@pytest.fixture
def sfp_devices() -> list[Device]:
    """The device_with_sfp fixture: one switch, an SFP uplink + an erroring PoE port."""
    return [Device.model_validate(r) for r in load_data("device_with_sfp.json")]


@pytest.fixture
def stat_devices() -> list[Device]:
    return [Device.model_validate(r) for r in load_data("stat_device.json")]


@pytest.fixture
def health_subsystems() -> list[HealthSubsystem]:
    return [HealthSubsystem.model_validate(r) for r in load_data("stat_health.json")]


def make_client(**fields: Any) -> Client:
    return Client.model_validate(fields)


def make_device(**fields: Any) -> Device:
    return Device.model_validate(fields)


def make_health(**fields: Any) -> HealthSubsystem:
    return HealthSubsystem.model_validate(fields)


@pytest.fixture
def repo(tmp_db_path: Path) -> Repository:
    r = Repository.open(tmp_db_path)
    yield r
    r.close()
