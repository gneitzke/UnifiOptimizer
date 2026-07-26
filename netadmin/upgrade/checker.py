"""PyPI version-check job (docs/ARCHITECTURE.md section 23).

A total no-op when ``updates.check`` is false. Otherwise :class:`VersionChecker`
runs one background task that asks PyPI, once, whether a newer
``unifioptimizer`` release exists, then keeps asking on ``updates.interval_s``
(default once a day). The daemon sends nothing else and no one else: one GET to
``https://pypi.org/pypi/unifioptimizer/json`` with a ``User-Agent`` naming this
build, nothing in the body, no telemetry, no callback URL.

The result is cached in the store's ``app_meta`` table (migration 0007) so a
restart shows the last known answer immediately instead of "unknown" until the
next tick, and so the check surviving a crash means the *daemon* restarting
does not itself trigger a fresh PyPI hit before the configured interval is up.

A check failure (network down, PyPI unreachable, a malformed response) is
logged once and never raised further: it keeps whatever was last cached,
degrades nothing on ``/api/health``, and never blocks startup -- the whole
point of this being a background job instead of a startup dependency.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx

from netadmin import __version__
from netadmin.config import Settings
from netadmin.logging import get_logger
from netadmin.store.repository import Repository

__all__ = [
    "PYPI_URL",
    "SKIP_VERSION_KEY",
    "SNOOZE_UNTIL_KEY",
    "VersionStatus",
    "VersionChecker",
    "build_version_checker",
    "parse_version",
    "read_cached_status",
]

_log = get_logger("upgrade.checker")

PYPI_URL = "https://pypi.org/pypi/unifioptimizer/json"

_REQUEST_TIMEOUT_S = 5.0
_FIRST_CHECK_MIN_S = 60.0
_FIRST_CHECK_MAX_S = 300.0

# app_meta keys (migration 0007). Namespaced under "update." so the one small
# key/value table can hold unrelated future facts without a name collision. The
# skip/snooze keys are written by the API router (POST /api/system/update/dismiss),
# not this module, but live here since this module owns the "update.*" namespace.
_META_LATEST_VERSION = "update.latest_version"
_META_CHECKED_TS = "update.checked_ts"
SKIP_VERSION_KEY = "update.skip_version"
SNOOZE_UNTIL_KEY = "update.snooze_until_ts"

Sleeper = Callable[[float], Any]


def parse_version(text: str) -> Optional[tuple[int, int, int]]:
    """Strict ``X.Y.Z`` parse, or ``None``.

    Deliberately rejects anything looser (pre-releases, build metadata, a
    fourth segment, non-numeric parts): a malformed or unexpected string from
    PyPI must never win or lose a version comparison by accident, it must
    simply fail to compare at all.
    """
    parts = text.strip().split(".")
    if len(parts) != 3:
        return None
    try:
        major, minor, patch = (int(p) for p in parts)
    except ValueError:
        return None
    return (major, minor, patch)


@dataclass(frozen=True)
class VersionStatus:
    """What the checker currently knows, cached or freshly fetched."""

    current_version: str
    latest_version: Optional[str]
    update_available: bool
    checked_ts: Optional[int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "update_available": self.update_available,
            "checked_ts": self.checked_ts,
        }


class VersionChecker:
    """Checks PyPI for a newer release on its own schedule; caches in ``app_meta``.

    Built by :func:`build_version_checker` and driven by the daemon lifespan
    exactly like the HA publisher / alert dispatcher: ``start`` is a no-op when
    disabled by config, otherwise it spawns one background task; ``stop`` tears
    it down. ``check_now`` is also callable directly (a forced refresh, e.g. a
    future ``POST /api/system/update/check``).
    """

    def __init__(
        self,
        settings: Settings,
        store: Repository,
        *,
        client: Optional[httpx.AsyncClient] = None,
        sleeper: Optional[Sleeper] = None,
        rng: Optional[random.Random] = None,
        wall_clock: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        self._cfg = settings.updates
        self._store = store
        self._client = client if client is not None else httpx.AsyncClient()
        self._owns_client = client is None
        self._sleep: Sleeper = sleeper or asyncio.sleep
        self._rng = rng or random.Random()
        self._wall_clock = wall_clock
        self._task: Optional[asyncio.Task[None]] = None
        self._running = False

    # -- introspection ---------------------------------------------------- #
    @property
    def enabled(self) -> bool:
        return bool(self._cfg.check)

    @property
    def running(self) -> bool:
        return self._running

    # -- lifecycle ---------------------------------------------------------- #
    async def start(self) -> None:
        """Spawn the background check loop, or no-op when disabled/already running."""
        if not self._cfg.check:
            _log.info("version check disabled (updates.check=false); not starting")
            return
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Cancel the background loop and close any client this instance owns."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - teardown only
                pass
            self._task = None
        if self._owns_client:
            await self._client.aclose()

    async def _loop(self) -> None:
        """First check jittered 60-300 s after start, then every ``interval_s``."""
        first_delay = self._rng.uniform(_FIRST_CHECK_MIN_S, _FIRST_CHECK_MAX_S)
        try:
            await self._sleep(first_delay)
        except asyncio.CancelledError:
            return
        while self._running:
            await self.check_now()
            try:
                await self._sleep(max(1, int(self._cfg.interval_s)))
            except asyncio.CancelledError:
                return

    # -- the check itself ---------------------------------------------------- #
    async def check_now(self) -> VersionStatus:
        """Force a refresh. On any failure: log once, keep the cached result."""
        try:
            resp = await self._client.get(
                PYPI_URL,
                timeout=_REQUEST_TIMEOUT_S,
                headers={"User-Agent": f"unifioptimizer/{__version__}"},
            )
            resp.raise_for_status()
            data = resp.json()
            latest = str(data["info"]["version"])
            if parse_version(latest) is None:
                raise ValueError(f"unparseable version from PyPI: {latest!r}")
        except Exception as exc:  # noqa: BLE001 - a check failure must never crash the daemon
            _log.warning("version check failed (keeping last known result): %s", exc)
            return self.cached_status()

        now = self._wall_clock()
        self._store.set_app_meta(_META_LATEST_VERSION, latest)
        self._store.set_app_meta(_META_CHECKED_TS, str(now))
        return self._status(latest, now)

    def cached_status(self) -> VersionStatus:
        """The last cached answer, with no network access at all."""
        return read_cached_status(self._store)

    def _status(self, latest: Optional[str], checked_ts: Optional[int]) -> VersionStatus:
        return _build_status(latest, checked_ts)


def _build_status(latest: Optional[str], checked_ts: Optional[int]) -> VersionStatus:
    current = parse_version(__version__)
    target = parse_version(latest) if latest else None
    available = bool(current is not None and target is not None and target > current)
    return VersionStatus(
        current_version=__version__,
        latest_version=latest,
        update_available=available,
        checked_ts=checked_ts,
    )


def read_cached_status(store: Repository) -> VersionStatus:
    """The last cached answer for ``store``, with no network access and no
    :class:`VersionChecker` instance required.

    The free-function counterpart to :meth:`VersionChecker.cached_status`, for a
    caller -- ``GET``/``POST /api/system/update*`` -- that only needs a read and
    should not have to construct (and thus own an ``httpx.AsyncClient`` for) a
    full checker just to read two cached values back.
    """
    latest = store.get_app_meta(_META_LATEST_VERSION)
    checked_raw = store.get_app_meta(_META_CHECKED_TS)
    checked = int(checked_raw) if checked_raw is not None else None
    return _build_status(latest, checked)


def build_version_checker(settings: Settings, store: Repository) -> VersionChecker:
    """Construct the checker for the daemon lifespan (mirrors the HA/alerts factories)."""
    return VersionChecker(settings, store)
