"""First-run setup endpoint tests (ARCHITECTURE.md 18), fully offline.

Every case drives the ASGI app over ``httpx.ASGITransport`` (no uvicorn, no
network). The controller is a fake (the validation probe's client is swapped for a
stub; ``detect_console`` is monkeypatched), the ingest factory is faked so a
hot-start starts a fake scheduler, and the credential is written to a TEMP
secrets file -- never the real data/secrets.env.

Pins: the status discriminator + its transitions; detect returns the console +
playbook + console URL; connect happy path validates read-only, writes secrets
(0600), returns the UI token, hot-starts ingest, and NEVER returns/logs the UniFi
key; a bad key is rejected cleanly and writes nothing; connect 409s / setup 401s
once configured.
"""

from __future__ import annotations

import asyncio
import logging
import stat
from pathlib import Path
from typing import Any, Optional

import httpx
import pytest

from netadmin.config import Settings
from netadmin.ingest.unifi.auth import UnifiAuthError, UnifiConnectionError
from netadmin.ingest.unifi.detect import (
    AUTH_API_KEY,
    AUTH_UNIFI_OS_COOKIE,
    KIND_CLOUDKEY_GEN2_PLUS,
    KIND_UNREACHABLE,
    ConsoleInfo,
)
from netadmin.server.main import DaemonComponents, create_app
from netadmin.server.routers import setup as setup_mod
from netadmin.store.repository import Repository

from .conftest import FakeScheduler, FakeSupervisor

pytestmark = pytest.mark.asyncio

API_KEY = "SECRET-API-KEY-do-not-leak-abc123"
HOST = "unifi.test.local"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def secrets_path(tmp_path: Path) -> Path:
    """A TEMP secrets file -- the connect handler writes here, never the real one."""
    return tmp_path / "secrets.env"


@pytest.fixture
def unconfigured_settings(tmp_db_path: Path) -> Settings:
    return Settings(_env_file=None, db_path=tmp_db_path, site_id="default")


@pytest.fixture
def configured_settings(tmp_db_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        db_path=tmp_db_path,
        site_id="default",
        unifi_host="https://unifi.test",
        unifi_api_key="boot-key",
        netadmin_api_token="boot-token",
    )


@pytest.fixture
def open_store(unconfigured_settings: Settings) -> Repository:
    store = Repository.open(unconfigured_settings.db_path, site_id=unconfigured_settings.site_id)
    yield store
    store.close()


@pytest.fixture
def setup_app(unconfigured_settings: Settings, open_store: Repository, secrets_path: Path) -> Any:
    app = create_app(
        settings=unconfigured_settings, store=open_store, components=DaemonComponents()
    )
    app.state.secrets_path = secrets_path
    return app


@pytest.fixture
def configured_app(
    configured_settings: Settings, open_store: Repository, secrets_path: Path
) -> Any:
    app = create_app(settings=configured_settings, store=open_store, components=DaemonComponents())
    app.state.secrets_path = secrets_path
    return app


async def _client(app: Any) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeProbeClient:
    """A read-only controller stub for the validation probe.

    Records every call so a test can prove the probe issues only ``connect`` + a
    single ``stat/device`` GET and never a mutating request.
    """

    def __init__(
        self, *, connect_exc: Optional[Exception] = None, get_exc: Optional[Exception] = None
    ) -> None:
        self.connect_exc = connect_exc
        self.get_exc = get_exc
        self.calls: list[tuple[str, Any]] = []
        self.closed = False

    async def connect(self) -> None:
        self.calls.append(("connect", None))
        if self.connect_exc is not None:
            raise self.connect_exc

    async def get_json(self, endpoint: str, params: Any = None) -> dict[str, Any]:
        self.calls.append(("get_json", endpoint))
        if self.get_exc is not None:
            raise self.get_exc
        return {"meta": {"rc": "ok"}, "data": []}

    async def post_json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:  # pragma: no cover
        self.calls.append(("post_json", args))
        raise AssertionError("validation probe must be read-only; no POST allowed")

    async def aclose(self) -> None:
        self.closed = True


