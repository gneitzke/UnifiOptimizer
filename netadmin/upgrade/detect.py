"""Install-method detection (docs/ARCHITECTURE.md section 23).

The self-update banner and CLI need to tell a user four different truths
depending on how they got the daemon running: "click Update", "run this one
host command", "update the add-on from Settings", or "git pull and reinstall".
Getting this wrong in either direction is actively harmful: offering a pip
self-upgrade inside a container upgrades nothing (the wheel is baked into the
image, not the running venv) and offering "git pull" to a pip install sends
someone hunting for a checkout that does not exist.

Detection is a strict first-hit-wins ladder, cheapest and most explicit checks
first:

1. ``NETADMIN_INSTALL_METHOD`` env var — baked into the container image
   (``Dockerfile.netadmin``) and the add-on entrypoint (``addon/run.sh``), and
   available as an explicit override anywhere else (tests, an unusual install).
   ``NETADMIN_UPDATE_VARIANT`` rides along to distinguish the two container
   deploy paths (``compose`` vs ``macmini``) that share one Dockerfile.
2. Home Assistant add-on — ``SUPERVISOR_TOKEN`` is set only inside a
   Supervisor-managed container.
3. Any other container — ``/.dockerenv`` or ``/proc/1/cgroup`` naming
   docker/containerd/podman.
4. A source checkout — either the distribution is not installed at all (a
   bare ``PYTHONPATH`` checkout), or it is installed editable
   (``direct_url.json``'s ``dir_info.editable``).
5. A pip virtualenv install — the distribution is installed, not editable, no
   ``direct_url.json`` at all (an ordinary wheel from PyPI), and the running
   interpreter is inside a venv (``sys.prefix != sys.base_prefix``).

``self_upgrade_supported`` is never a fake button: it is true only when
detection actually resolved through case 5 (a real, self-detected pip venv)
*and* that venv's ``sys.prefix`` is writable *and* the platform is POSIX. An
explicit ``NETADMIN_INSTALL_METHOD=pip`` override, a system-wide (non-venv) pip
install, or a non-editable ``direct_url.json`` (e.g. a VCS/URL install) are all
reported as ``pip`` for display purposes but never offer self-upgrade.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Literal, Mapping, Optional

__all__ = ["InstallMethod", "UpdateVariant", "InstallInfo", "detect_install_method"]

InstallMethod = Literal["pip", "container", "addon", "source"]
UpdateVariant = Literal["compose", "macmini"]

_VALID_METHODS: tuple[str, ...] = ("pip", "container", "addon", "source")
_VALID_VARIANTS: tuple[str, ...] = ("compose", "macmini")

_DIST_NAME = "unifioptimizer"
_CONTAINER_CGROUP_TOKENS: tuple[str, ...] = ("docker", "containerd", "podman")


@dataclass(frozen=True)
class InstallInfo:
    """The detected install method plus what that implies for self-update."""

    method: InstallMethod
    variant: Optional[str]
    self_upgrade_supported: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "install_method": self.method,
            "variant": self.variant,
            "self_upgrade_supported": self.self_upgrade_supported,
        }


def _cgroup_mentions_container(path: Path) -> bool:
    """True if ``path`` (normally ``/proc/1/cgroup``) names a container runtime.

    Missing (macOS, most non-Linux hosts) or unreadable is honestly "no
    evidence of a container here", not an error -- this check is one signal
    among several, not the only one.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    lowered = text.lower()
    return any(token in lowered for token in _CONTAINER_CGROUP_TOKENS)


def _load_distribution(name: str) -> Optional[importlib_metadata.Distribution]:
    try:
        return importlib_metadata.distribution(name)
    except importlib_metadata.PackageNotFoundError:
        return None


def _direct_url_editable(dist: importlib_metadata.Distribution) -> Optional[bool]:
    """Editable-ness of an installed distribution's ``direct_url.json``.

    Returns ``None`` when there is no ``direct_url.json`` at all (the ordinary
    case for a wheel installed off PyPI); ``True``/``False`` when one is
    present, read from ``dir_info.editable`` (a direct_url with no
    ``dir_info`` -- e.g. a VCS or URL install -- is present but not editable,
    so that is ``False``, not ``None``).
    """
    try:
        raw = dist.read_text("direct_url.json")
    except Exception:  # noqa: BLE001 - a malformed distribution is not "editable"
        return None
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    dir_info = data.get("dir_info") if isinstance(data, dict) else None
    if not isinstance(dir_info, dict):
        return False
    return bool(dir_info.get("editable", False))


