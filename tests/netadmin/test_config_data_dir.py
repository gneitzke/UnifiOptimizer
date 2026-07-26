"""The runtime ``data/`` directory resolves from the environment, not the installed
package location.

Regression for the wheel-install bug: deriving ``data/`` from ``__file__`` put
``secrets.env`` and the SQLite DB inside ``site-packages`` -- credentials never
loaded and the database was wiped on every ``pip install --upgrade``.
"""

from __future__ import annotations

from pathlib import Path

from netadmin.config import _runtime_data_dir


def test_defaults_to_cwd_data(monkeypatch, tmp_path):
    monkeypatch.delenv("NETADMIN_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert _runtime_data_dir() == tmp_path / "data"


def test_env_override_wins(monkeypatch, tmp_path):
    target = tmp_path / "custom-data"
    monkeypatch.setenv("NETADMIN_DATA_DIR", str(target))
    assert _runtime_data_dir() == target


def test_env_override_expands_user(monkeypatch):
    monkeypatch.setenv("NETADMIN_DATA_DIR", "~/netadmin-data")
    assert _runtime_data_dir() == Path("~/netadmin-data").expanduser()


def test_not_derived_from_package_path(monkeypatch, tmp_path):
    """The resolved data dir tracks the runtime, never the installed package."""
    import netadmin.config as cfg

    monkeypatch.delenv("NETADMIN_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    resolved = _runtime_data_dir()
    assert resolved == tmp_path / "data"
    assert cfg.PROJECT_ROOT not in resolved.parents