class FakeCollector:
    def __init__(self) -> None:
        self.status = object()


class FakeBuilt:
    """Stand-in for netadmin.ingest.factory.BuiltComponents."""

    def __init__(self) -> None:
        self.scheduler = FakeScheduler()
        self.ws_supervisor = FakeSupervisor()
        self.probes = FakeSupervisor()
        self.collector = FakeCollector()

        async def _backfill() -> None:
            return None

        self.backfill = _backfill


def _install_fake_probe(monkeypatch: pytest.MonkeyPatch, client: FakeProbeClient) -> None:
    def _factory(**_kwargs: Any) -> FakeProbeClient:
        return client

    monkeypatch.setattr(setup_mod, "_build_probe_client", _factory)


def _install_fake_ingest(monkeypatch: pytest.MonkeyPatch) -> list[FakeBuilt]:
    """Fake the ingest factory so a hot-start builds + starts fakes. Returns the
    list the built bundle lands in so a test can assert the scheduler started."""
    built: list[FakeBuilt] = []

    def _fake_build_components(settings: Any, store: Any, *, issue_engine: Any = None) -> FakeBuilt:
        b = FakeBuilt()
        built.append(b)
        return b

    monkeypatch.setattr("netadmin.ingest.factory.build_components", _fake_build_components)
    return built


class _LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def text(self) -> str:
        fmt = logging.Formatter("%(name)s %(levelname)s %(message)s")
        return "\n".join(fmt.format(r) for r in self.records)


def _cloudkey_info() -> ConsoleInfo:
    return ConsoleInfo(
        kind=KIND_CLOUDKEY_GEN2_PLUS,
        model="UCK-G2-Plus",
        is_unifi_os=True,
        network_version="9.1.0",
        api_key_supported=True,
        recommended_auth=AUTH_API_KEY,
        reachable=True,
    )


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #
async def test_status_unconfigured(setup_app: Any) -> None:
    async with await _client(setup_app) as c:
        resp = await c.get("/api/setup/status")
    assert resp.status_code == 200
    assert resp.json() == {"configured": False, "controller_connected": False}


