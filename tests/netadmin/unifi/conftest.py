"""Fixtures for the UniFi client test suite.

All tests are offline: recorded controller payloads live in ``fixtures/`` (MACs
consistently randomized, hostnames/IPs/serials dropped at record time) and HTTP
is mocked with ``respx``. No test ever touches a real controller or
``data/secrets.env``.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    """Load a recorded ``{"meta":..., "data":[...]}`` envelope by filename."""
    return json.loads((FIXTURES_DIR / name).read_text())


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def fixture() -> Any:
    """Return the :func:`load_fixture` helper (usable as ``fixture("x.json")``)."""
    return load_fixture


def make_jwt(claims: dict[str, Any]) -> str:
    """Build an unsigned-payload JWT string carrying ``claims`` (test helper)."""

    def _b64(obj: dict[str, Any]) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{_b64({'alg': 'HS256', 'typ': 'JWT'})}.{_b64(claims)}.signature"


@pytest.fixture
def jwt_factory():
    return make_jwt