def _prefix_writable(prefix: str) -> bool:
    try:
        return os.access(prefix, os.W_OK)
    except OSError:  # pragma: no cover - defensive
        return False


def detect_install_method(
    *,
    env: Optional[Mapping[str, str]] = None,
    dockerenv_path: Path = Path("/.dockerenv"),
    cgroup_path: Path = Path("/proc/1/cgroup"),
    distribution: Optional[importlib_metadata.Distribution] = None,
    distribution_missing: bool = False,
    sys_prefix: Optional[str] = None,
    sys_base_prefix: Optional[str] = None,
    posix: Optional[bool] = None,
) -> InstallInfo:
    """Detect how this daemon was installed. First hit wins (see module docstring).

    Every environment fact is an optional parameter with a real-world default,
    so production calls this with no arguments and tests can drive each branch
    directly without faking a filesystem or interpreter. ``distribution``
    overrides the real ``importlib.metadata`` lookup only when explicitly
    passed (including ``None`` alongside ``distribution_missing=True`` to mean
    "not installed"); omitted, the real installed ``unifioptimizer``
    distribution is looked up.
    """
    env = os.environ if env is None else env
    sys_prefix = sys.prefix if sys_prefix is None else sys_prefix
    sys_base_prefix = sys.base_prefix if sys_base_prefix is None else sys_base_prefix
    is_posix = (os.name == "posix") if posix is None else posix

    # 1. Explicit override -- baked into the container/add-on images, or set by
    # hand. Never implies self-upgrade: an override is a statement of fact by
    # whoever set it, not evidence this process detected a real pip venv.
    override = env.get("NETADMIN_INSTALL_METHOD")
    if override in _VALID_METHODS:
        variant = env.get("NETADMIN_UPDATE_VARIANT")
        variant = variant if variant in _VALID_VARIANTS else None
        return InstallInfo(method=override, variant=variant, self_upgrade_supported=False)  # type: ignore[arg-type]

    # 2. Home Assistant add-on.
    if env.get("SUPERVISOR_TOKEN"):
        return InstallInfo(method="addon", variant=None, self_upgrade_supported=False)

    # 3. Any other container.
    if dockerenv_path.exists() or _cgroup_mentions_container(cgroup_path):
        return InstallInfo(method="container", variant=None, self_upgrade_supported=False)

    # 4 & 5. Distinguish a source checkout from a pip install by asking
    # importlib.metadata about the installed distribution. ``distribution``
    # (including an explicit ``None`` paired with ``distribution_missing``)
    # overrides the real lookup for tests; production always takes the real
    # lookup by leaving both at their defaults.
    if distribution is not None or distribution_missing:
        dist = distribution
    else:
        dist = _load_distribution(_DIST_NAME)

    if dist is None:
        # No distribution metadata at all: a bare checkout running off
        # PYTHONPATH/sys.path with no `pip install` of any kind.
        return InstallInfo(method="source", variant=None, self_upgrade_supported=False)

    editable = _direct_url_editable(dist)
    if editable:
        return InstallInfo(method="source", variant=None, self_upgrade_supported=False)

    in_venv = sys_prefix != sys_base_prefix
    if editable is None and in_venv:
        # Case 5: a real, self-detected pip virtualenv install.
        self_upgrade = is_posix and _prefix_writable(sys_prefix)
        return InstallInfo(method="pip", variant=None, self_upgrade_supported=self_upgrade)

    # Distribution present but neither cleanly "source" (editable) nor cleanly
    # case 5 (a non-editable direct_url, e.g. a VCS/URL install, or a
    # system-wide non-venv pip install). Reported as pip for display, but
    # self-upgrade requires the exact case-5 shape above -- never a guess.
    return InstallInfo(method="pip", variant=None, self_upgrade_supported=False)
