"""``__version__`` must match what PyPI published, in every install shape.

The dashboard shows this so an operator can compare it against the latest release,
and the update banner compares it too. Two live failures motivated these tests: a
hardcoded "0.1.0" that sat unchanged while the package shipped 0.3.0, and then the
deployed container reporting "0.0.0.dev0" because the image COPYs the source tree
instead of pip-installing it, so there is no distribution metadata to read.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from netadmin import _resolve_version

REPO_ROOT = Path(__file__).resolve().parents[2]


def _pyproject_version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
        return str(tomllib.load(fh)["project"]["version"])


def test_baked_env_version_is_used_when_there_is_no_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The container path: no metadata, version baked into the image."""
    import netadmin

    def _boom(_name: str) -> str:
        raise netadmin.PackageNotFoundError(_name)

    monkeypatch.setattr(netadmin, "_pkg_version", _boom)
    monkeypatch.setenv("NETADMIN_VERSION", "9.9.9")
    assert _resolve_version() == "9.9.9"


def test_falls_back_to_pyproject_off_a_bare_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    """No metadata and nothing baked: read the source of truth next door."""
    import netadmin

    def _boom(_name: str) -> str:
        raise netadmin.PackageNotFoundError(_name)

    monkeypatch.setattr(netadmin, "_pkg_version", _boom)
    monkeypatch.delenv("NETADMIN_VERSION", raising=False)
    assert _resolve_version() == _pyproject_version()


def test_the_image_bakes_the_current_version() -> None:
    """Dockerfile NETADMIN_VERSION must track pyproject, or the mini lies again."""
    dockerfile = (REPO_ROOT / "Dockerfile.netadmin").read_text()
    assert f"NETADMIN_VERSION={_pyproject_version()}" in dockerfile
