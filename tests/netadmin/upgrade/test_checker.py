"""VersionChecker: the PyPI version-check job and its app_meta cache.

Every real network call goes through ``respx`` against the exact
``https://pypi.org/pypi/unifioptimizer/json`` URL; no test opens a socket. The
background loop's timing is driven by an injected fake sleeper synchronised
with ``asyncio.Event``s rather than real delays or ``asyncio.sleep(0)`` step
counting, so these tests are deterministic regardless of how many internal
await points the mocked HTTP call happens to have.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from netadmin.config import Settings
from netadmin.store.repository import Repository
from netadmin.upgrade.checker import PYPI_URL, VersionChecker, build_version_checker, parse_version

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# parse_version
# --------------------------------------------------------------------------- #


async def test_parse_version_strict_triple() -> None:
    assert parse_version("1.2.3") == (1, 2, 3)


@pytest.mark.parametrize(
    "text", ["1.2", "1.2.3.4", "1.2.3rc1", "v1.2.3", "1.2.x", "", "  ", "1..3"]
)
async def test_parse_version_rejects_anything_looser(text: str) -> None:
    assert parse_version(text) is None


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def store(tmp_db_path: Path) -> Any:
    repo = Repository.open(tmp_db_path)
    yield repo
    repo.close()


@pytest.fixture
def settings(tmp_db_path: Path) -> Settings:
    return Settings(_env_file=None, db_path=tmp_db_path)


def _pypi_response(version: str) -> httpx.Response:
    return httpx.Response(200, json={"info": {"version": version}})


# --------------------------------------------------------------------------- #
# check_now: success, caching, comparison
# --------------------------------------------------------------------------- #


@respx.mock
async def test_check_now_caches_the_latest_version(settings: Settings, store: Any) -> None:
    respx.get(PYPI_URL).mock(return_value=_pypi_response("9.9.9"))
    checker = VersionChecker(settings, store, wall_clock=lambda: 1_000)

    status = await checker.check_now()

    assert status.latest_version == "9.9.9"
    assert status.checked_ts == 1_000
    assert store.get_app_meta("update.latest_version") == "9.9.9"
    assert store.get_app_meta("update.checked_ts") == "1000"
    await checker.stop()


@respx.mock
async def test_check_now_sends_no_body_and_a_versioned_user_agent(
    settings: Settings, store: Any
) -> None:
    route = respx.get(PYPI_URL).mock(return_value=_pypi_response("1.0.0"))
    checker = VersionChecker(settings, store)

    await checker.check_now()

    sent = route.calls.last.request
    assert sent.content == b""
    assert sent.headers["User-Agent"].startswith("unifioptimizer/")
    await checker.stop()


@respx.mock
async def test_update_available_true_when_latest_is_newer(settings: Settings, store: Any) -> None:
    respx.get(PYPI_URL).mock(return_value=_pypi_response("999.0.0"))
    checker = VersionChecker(settings, store)

    status = await checker.check_now()

    assert status.update_available is True
    await checker.stop()


@respx.mock
async def test_update_available_false_when_latest_is_not_newer(
    settings: Settings, store: Any
) -> None:
    respx.get(PYPI_URL).mock(return_value=_pypi_response("0.0.1"))
    checker = VersionChecker(settings, store)

    status = await checker.check_now()

    assert status.update_available is False
    await checker.stop()


# --------------------------------------------------------------------------- #
# failure handling: log once, keep the cache, never raise
# --------------------------------------------------------------------------- #


@respx.mock
async def test_network_failure_keeps_the_previous_cache(settings: Settings, store: Any) -> None:
    respx.get(PYPI_URL).mock(return_value=_pypi_response("2.0.0"))
    checker = VersionChecker(settings, store, wall_clock=lambda: 1_000)
    first = await checker.check_now()
    assert first.latest_version == "2.0.0"

    respx.get(PYPI_URL).mock(side_effect=httpx.ConnectError("no route"))
    second = await checker.check_now()

    assert second.latest_version == "2.0.0"  # unchanged: the stale cache survives
    assert second.checked_ts == 1_000
    await checker.stop()


@respx.mock
async def test_http_error_status_keeps_cache_and_never_raises(
    settings: Settings, store: Any
) -> None:
    respx.get(PYPI_URL).mock(return_value=httpx.Response(503))
    checker = VersionChecker(settings, store)

    status = await checker.check_now()  # must not raise

    assert status.latest_version is None
    assert status.update_available is False
    await checker.stop()


@respx.mock
async def test_malformed_version_string_is_rejected_not_cached(
    settings: Settings, store: Any
) -> None:
    respx.get(PYPI_URL).mock(return_value=_pypi_response("not-a-version"))
    checker = VersionChecker(settings, store)

    status = await checker.check_now()

    assert status.latest_version is None
    assert store.get_app_meta("update.latest_version") is None
    await checker.stop()


async def test_cached_status_with_nothing_cached_yet(settings: Settings, store: Any) -> None:
    checker = VersionChecker(settings, store)
    status = checker.cached_status()
    assert status.latest_version is None
    assert status.checked_ts is None
    assert status.update_available is False
    await checker.stop()


# --------------------------------------------------------------------------- #
# start/stop lifecycle
# --------------------------------------------------------------------------- #


async def test_disabled_by_config_start_is_a_total_noop(tmp_db_path: Path, store: Any) -> None:
    settings = Settings(_env_file=None, db_path=tmp_db_path, updates={"check": False})
    checker = VersionChecker(settings, store)

    await checker.start()

    assert checker.running is False
    await checker.stop()


async def test_start_records_a_jittered_first_delay_then_parks(
    settings: Settings, store: Any
) -> None:
    """The first check waits 60-300 s (jittered); prove the delay lands in that
    range without ever waiting for real, by parking the fake sleeper on an event
    that never fires and inspecting what it was called with."""
    sleeps: list[float] = []
    started = asyncio.Event()
    park = asyncio.Event()

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        started.set()
        await park.wait()

    checker = VersionChecker(settings, store, sleeper=fake_sleep)
    await checker.start()
    await asyncio.wait_for(started.wait(), timeout=2.0)

    assert checker.running is True
    assert len(sleeps) == 1
    assert 60.0 <= sleeps[0] <= 300.0

    await checker.stop()  # cancels the parked task cleanly
    assert checker.running is False


@respx.mock
async def test_loop_checks_once_then_sleeps_for_the_configured_interval(
    tmp_db_path: Path, store: Any
) -> None:
    settings = Settings(_env_file=None, db_path=tmp_db_path, updates={"interval_s": 111})
    respx.get(PYPI_URL).mock(return_value=_pypi_response("1.2.3"))

    sleeps: list[float] = []
    reached_interval_sleep = asyncio.Event()
    park = asyncio.Event()

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) == 2:
            reached_interval_sleep.set()
            await park.wait()

    checker = VersionChecker(settings, store, sleeper=fake_sleep)
    await checker.start()
    await asyncio.wait_for(reached_interval_sleep.wait(), timeout=2.0)

    # A check_now() ran between the jittered first sleep and the interval sleep.
    assert store.get_app_meta("update.latest_version") == "1.2.3"
    assert 60.0 <= sleeps[0] <= 300.0  # the jittered first delay
    assert sleeps[1] == 111  # the configured interval, verbatim

    await checker.stop()


async def test_stop_before_start_is_safe(settings: Settings, store: Any) -> None:
    checker = VersionChecker(settings, store)
    await checker.stop()  # must not raise


async def test_build_version_checker_returns_a_checker(settings: Settings, store: Any) -> None:
    checker = build_version_checker(settings, store)
    assert isinstance(checker, VersionChecker)
    await checker.stop()
