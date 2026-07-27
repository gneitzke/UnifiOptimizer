"""netadmin: the UnifiOptimizer rebuild core.

A stateful network-admin engine: local time-series store, continuous
collector, confounder-aware detectors, and an issue-lifecycle engine that
tracks every finding from first sighting to verified fix. See
``docs/ARCHITECTURE.md`` for the binding design.
"""

from __future__ import annotations

import os
import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path


# The PyPI distribution is "unifioptimizer" (the product name); the import
# package stays "netadmin" (pyproject.toml explains the split). Deriving
# __version__ from the installed distribution's own metadata is what keeps it
# from ever drifting from ``pyproject.toml``'s ``[project].version`` again --
# this used to be a hand-maintained literal that silently fell behind (stuck at
# "0.1.0" while the package had moved on to "0.3.0"), which would have made the
# self-update check (docs/ARCHITECTURE.md section 23) tell every user they were
# perpetually behind. A checkout with no installed distribution at all (running
# straight off PYTHONPATH, no ``pip install -e .``) has no metadata to read;
# "0.0.0.dev0" is an honest "not a real, versioned install" sentinel for that
# case, never a guess.
def _resolve_version() -> str:
    """The running version, resolved from the most authoritative source available.

    1. The installed distribution's metadata. Correct for every pip install, and
       impossible to drift from pyproject.toml.
    2. ``NETADMIN_VERSION``, baked into the container image at build time. The
       image COPYs the source tree rather than pip-installing it, so there is no
       distribution to interrogate; without this the dashboard reported
       "0.0.0.dev0" on the deployed daemon while PyPI said 0.4.0, which defeats
       the point of showing a version at all.
    3. ``pyproject.toml`` next to the source, for a bare checkout run off
       PYTHONPATH.
    4. A sentinel, which is honest about not being a real versioned install.
    """
    # An explicit build/operator override wins over everything inferred.
    baked_first = os.environ.get("NETADMIN_VERSION")
    if baked_first:
        return baked_first.strip()
    # Otherwise a source checkout wins over installed metadata. Running with PYTHONPATH=. against
    # a stale site-packages copy would otherwise report the INSTALLED version while
    # executing checkout code, which is the same drift this function exists to stop.
    here = Path(__file__).resolve()
    if not any(part in {"site-packages", "dist-packages"} for part in here.parts):
        try:
            pyproject = here.parent.parent / "pyproject.toml"
            with pyproject.open("rb") as fh:
                return str(tomllib.load(fh)["project"]["version"])
        except Exception:  # noqa: BLE001 - fall through to metadata
            pass
    try:
        return _pkg_version("unifioptimizer")
    except PackageNotFoundError:
        pass
    baked = os.environ.get("NETADMIN_VERSION")
    if baked:
        return baked.strip()
    try:
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        with pyproject.open("rb") as fh:
            return str(tomllib.load(fh)["project"]["version"])
    except Exception:  # noqa: BLE001 - any failure just means "unknown"
        return "0.0.0.dev0"


__version__ = _resolve_version()
__all__ = ["__version__"]
