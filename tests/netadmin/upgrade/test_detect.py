"""Install-method detection: the first-hit-wins ladder (ARCHITECTURE.md section 23)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from netadmin.upgrade.detect import InstallInfo, detect_install_method

_NO_HIT = {
    "dockerenv_path": Path("/does/not/exist/.dockerenv"),
    "cgroup_path": Path("/does/not/exist/cgroup"),
}


def test_env_override_wins_first(tmp_path: Path) -> None:
    """Case 1: an explicit override short-circuits every other signal."""
    dockerenv_present = tmp_path / "dockerenv-present"
    dockerenv_present.touch()  # container evidence is present too...
    info = detect_install_method(
        env={"NETADMIN_INSTALL_METHOD": "pip"},  # ...but the override wins anyway.
        dockerenv_path=dockerenv_present,
        cgroup_path=Path("/does/not/exist/cgroup"),
    )
    assert info == InstallInfo(method="pip", variant=None, self_upgrade_supported=False)


def test_env_override_carries_the_variant() -> None:
    info = detect_install_method(
        env={"NETADMIN_INSTALL_METHOD": "container", "NETADMIN_UPDATE_VARIANT": "macmini"},
        **_NO_HIT,
    )
    assert info == InstallInfo(method="container", variant="macmini", self_upgrade_supported=False)


def test_env_override_ignores_an_unknown_variant() -> None:
    info = detect_install_method(
        env={"NETADMIN_INSTALL_METHOD": "container", "NETADMIN_UPDATE_VARIANT": "bogus"},
        **_NO_HIT,
    )
    assert info.variant is None


def test_env_override_ignores_an_unknown_method(tmp_path: Path) -> None:
    """A garbage NETADMIN_INSTALL_METHOD is not trusted; detection falls through."""
    info = detect_install_method(
        env={"NETADMIN_INSTALL_METHOD": "not-a-real-method"},
        **_NO_HIT,
        distribution_missing=True,
    )
    assert info.method == "source"


def test_addon_beats_container_and_pip_signals(tmp_path: Path) -> None:
    """Case 2: SUPERVISOR_TOKEN wins even with /.dockerenv present too."""
    dockerenv = tmp_path / ".dockerenv"
    dockerenv.touch()
    info = detect_install_method(
        env={"SUPERVISOR_TOKEN": "abc123"},
        dockerenv_path=dockerenv,
        cgroup_path=Path("/does/not/exist/cgroup"),
    )
    assert info == InstallInfo(method="addon", variant=None, self_upgrade_supported=False)


def test_dockerenv_file_signals_container() -> None:
    info = detect_install_method(
        env={},
        dockerenv_path=Path(__file__),  # any existing file stands in for /.dockerenv
        cgroup_path=Path("/does/not/exist/cgroup"),
    )
    assert info == InstallInfo(method="container", variant=None, self_upgrade_supported=False)


@pytest.mark.parametrize("token", ["docker", "containerd", "podman", "DOCKER"])
def test_cgroup_mentioning_a_runtime_signals_container(tmp_path: Path, token: str) -> None:
    cgroup = tmp_path / "cgroup"
    cgroup.write_text(f"0::/{token}/deadbeef\n", encoding="utf-8")
    info = detect_install_method(
        env={}, dockerenv_path=Path("/does/not/exist/.dockerenv"), cgroup_path=cgroup
    )
    assert info.method == "container"


def test_cgroup_without_a_runtime_token_does_not_signal_container(tmp_path: Path) -> None:
    cgroup = tmp_path / "cgroup"
    cgroup.write_text("0::/user.slice/user-1000.slice\n", encoding="utf-8")
    info = detect_install_method(
        env={},
        dockerenv_path=Path("/does/not/exist/.dockerenv"),
        cgroup_path=cgroup,
        distribution_missing=True,
    )
    assert info.method == "source"


def test_missing_distribution_is_source() -> None:
    """Case 4: no distribution metadata at all -- a bare PYTHONPATH checkout."""
    info = detect_install_method(env={}, **_NO_HIT, distribution_missing=True)
    assert info == InstallInfo(method="source", variant=None, self_upgrade_supported=False)


def _fake_distribution(direct_url_json: "object | None") -> MagicMock:
    dist = MagicMock()
    if direct_url_json is None:
        dist.read_text.return_value = None
    else:
        dist.read_text.return_value = json.dumps(direct_url_json)
    return dist


def test_editable_direct_url_is_source() -> None:
    """Case 4: an editable install (``pip install -e .``) is a source checkout."""
    dist = _fake_distribution({"url": "file:///repo", "dir_info": {"editable": True}})
    info = detect_install_method(
        env={},
        **_NO_HIT,
        distribution=dist,
        sys_prefix="/venv",
        sys_base_prefix="/usr",
    )
    assert info == InstallInfo(method="source", variant=None, self_upgrade_supported=False)


def test_wheel_install_in_a_venv_is_pip_and_supports_self_upgrade(tmp_path: Path) -> None:
    """Case 5: present, no direct_url.json, inside a venv, writable prefix, POSIX."""
    dist = _fake_distribution(None)
    venv_dir = tmp_path / "venv"
    venv_dir.mkdir()
    info = detect_install_method(
        env={},
        **_NO_HIT,
        distribution=dist,
        sys_prefix=str(venv_dir),
        sys_base_prefix="/usr",
        posix=True,
    )
    assert info == InstallInfo(method="pip", variant=None, self_upgrade_supported=True)


def test_wheel_install_in_a_venv_on_windows_does_not_support_self_upgrade(tmp_path: Path) -> None:
    dist = _fake_distribution(None)
    venv_dir = tmp_path / "venv"
    venv_dir.mkdir()
    info = detect_install_method(
        env={},
        **_NO_HIT,
        distribution=dist,
        sys_prefix=str(venv_dir),
        sys_base_prefix="/usr",
        posix=False,
    )
    assert info == InstallInfo(method="pip", variant=None, self_upgrade_supported=False)


def test_wheel_install_with_unwritable_prefix_does_not_support_self_upgrade(
    tmp_path: Path,
) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root ignores the write-permission bit; cannot exercise this branch")
    dist = _fake_distribution(None)
    venv_dir = tmp_path / "venv"
    venv_dir.mkdir()
    venv_dir.chmod(0o500)  # read + execute, not write
    try:
        info = detect_install_method(
            env={},
            **_NO_HIT,
            distribution=dist,
            sys_prefix=str(venv_dir),
            sys_base_prefix="/usr",
            posix=True,
        )
    finally:
        venv_dir.chmod(0o700)  # restore so pytest's tmp cleanup can remove it
    assert info == InstallInfo(method="pip", variant=None, self_upgrade_supported=False)


def test_wheel_install_outside_a_venv_is_pip_without_self_upgrade() -> None:
    """Present, no direct_url, but sys.prefix == sys.base_prefix (system Python):
    not case 5, so reported as pip for display but never offers self-upgrade."""
    dist = _fake_distribution(None)
    info = detect_install_method(
        env={},
        **_NO_HIT,
        distribution=dist,
        sys_prefix="/usr",
        sys_base_prefix="/usr",
        posix=True,
    )
    assert info == InstallInfo(method="pip", variant=None, self_upgrade_supported=False)


def test_non_editable_direct_url_is_pip_without_self_upgrade() -> None:
    """A VCS/URL install (direct_url present, no dir_info) is not editable and
    not case 5 either; reported as pip, self-upgrade never offered."""
    dist = _fake_distribution({"url": "https://example.com/repo.git"})
    info = detect_install_method(
        env={},
        **_NO_HIT,
        distribution=dist,
        sys_prefix="/venv",
        sys_base_prefix="/usr",
        posix=True,
    )
    assert info == InstallInfo(method="pip", variant=None, self_upgrade_supported=False)


def test_malformed_direct_url_json_is_treated_as_no_direct_url(tmp_path: Path) -> None:
    dist = MagicMock()
    dist.read_text.return_value = "{not json"
    venv_dir = tmp_path / "venv"
    venv_dir.mkdir()
    info = detect_install_method(
        env={},
        **_NO_HIT,
        distribution=dist,
        sys_prefix=str(venv_dir),
        sys_base_prefix="/usr",
        posix=True,
    )
    # Falls through to case 5 (no usable direct_url) since it is in a venv.
    assert info.method == "pip"
    assert info.self_upgrade_supported is True


def test_as_dict_shape() -> None:
    info = InstallInfo(method="pip", variant=None, self_upgrade_supported=True)
    assert info.as_dict() == {
        "install_method": "pip",
        "variant": None,
        "self_upgrade_supported": True,
    }
