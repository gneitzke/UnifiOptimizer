"""FastAPI application factory and daemon lifespan (ARCHITECTURE.md 5.2 & 12).

One process runs everything (section 3). :func:`create_app` builds the app; its
lifespan is the daemon's startup path: open the store, build the issue engine,
start the collector scheduler, the WebSocket supervisor, and the active probes,
kick off a startup backfill, then tear all of it down cleanly on shutdown.

The ingest subsystems (collector, WS supervisor, probes, backfill) are built by
peers in parallel. They are imported **lazily and defensively** here, against the
names in the architecture's repo layout (section 14); when a module is not yet
present the daemon still boots and ``/api/health`` honestly reports the subsystem
as unavailable rather than crashing. The integration pass reconciles the exact
constructor signatures. Tests inject a :class:`DaemonComponents` bundle (e.g. a
fake scheduler) to drive the lifespan without a live controller.

CORS is pinned to the configured origins (section 12); ``*`` is never allowed.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from netadmin import __version__
from netadmin.config import SECRETS_ENV, Settings, get_settings
from netadmin.issues.engine import IssueEngine
from netadmin.issues.store_repository import StoreIssueRepository
from netadmin.logging import get_logger
from netadmin.server.auth import ApiTokenAuthMiddleware
from netadmin.server.routers import changes as changes_router
from netadmin.server.routers import events as events_router
from netadmin.server.routers import fixes as fixes_router
from netadmin.server.routers import incidents as incidents_router
from netadmin.server.routers import inventory as inventory_router
from netadmin.server.routers import issues as issues_router
from netadmin.server.routers import metrics as metrics_router
from netadmin.server.routers import ondemand as ondemand_router
from netadmin.server.routers import report as report_router
from netadmin.server.routers import setup as setup_router
from netadmin.server.routers import sle as sle_router
from netadmin.server.routers import system as system_router
from netadmin.server.runtime import DaemonState, build_health, maybe_await
from netadmin.server.ws import WsBroadcaster, websocket_endpoint
from netadmin.store.repository import Repository

_log = get_logger("server.main")

DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"
_DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    f"http://localhost:{DEFAULT_PORT}",
    f"http://127.0.0.1:{DEFAULT_PORT}",
)


@dataclass
class DaemonComponents:
    """The four runtime subsystems the lifespan starts and stops.

    Any field left ``None`` is simply not started. Tests pass a bundle with a
    fake scheduler; production leaves this ``None`` on the app so the lifespan
    builds the real subsystems lazily via :func:`build_default_components`.
    """

    scheduler: Any = None  # APScheduler-style: .start() / .shutdown(wait=False)
    ws_supervisor: Any = None  # async .start() / .stop(); optional .state
    probes: Any = None  # async .start() / .stop()
    backfill: Optional[Callable[[], Awaitable[Any]]] = None  # awaited once at startup


def _cors_origins(settings: Settings) -> list[str]:
    """Configured CORS origins, sanitised so ``*`` never slips through."""
    raw = getattr(settings, "cors_origins", None)
    origins = list(raw) if raw else list(_DEFAULT_CORS_ORIGINS)
    clean = [o for o in origins if o and o != "*"]
    if len(clean) != len(origins):
        _log.warning("dropped wildcard/empty CORS origin; CORS must be pinned (section 12)")
    return clean or list(_DEFAULT_CORS_ORIGINS)


def build_default_components(
    settings: Settings, store: Repository, engine: IssueEngine, state: DaemonState
) -> DaemonComponents:
    """Construct the real ingest subsystems, tolerating an unconfigured controller.

    The whole ingest stack shares one authenticated controller session, so it is
    assembled together by :func:`netadmin.ingest.factory.build_components`. When
    the controller is not configured (no credentials) that raises, and every
    subsystem is recorded unavailable on ``state`` -- surfaced at ``/api/health``
    -- rather than crashing the daemon. On success the collector's live status is
    published to ``state`` so health reports each poll job by its real id.
    """
    try:
        from netadmin.ingest.factory import build_components

        built = build_components(settings, store, issue_engine=engine)
    except Exception as exc:  # noqa: BLE001 - unconfigured/unbuildable ingest is not fatal
        for name in ("scheduler", "ws_supervisor", "probes", "backfill"):
            state.mark_unavailable(name, exc)
        return DaemonComponents()

    state.collector_status = built.collector.status
    return DaemonComponents(
        scheduler=built.scheduler,
        ws_supervisor=built.ws_supervisor,
        probes=built.probes,
        backfill=built.backfill,
    )


async def _start_component(name: str, ref: Any, state: DaemonState) -> Any:
    """Start one subsystem, tolerating a start failure without killing the daemon."""
    if ref is None:
        return None
    try:
        starter = getattr(ref, "start", None)
        if starter is not None:
            await maybe_await(starter())
        return ref
    except Exception as exc:  # noqa: BLE001 - a broken subsystem must not down the API
        state.mark_unavailable(name, exc)
        return None


async def _stop_component(name: str, ref: Any, *, scheduler: bool = False) -> None:
    """Stop one subsystem, swallowing shutdown errors (we are already tearing down)."""
    if ref is None:
        return
    try:
        if scheduler:
            ref.shutdown(wait=False)
        else:
            stopper = getattr(ref, "stop", None)
            if stopper is not None:
                await maybe_await(stopper())
    except Exception:  # noqa: BLE001 - best-effort cleanup
        _log.warning("error stopping %s during shutdown", name, exc_info=True)


async def _run_backfill(backfill: Callable[[], Awaitable[Any]], state: DaemonState) -> None:
    """Run the startup backfill as a background task, recording its progress.

    Backfill can take a while (chunked ``stat/report`` pulls); running it as a
    task lets the API become ready immediately while ``/api/health`` shows the
    backfill still in flight.
    """
    state.backfill_status = "running"
    try:
        await backfill()
        state.backfill_status = "done"
    except asyncio.CancelledError:
        state.backfill_status = "cancelled"
        raise
    except Exception:  # noqa: BLE001
        state.backfill_status = "failed"
        _log.exception("startup backfill failed")


_INGEST_SUBSYSTEMS = ("scheduler", "ws_supervisor", "probes", "backfill")


async def start_ingest(app: FastAPI, *, rebuild: bool = False) -> None:
    """Build (if needed) and start the ingest subsystems in the running process.

    The single ingest bring-up path (ARCHITECTURE.md 18): the lifespan calls it
    once at boot, and the first-run ``POST /api/setup/connect`` calls it again —
    with ``rebuild=True`` — the moment credentials are written, hot-starting the
    collector / WS supervisor / probes / backfill **without a restart**.

    Reads everything off ``app.state`` (settings/store/issue_engine/daemon) so a
    hot-start picks up the freshly-written credentials the connect handler just
    applied to ``app.state.settings``. ``rebuild`` drops any previously-built
    components and clears the four subsystems' ``unavailable`` markers so
    :func:`build_default_components` reconstructs them from the *current* settings
    (at boot the controller was unconfigured, so nothing was built). Each start
    failure is recorded on the daemon state, never fatal.
    """
    settings: Settings = app.state.settings
    state: DaemonState = app.state.daemon
    store: Repository = app.state.store
    engine: IssueEngine = app.state.issue_engine

    if rebuild:
        app.state.components = None
        for name in _INGEST_SUBSYSTEMS:
            state.unavailable.pop(name, None)

    comps: Optional[DaemonComponents] = app.state.components
    if comps is None:
        comps = build_default_components(settings, store, engine, state)
        app.state.components = comps

    # Start subsystems; each failure is recorded, not fatal.
    state.scheduler = await _start_component("scheduler", comps.scheduler, state)
    state.ws_supervisor = await _start_component("ws_supervisor", comps.ws_supervisor, state)
    state.probes = await _start_component("probes", comps.probes, state)

    if comps.backfill is not None:
        state.backfill_task = asyncio.create_task(_run_backfill(comps.backfill, state))
    else:
        state.backfill_status = "absent"


def _heartbeat_payload(store: Repository, state: DaemonState, settings: Settings) -> dict[str, Any]:
    """The slim poll-age view the WebSocket heartbeat carries every 30 s.

    A projection of the same honest :func:`build_health` document — overall
    status, readiness, and each job's status + last-success age — so the UI can
    show "collector last ran 12 s ago" and grey out when the daemon goes quiet,
    without a second poll.
    """
    health = build_health(store, state, settings)
    return {
        "ts": health["now"],
        "ready": health["ready"],
        "status": health["status"],
        "jobs": [
            {
                "job": j["job"],
                "status": j["status"],
                "last_success_age_s": j["last_success_age_s"],
            }
            for j in health["jobs"]
        ],
    }


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    state: DaemonState = app.state.daemon

    # Auth posture, logged once at startup (section 12). An open API on a LAN-bound
    # daemon is a deliberate-but-loud default, never a silent one. Controller
    # mutations (fix apply/revert) fail closed either way: without a token they are
    # refused outright, so an open API is read-only over HTTP.
    if settings.api_token:
        _log.info("API authentication enabled (static bearer token)")
    else:
        _log.warning(
            "API is unauthenticated — set NETADMIN_API_TOKEN. "
            "Fix apply/revert over HTTP are DISABLED until a token is configured."
        )

    owns_store = app.state.store is None
    if owns_store:
        app.state.store = Repository.open(settings.db_path, site_id=settings.site_id)
    store: Repository = app.state.store

    if app.state.issue_engine is None:
        app.state.issue_engine = IssueEngine(StoreIssueRepository(store))
    engine: IssueEngine = app.state.issue_engine

    # Wire the WebSocket broadcaster to the engine's transition stream (section 7)
    # and give its heartbeat a live view of poll ages. Registering the callback
    # here — at daemon startup, on the shared engine the detection passes drive —
    # is what turns every lifecycle change into a ``/ws`` push with no polling.
    broadcaster: WsBroadcaster = app.state.ws_broadcaster
    # Remove-then-add: the engine may be injected and outlive one lifespan (tests,
    # tech-visit mode), and add_callback appends unconditionally, so a second
    # lifespan on the same engine would push every transition to ``/ws`` twice.
    engine.remove_callback(broadcaster.on_transition)
    engine.add_callback(broadcaster.on_transition)
    broadcaster.set_heartbeat_provider(lambda: _heartbeat_payload(store, state, settings))
    await broadcaster.start_heartbeat()

    # Home Assistant MQTT bridge (section 11). A total no-op unless ha.enabled and a
    # broker host are configured; it registers its own engine callback and runs all
    # MQTT I/O in an isolated, reconnecting task, so a broker fault never touches the
    # daemon. Built and started defensively — a construction fault is recorded, not
    # fatal (a health surface that says "HA down" beats a daemon that will not boot).
    try:
        from netadmin.integrations.home_assistant import build_ha_publisher

        app.state.ha_publisher = build_ha_publisher(settings, store, engine)
        await app.state.ha_publisher.start()
    except Exception as exc:  # noqa: BLE001 - the HA bridge is never load-bearing
        app.state.ha_publisher = None
        state.mark_unavailable("ha_publisher", exc)

    # Outbound alert channels (section 20). Same contract as the HA bridge: a total
    # no-op unless ``alerts.enabled`` and at least one channel has a URL in
    # data/secrets.env, its own engine callback, all HTTP in isolated per-channel
    # worker tasks. A construction fault is recorded, never fatal.
    try:
        from netadmin.integrations.alerts import build_alert_dispatcher

        app.state.alerts = build_alert_dispatcher(settings, engine)
        # Publish to DaemonState *before* start(): start() registers the engine
        # callback and flips _running early, so a failure part-way through would
        # otherwise leave a live callback feeding a queue nothing ever drains,
        # with teardown skipping stop() because state.alerts was still None.
        state.alerts = app.state.alerts
        await app.state.alerts.start()
    except Exception as exc:  # noqa: BLE001 - a broken webhook must not down the daemon
        app.state.alerts = None
        state.alerts = None
        state.mark_unavailable("alerts", exc)

    # Auto-investigation of newly-confirmed issues (section 21). Same contract as
    # the two integrations above: off by default, its own engine callback, all
    # provider work in an isolated worker task so a slow model call can never touch
    # the detect cycle. A construction fault is recorded, never fatal.
    try:
        from netadmin.llm.auto import build_auto_investigator

        app.state.auto_investigator = build_auto_investigator(settings, store, engine)
        # Published before start() for the same reason as the dispatcher above.
        state.auto_investigator = app.state.auto_investigator
        await app.state.auto_investigator.start()
    except Exception as exc:  # noqa: BLE001 - investigation is never load-bearing
        app.state.auto_investigator = None
        state.auto_investigator = None
        state.mark_unavailable("auto_investigator", exc)

    # Self-update version check (section 23 -- foundation only: this phase ships
    # the background PyPI check and its cache, no API route or UI banner yet).
    # Same contract as the integrations above: off only when configured off
    # (``updates.check=false``), all network I/O in its own background task, so
    # PyPI being slow or unreachable can never touch the detect cycle or
    # startup. A construction fault is recorded, never fatal.
    try:
        from netadmin.upgrade.checker import build_version_checker

        app.state.version_checker = build_version_checker(settings, store)
        await app.state.version_checker.start()
    except Exception as exc:  # noqa: BLE001 - the update checker is never load-bearing
        app.state.version_checker = None
        state.mark_unavailable("version_checker", exc)

    await start_ingest(app)

    state.ready = True
    _log.info("netadmin daemon ready (uptime clock started)")
    try:
        yield
    finally:
        state.ready = False
        # Auto-investigation stops first: it is the only consumer that can be
        # holding a long provider call, and nothing downstream depends on it.
        auto_investigator = getattr(app.state, "auto_investigator", None)
        if auto_investigator is not None:
            await _stop_component("auto_investigator", auto_investigator)
        # Alerts stop first: draining a queued "resolved" is worth the grace window,
        # and every later teardown step can only produce noise nobody wants paged on.
        alerts = getattr(app.state, "alerts", None)
        if alerts is not None:
            await _stop_component("alerts", alerts)
        ha_publisher = getattr(app.state, "ha_publisher", None)
        if ha_publisher is not None:
            await _stop_component("ha_publisher", ha_publisher)
        version_checker = getattr(app.state, "version_checker", None)
        if version_checker is not None:
            await _stop_component("version_checker", version_checker)
        engine.remove_callback(broadcaster.on_transition)
        await broadcaster.stop()
        if state.backfill_task is not None and not state.backfill_task.done():
            state.backfill_task.cancel()
            try:
                await state.backfill_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        # Reverse start order for a clean shutdown.
        await _stop_component("probes", state.probes)
        await _stop_component("ws_supervisor", state.ws_supervisor)
        await _stop_component("scheduler", state.scheduler, scheduler=True)
        if owns_store:
            store.close()
        _log.info("netadmin daemon stopped")


def create_app(
    settings: Optional[Settings] = None,
    *,
    store: Optional[Repository] = None,
    issue_engine: Optional[IssueEngine] = None,
    components: Optional[DaemonComponents] = None,
) -> FastAPI:
    """Build the netadmin FastAPI app.

    ``store`` / ``issue_engine`` may be injected (router tests, tech-visit mode);
    when omitted the lifespan opens them from ``settings``. ``components`` injects
    the runtime subsystems for lifespan tests; when omitted the lifespan builds
    the real ones lazily. Passing an empty ``DaemonComponents()`` runs a lifespan
    that starts nothing -- useful for exercising the app without any ingest.
    """
    settings = settings or get_settings()

    app = FastAPI(
        title="netadmin",
        version=__version__,
        summary="UnifiOptimizer rebuild: a network admin that remembers.",
        lifespan=_lifespan,
    )

    app.state.settings = settings
    app.state.store = store
    app.state.issue_engine = issue_engine
    app.state.components = components
    # Where first-run setup writes the controller credential + minted token
    # (ARCHITECTURE.md 18). A settable seam so tests target a temp file, never the
    # real data/secrets.env; production uses the gitignored default.
    app.state.secrets_path = SECRETS_ENV
    app.state.daemon = DaemonState(started_ts=int(time.time()))
    # The one real WebSocket's fan-out hub. Created here so ``/ws`` can find it
    # even before the lifespan runs; the lifespan registers it on the engine and
    # starts its heartbeat.
    app.state.ws_broadcaster = WsBroadcaster()
    # The Home Assistant MQTT bridge is built in the lifespan (section 11); declared
    # here so ``app.state.ha_publisher`` exists even for a lifespan that never ran.
    app.state.ha_publisher = None
    # The outbound alert dispatcher is built in the lifespan (section 20); declared
    # here so ``app.state.alerts`` exists even for a lifespan that never ran.
    app.state.alerts = None
    # The auto-investigator is built in the lifespan (section 21); declared here so
    # ``app.state.auto_investigator`` exists even for a lifespan that never ran.
    app.state.auto_investigator = None
    # The self-update version checker is built in the lifespan (section 23);
    # declared here so ``app.state.version_checker`` exists even for a lifespan
    # that never ran.
    app.state.version_checker = None
    # Fix-engine controller seams. ``None`` means the fixes router builds a
    # read-only reader (and, only for a confirmed apply, a writer) per-request from
    # the configured credentials. Tests inject a ``FixSeams`` with fakes here so the
    # whole fix lifecycle runs offline and no apply path can reach a live controller.
    app.state.fix_seams = None
    # Self-update apply seam (ARCHITECTURE.md 23). ``None`` means the system router
    # spawns the real detached ``netadmin upgrade run`` subprocess. Tests inject a
    # recorder here so POST /api/system/update/apply never launches a real process.
    app.state.upgrade_spawner = None

    # Auth is added BEFORE CORS so CORS ends up the outermost layer (Starlette runs
    # the last-added middleware first): a 401 from the auth gate still passes back
    # through CORS and carries its headers, so the browser can read the 401 and show
    # the token screen instead of a bare network error. A falsy token makes the
    # middleware a pass-through (open access; the lifespan logs the warning).
    #
    # The token and setup-configured state are read LIVE off ``app.state.settings``
    # (ARCHITECTURE.md 18): first-run connect writes credentials + mints a token and
    # updates that settings object in place, so the API locks with the new token and
    # ``/api/setup/*`` closes -- in the running process, with no restart.
    app.add_middleware(
        ApiTokenAuthMiddleware,
        token_provider=lambda: app.state.settings.api_token,
        configured_provider=lambda: setup_router.is_configured(app.state.settings),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(settings),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    app.include_router(setup_router.router)
    app.include_router(system_router.router)
    app.include_router(issues_router.router)
    app.include_router(incidents_router.router)
    app.include_router(sle_router.router)
    app.include_router(inventory_router.router)
    app.include_router(inventory_router.offenders_router)
    app.include_router(metrics_router.router)
    app.include_router(report_router.router)
    app.include_router(events_router.router)
    app.include_router(changes_router.router)
    app.include_router(fixes_router.router)
    app.include_router(ondemand_router.router)
    app.add_api_websocket_route("/ws", websocket_endpoint)
    _mount_spa(app, settings)
    return app


def _resolve_web_dir(settings: Settings) -> Path:
    """Locate the built web UI to serve, preferring the bundled copy.

    Resolution order (first hit wins):

    1. ``settings.web_dist_path`` when explicitly set — a dev/test override.
    2. The **bundled** UI shipped inside the wheel at ``netadmin/_webui/`` —
       this is what makes ``pip install`` give a working dashboard with no Node
       (the packaging point; produced by ``tools/build_web.py``).
    3. ``web/dist`` relative to the cwd — a source checkout during development,
       where the UI is built into ``web/dist`` but not bundled into the package.
    """
    override = getattr(settings, "web_dist_path", None)
    if override:
        return Path(override).resolve()
    bundled = Path(__file__).resolve().parent.parent / "_webui"
    if (bundled / "index.html").is_file():
        return bundled
    return Path("web/dist").resolve()


def _mount_spa(app: FastAPI, settings: Settings) -> None:
    """Serve the built web UI when present (bundled UI, else web/dist for dev).

    Mounted last so /api/* routes and /ws always win. StaticFiles(html=True)
    serves index.html at "/" but 404s deep SPA links (/issues/42), so a
    catch-all fallback returns index.html for any non-API GET instead.
    """
    dist = _resolve_web_dir(settings)
    index = dist / "index.html"
    if not index.is_file():
        _log.warning(
            "web UI not served: %s missing. Install the wheel (ships a bundled UI) "
            "or build the dev UI with `python tools/build_web.py` / `npm run build`.",
            index,
        )
        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:  # pragma: no cover - trivial IO
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist):
            return FileResponse(candidate)
        return FileResponse(index)


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DaemonComponents",
    "build_default_components",
    "start_ingest",
    "create_app",
    "_resolve_web_dir",
]
