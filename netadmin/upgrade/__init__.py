"""Self-update foundation: install-method detection + PyPI version check
(docs/ARCHITECTURE.md section 23).

This package currently holds the *foundation* only: detecting how the daemon
was installed (:mod:`netadmin.upgrade.detect`) and a background job that checks
PyPI for a newer release (:mod:`netadmin.upgrade.checker`). The API routes, the
web banner, and the pip self-upgrade runner are later phases of the same
design and are not implemented here.
"""

from netadmin.upgrade.checker import (
    PYPI_URL,
    VersionChecker,
    VersionStatus,
    build_version_checker,
    parse_version,
)
from netadmin.upgrade.detect import InstallInfo, InstallMethod, UpdateVariant, detect_install_method

__all__ = [
    "PYPI_URL",
    "VersionChecker",
    "VersionStatus",
    "build_version_checker",
    "parse_version",
    "InstallInfo",
    "InstallMethod",
    "UpdateVariant",
    "detect_install_method",
]
