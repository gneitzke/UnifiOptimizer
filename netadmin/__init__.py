"""netadmin: the UnifiOptimizer rebuild core.

A stateful network-admin engine: local time-series store, continuous
collector, confounder-aware detectors, and an issue-lifecycle engine that
tracks every finding from first sighting to verified fix. See
``docs/ARCHITECTURE.md`` for the binding design.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

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
try:
    __version__ = _pkg_version("unifioptimizer")
except PackageNotFoundError:  # pragma: no cover - only hit off a bare checkout
    __version__ = "0.0.0.dev0"

__all__ = ["__version__"]