async def test_status_configured_from_boot(configured_app: Any) -> None:
    # An install with secrets already present is configured from boot -> token gate.
    async with await _client(configured_app) as c:
        resp = await c.get("/api/setup/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["controller_connected"] is False  # ingest not started in this test app


# --------------------------------------------------------------------------- #
# scan (LAN discovery assist)
# --------------------------------------------------------------------------- #
def _install_fake_scan(monkeypatch: pytest.MonkeyPatch, result: Any) -> None:
    """Swap the LAN scanner for a fake so the endpoint runs no real sockets."""

    async def _fake_discover(**_kwargs: Any) -> Any:
        return result

    monkeypatch.setattr(setup_mod, "discover_consoles", _fake_discover)


async def test_scan_returns_confirmed_candidates(
    setup_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from netadmin.server.services.discovery import DiscoveredConsole, DiscoveryResult

    result = DiscoveryResult(
        scanned=["192.168.1.0/24"],
        candidates=[
            DiscoveredConsole(
                host="https://192.168.1.1",
                port=443,
                kind=KIND_CLOUDKEY_GEN2_PLUS,
                label="UniFi CloudKey Gen2 Plus (UCK-G2-Plus)",
                model="UCK-G2-Plus",
                api_key_status="supported",
            )
        ],
    )
    _install_fake_scan(monkeypatch, result)
    async with await _client(setup_app) as c:
        resp = await c.post("/api/setup/scan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["scanned"] == ["192.168.1.0/24"]
    assert body["candidates"][0]["host"] == "https://192.168.1.1"
    assert body["candidates"][0]["kind"] == KIND_CLOUDKEY_GEN2_PLUS


async def test_scan_none_found_is_honest_empty(
    setup_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from netadmin.server.services.discovery import DiscoveryResult

    _install_fake_scan(monkeypatch, DiscoveryResult(scanned=["10.0.0.0/24"], candidates=[]))
    async with await _client(setup_app) as c:
        resp = await c.post("/api/setup/scan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["candidates"] == []


async def test_scan_degrades_to_empty_on_error(
    setup_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(**_kwargs: Any) -> Any:
        raise OSError("scan blew up")

    monkeypatch.setattr(setup_mod, "discover_consoles", _boom)
    async with await _client(setup_app) as c:
        resp = await c.post("/api/setup/scan")
    # A scan failure must never surface as a 500 -- it degrades to "none found".
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "scanned": [], "candidates": []}


async def test_scan_locked_once_configured(configured_app: Any) -> None:
    # Setup routes 409 (or auth-gate) once configured; scan is no exception.
    async with await _client(configured_app) as c:
        no_tok = await c.post("/api/setup/scan")
        with_tok = await c.post("/api/setup/scan", headers={"Authorization": "Bearer boot-token"})
    assert no_tok.status_code == 401
    assert with_tok.status_code == 409


# --------------------------------------------------------------------------- #
# detect
# --------------------------------------------------------------------------- #
async def test_detect_returns_console_playbook_and_url(
    setup_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_detect(host: str, **_kw: Any) -> ConsoleInfo:
        return _cloudkey_info()

    monkeypatch.setattr(setup_mod, "detect_console", _fake_detect)
    async with await _client(setup_app) as c:
        resp = await c.post("/api/setup/detect", json={"host": HOST})
    assert resp.status_code == 200
    body = resp.json()
    assert body["console"]["kind"] == KIND_CLOUDKEY_GEN2_PLUS
    assert body["console"]["model"] == "UCK-G2-Plus"
    assert body["playbook"]["auth_mode"] == "api_key"
    assert body["playbook"]["steps"]  # non-empty guidance
    assert body["console_url"] == "https://unifi.test.local/"


async def test_detect_tolerates_unreachable_host(
    setup_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_detect(host: str, **_kw: Any) -> ConsoleInfo:
        return ConsoleInfo(
            kind=KIND_UNREACHABLE,
            is_unifi_os=False,
            api_key_supported=False,
            recommended_auth=AUTH_UNIFI_OS_COOKIE,
            reachable=False,
            detail="nothing answered",
        )

    monkeypatch.setattr(setup_mod, "detect_console", _fake_detect)
    async with await _client(setup_app) as c:
        resp = await c.post("/api/setup/detect", json={"host": "10.255.255.1"})
    assert resp.status_code == 200  # honest, not an error
    body = resp.json()
    assert body["console"]["kind"] == KIND_UNREACHABLE
    assert body["console"]["reachable"] is False
    assert body["playbook"]["auth_mode"] == "cookie"


async def test_detect_blank_host_400(setup_app: Any) -> None:
    async with await _client(setup_app) as c:
        resp = await c.post("/api/setup/detect", json={"host": "   "})
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_host"


# --------------------------------------------------------------------------- #
# connect -- happy path
# --------------------------------------------------------------------------- #
async def test_connect_happy_path_writes_secrets_returns_token_starts_ingest(
    setup_app: Any, secrets_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = FakeProbeClient()
    _install_fake_probe(monkeypatch, probe)
    built = _install_fake_ingest(monkeypatch)

    cap = _LogCapture()
    root = logging.getLogger("netadmin")
    root.addHandler(cap)
    try:
        async with await _client(setup_app) as c:
            resp = await c.post("/api/setup/connect", json={"host": HOST, "api_key": API_KEY})
        await asyncio.sleep(0.05)  # let the backfill task settle
    finally:
        root.removeHandler(cap)

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    ui_token = body["ui_token"]
    assert isinstance(ui_token, str) and len(ui_token) >= 20

    # The UniFi key is never in the response body, and never logged.
    assert API_KEY not in resp.text
    assert API_KEY not in cap.text()

    # Read-only probe: exactly connect + one stat/device GET, then closed.
    assert probe.calls == [("connect", None), ("get_json", "stat/device")]
    assert probe.closed is True

    # Secrets written to the TEMP file, chmod 600, with the key + host + token.
    assert secrets_path.exists()
    assert stat.S_IMODE(secrets_path.stat().st_mode) == 0o600
    text = secrets_path.read_text(encoding="utf-8")
    assert f"UNIFI_API_KEY={API_KEY}" in text
    assert "UNIFI_HOST=https://unifi.test.local" in text
    assert f"NETADMIN_API_TOKEN={ui_token}" in text

    # Ingest hot-started: the fake scheduler was built and started.
    assert len(built) == 1
    assert built[0].scheduler.started is True

    # Status now reports configured + connected, live, with no restart.
    async with await _client(setup_app) as c:
        status = (await c.get("/api/setup/status")).json()
    assert status["configured"] is True
    assert status["controller_connected"] is True


async def test_connect_accepts_username_password(
    setup_app: Any, secrets_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = FakeProbeClient()
    _install_fake_probe(monkeypatch, probe)
    _install_fake_ingest(monkeypatch)

    async with await _client(setup_app) as c:
        resp = await c.post(
            "/api/setup/connect",
            json={"host": HOST, "username": "admin", "password": "pw-secret"},
        )
        await asyncio.sleep(0.05)
    assert resp.status_code == 200
    text = secrets_path.read_text(encoding="utf-8")
    assert "UNIFI_USERNAME=admin" in text
    assert "UNIFI_PASSWORD=pw-secret" in text
    assert "UNIFI_API_KEY" not in text
    # Password is not echoed back.
    assert "pw-secret" not in resp.text


# --------------------------------------------------------------------------- #
# connect -- rejection paths (write nothing)
# --------------------------------------------------------------------------- #
async def test_connect_rejects_bad_key_writes_nothing(
    setup_app: Any, secrets_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = FakeProbeClient(get_exc=UnifiAuthError("stat/device -> 401 (auth)"))
    _install_fake_probe(monkeypatch, probe)
    built = _install_fake_ingest(monkeypatch)

    async with await _client(setup_app) as c:
        resp = await c.post("/api/setup/connect", json={"host": HOST, "api_key": "wrong"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "auth_failed"
    # Nothing persisted, ingest never built.
    assert not secrets_path.exists()
    assert built == []
    # Still unconfigured.
    async with await _client(setup_app) as c:
        assert (await c.get("/api/setup/status")).json()["configured"] is False


async def test_connect_unreachable_is_clean_error(
    setup_app: Any, secrets_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = FakeProbeClient(connect_exc=UnifiConnectionError("no route"))
    _install_fake_probe(monkeypatch, probe)
    _install_fake_ingest(monkeypatch)
    async with await _client(setup_app) as c:
        resp = await c.post("/api/setup/connect", json={"host": HOST, "api_key": API_KEY})
    assert resp.status_code == 400
    assert resp.json()["code"] == "unreachable"
    assert not secrets_path.exists()


async def test_connect_missing_credential_400(setup_app: Any, secrets_path: Path) -> None:
    async with await _client(setup_app) as c:
        resp = await c.post("/api/setup/connect", json={"host": HOST})
    assert resp.status_code == 400
    assert resp.json()["code"] == "missing_credential"
    assert not secrets_path.exists()


async def test_connect_blank_host_400(setup_app: Any) -> None:
    async with await _client(setup_app) as c:
        resp = await c.post("/api/setup/connect", json={"host": "", "api_key": API_KEY})
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_host"


async def test_connect_rejects_newline_credential_writes_nothing(
    setup_app: Any, secrets_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A newline in the API key must never reach secrets.env -- it would split into a
    # second KEY=VALUE line and smuggle in an attacker-chosen key. The endpoint
    # rejects it cleanly (400) before any probe or write; nothing is persisted.
    probe = FakeProbeClient()
    _install_fake_probe(monkeypatch, probe)
    built = _install_fake_ingest(monkeypatch)

    async with await _client(setup_app) as c:
        resp = await c.post(
            "/api/setup/connect",
            json={"host": HOST, "api_key": "abc\nNETADMIN_API_TOKEN=attacker"},
        )
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_credential"
    # No probe ran, no secrets written, no ingest built, still unconfigured.
    assert probe.calls == []
    assert not secrets_path.exists()
    assert built == []
    async with await _client(setup_app) as c:
        assert (await c.get("/api/setup/status")).json()["configured"] is False


# --------------------------------------------------------------------------- #
# validation probe unit -- error mapping + read-only
# --------------------------------------------------------------------------- #
async def test_validate_credential_maps_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from netadmin.ingest.unifi.auth import TwoFactorRequired

    async def _run(exc: Exception, *, on_connect: bool = False) -> Optional[tuple[str, str]]:
        client = FakeProbeClient(
            connect_exc=exc if on_connect else None,
            get_exc=None if on_connect else exc,
        )
        monkeypatch.setattr(setup_mod, "_build_probe_client", lambda **_k: client)
        return await setup_mod._validate_credential(
            host="https://h", site="default", api_key="k", username=None, password=None
        )

    assert (await _run(UnifiAuthError("401")))[0] == "auth_failed"
    assert (await _run(UnifiConnectionError("x"), on_connect=True))[0] == "unreachable"
    assert (await _run(TwoFactorRequired("499"), on_connect=True))[0] == "twofactor_required"


async def test_validate_credential_success_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = FakeProbeClient()
    monkeypatch.setattr(setup_mod, "_build_probe_client", lambda **_k: probe)
    result = await setup_mod._validate_credential(
        host="https://h", site="default", api_key="k", username=None, password=None
    )
    assert result is None
    assert probe.calls == [("connect", None), ("get_json", "stat/device")]
    assert probe.closed is True


# --------------------------------------------------------------------------- #
# lock: 409 / 401 once configured
# --------------------------------------------------------------------------- #
async def test_connect_409_when_already_configured(
    configured_app: Any, secrets_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Bearer token required once configured; with it, the handler 409s (no overwrite).
    probe = FakeProbeClient()
    _install_fake_probe(monkeypatch, probe)
    async with await _client(configured_app) as c:
        resp = await c.post(
            "/api/setup/connect",
            json={"host": HOST, "api_key": API_KEY},
            headers={"Authorization": "Bearer boot-token"},
        )
    assert resp.status_code == 409
    assert resp.json()["code"] == "already_configured"
    # The existing config was never touched: no probe ran, no secrets written.
    assert probe.calls == []
    assert not secrets_path.exists()


async def test_connect_401_without_token_when_configured(configured_app: Any) -> None:
    async with await _client(configured_app) as c:
        resp = await c.post("/api/setup/connect", json={"host": HOST, "api_key": API_KEY})
    assert resp.status_code == 401


async def test_detect_locked_behind_token_once_configured(
    configured_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_detect(host: str, **_kw: Any) -> ConsoleInfo:
        return _cloudkey_info()

    monkeypatch.setattr(setup_mod, "detect_console", _fake_detect)
    async with await _client(configured_app) as c:
        no_tok = await c.post("/api/setup/detect", json={"host": HOST})
        good = await c.post(
            "/api/setup/detect",
            json={"host": HOST},
            headers={"Authorization": "Bearer boot-token"},
        )
    assert no_tok.status_code == 401
    assert good.status_code == 200


async def test_status_always_open_even_when_configured(configured_app: Any) -> None:
    # The discriminator must never require a token (the UI reads it to bootstrap).
    async with await _client(configured_app) as c:
        resp = await c.get("/api/setup/status")
    assert resp.status_code == 200
    assert resp.json()["configured"] is True
