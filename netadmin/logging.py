"""Logging setup for netadmin: rotating file + rich console.

Stdlib ``logging`` only (this module is ``netadmin.logging``; ``import logging``
resolves to the stdlib via absolute imports). Configuration is idempotent and
lazy: call :func:`get_logger` and the handlers are installed on first use. No
``print`` is used anywhere in the package.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from rich.logging import RichHandler

from netadmin.config import DEFAULT_LOG_DIR

LOG_FILENAME = "netadmin.log"
MAX_BYTES = 10 * 1024 * 1024  # 10 MB
BACKUP_COUNT = 5
_FILE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

_ROOT_NAME = "netadmin"
_configured = False


def configure_logging(
    level: int | str = logging.INFO,
    log_dir: Optional[Path] = None,
    *,
    force: bool = False,
) -> logging.Logger:
    """Install the rotating-file and rich-console handlers on the ``netadmin``
    logger. Safe to call repeatedly; a second call is a no-op unless ``force``.
    """
    global _configured

    root = logging.getLogger(_ROOT_NAME)
    if _configured and not force:
        return root

    if isinstance(level, str):
        level = logging.getLevelName(level.upper())
        if not isinstance(level, int):
            level = logging.INFO

    root.setLevel(level)
    root.handlers.clear()
    root.propagate = False  # own the netadmin namespace; don't double-log

    target_dir = Path(log_dir) if log_dir is not None else DEFAULT_LOG_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        target_dir / LOG_FILENAME,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(_FILE_FORMAT, datefmt=_DATE_FORMAT))
    file_handler.setLevel(level)
    root.addHandler(file_handler)

    console_handler = RichHandler(
        rich_tracebacks=True,
        show_path=False,
        omit_repeated_times=False,
    )
    console_handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
    console_handler.setLevel(level)
    root.addHandler(console_handler)

    _configured = True
    return root


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger, configuring handlers on first use.

    ``name`` may be a bare module name or a dotted path; it is placed under the
    ``netadmin`` root so all package logs share the same handlers.
    """
    if not _configured:
        configure_logging()

    if name == _ROOT_NAME or name.startswith(_ROOT_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_ROOT_NAME}.{name}")


__all__ = [
    "LOG_FILENAME",
    "MAX_BYTES",
    "BACKUP_COUNT",
    "configure_logging",
    "get_logger",
]
