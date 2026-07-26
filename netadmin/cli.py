"""netadmin command-line entry point.

Running ``netadmin`` with no subcommand (or ``netadmin up``) just *runs*: it
starts the daemon and opens your browser to the dashboard, printing the URL. That
is the one-command path — ``pip install unifioptimizer`` then ``netadmin``.

Subcommands:

* ``up``          - start the daemon and open the dashboard (the bare-run form).
* ``daemon``      - run the always-on collector + detection + API process
                    (the explicit, service-manager form: no browser is opened).
* ``status``      - hit a running daemon's ``/api/health`` and report it.
* ``token``       - print the configured access token (or error if none is set).
* ``visit``       - on-demand "tech visit": backfill, detect, report, exit.
* ``detect``      - identify a UniFi console (read-only) and print its API-key /
                    local-admin setup steps plus the exact ``data/secrets.env`` lines.
* ``investigate`` - build / import an LLM investigation dossier for an issue.
* ``fix``         - render (dry-run, default) or, only with ``--apply --confirm``,
                    apply a tracked issue's remediation; ``--revert`` restores a
                    change's captured before-state.
* ``demo-seed``   - generate a fictional, PII-free demo database (no controller
                    access) for screenshots and a public live demo.
* ``upgrade run`` - internal: runs the pip self-upgrade procedure. Spawned
                    detached by ``POST /api/system/update/apply``; not for a user
                    to type directly (see docs/ARCHITECTURE.md section 23).

Wired as the ``netadmin`` console script via ``[project.scripts]`` in
``pyproject.toml``.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional, Sequence

from netadmin import __version__
from netadmin.config import get_settings
from netadmin.llm.provider import PROVIDER_NAMES
from netadmin.logging import configure_logging, get_logger

log = get_logger("cli")


def _resolve_host_port(args: argparse.Namespace) -> tuple[str, int]:
    """Bind/target host+port: CLI flags win, else settings, else the 8765 default."""
    from netadmin.server.main import DEFAULT_HOST, DEFAULT_PORT

    settings = get_settings()
    host = getattr(args, "host", None) or getattr(settings, "server_host", DEFAULT_HOST)
    port = getattr(args, "port", None) or getattr(settings, "server_port", DEFAULT_PORT)
    return host, int(port)


def _dashboard_url(host: str, port: int) -> str:
    """The browser-facing URL for a given bind host (unspecified binds -> loopback)."""
    display = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    return f"http://{display}:{port}/"


def _is_headless() -> bool:
    """True when we should NOT try to open a browser.

    Honours ``NETADMIN_NO_BROWSER`` everywhere; on Linux, treats the absence of
    both ``DISPLAY`` and ``WAYLAND_DISPLAY`` as headless (a server / SSH session).
    macOS and Windows always have a windowing system available.
    """
    if os.environ.get("NETADMIN_NO_BROWSER"):
        return True
    if sys.platform.startswith("linux"):
        return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return False


def _open_browser_when_ready(host: str, port: int, url: str) -> None:
    """Wait until the daemon accepts connections, then open the browser once.

    Runs in a background thread so it can poll the port while uvicorn is starting
    in the foreground. Best-effort: a failure to open a browser is never fatal.
    """
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((probe_host, port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.25)
    else:
        log.debug("daemon did not become reachable in time; not opening a browser")
        return
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - opening a browser is a convenience, never load-bearing
        log.debug("could not open a browser", exc_info=True)


def _run_server(args: argparse.Namespace, *, open_browser: bool) -> int:
    """Run the daemon under a single-worker uvicorn (ARCHITECTURE.md 5.2, section 12).

    One process, one worker: a multi-worker scheduler double-fires (design
    verdict, section 2). The FastAPI lifespan owns collector/WS/probes/backfill
    startup and shutdown. When ``open_browser`` is set (the bare-run / ``up``
    form), a background thread opens the dashboard once the port is listening,
    unless the environment looks headless.
    """
    import uvicorn

    from netadmin.server.main import create_app

    settings = get_settings()
    host, port = _resolve_host_port(args)
    url = _dashboard_url(host, port)

    app = create_app(settings=settings)
    log.info("starting netadmin daemon on %s", url)
    # Print the URL to stdout too: the log may be routed to a file, and the whole
    # promise of the bare-run form is "it tells you where to go".
    print(f"\nUnifiOptimizer is running — open {url}\n", flush=True)

    if open_browser:
        if _is_headless():
            log.info("headless environment detected; open %s in a browser yourself", url)
        else:
            threading.Thread(
                target=_open_browser_when_ready,
                args=(host, port, url),
                name="netadmin-open-browser",
                daemon=True,
            ).start()

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        workers=1,
        lifespan="on",
        log_config=None,  # reuse netadmin's rich + rotating-file logging
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run()
    return 0


def _cmd_daemon(args: argparse.Namespace) -> int:
    """Run the always-on daemon (explicit, service-manager form; no browser)."""
    return _run_server(args, open_browser=False)


def _cmd_up(args: argparse.Namespace) -> int:
    """Start the daemon and open the dashboard (the bare-run / ``netadmin up`` form)."""
    return _run_server(args, open_browser=True)


def _cmd_status(args: argparse.Namespace) -> int:
    """Query a running daemon's ``/api/health`` and print a summary.

    Exit code: 0 when reachable and healthy, 2 when reachable but degraded/
    starting, 1 when the daemon is unreachable.
    """
    import httpx

    host, port = _resolve_host_port(args)
    url = f"http://{host}:{port}/api/health"
    try:
        resp = httpx.get(url, timeout=5.0)
    except httpx.HTTPError as exc:
        log.error("daemon unreachable at %s: %s", url, exc)
        return 1

    if resp.status_code != 200:
        log.error("health check returned HTTP %d from %s", resp.status_code, url)
        return 1

    data = resp.json()
    status = data.get("status", "UNKNOWN")
    uptime = data.get("uptime_s", "UNKNOWN")
    entities = data.get("entities", {}).get("total", "UNKNOWN")
    backfill = data.get("backfill", "UNKNOWN")
    log.info(
        "daemon status=%s uptime_s=%s entities=%s backfill=%s", status, uptime, entities, backfill
    )
    for job in data.get("jobs", []):
        log.info(
            "  job=%s status=%s last_success_age_s=%s consecutive_failures=%s",
            job.get("job"),
            job.get("status"),
            job.get("last_success_age_s"),
            job.get("consecutive_failures"),
        )
    if args.json:
        log.info(json.dumps(data, indent=2, sort_keys=True))
    return 0 if status == "ok" else 2


def _cmd_token(args: argparse.Namespace) -> int:
    """Print the configured access token, or fail clearly when none is set.

    The container-safe counterpart to the loopback bypass on ``GET
    /api/system/token`` (ARCHITECTURE.md 18.1): inside a NAT'd container the
    ASGI client is the bridge gateway, never truly loopback, so that HTTP
    recovery path never fires there. This reads ``Settings.netadmin_api_token``
    straight from ``data/secrets.env`` / the environment -- no running daemon
    required, no controller call.

    Exit codes: 0 with the token printed on stdout, 1 (a message on stderr) when
    no token is configured.
    """
    settings = get_settings()
    token = settings.api_token
    if token is None:
        print(
            "no access token configured; set NETADMIN_API_TOKEN in data/secrets.env",
            file=sys.stderr,
        )
        return 1
    print(token)
    return 0


def _cmd_visit(args: argparse.Namespace) -> int:
    """Run an on-demand tech visit (ARCHITECTURE.md section 3).

    Connect **read-only** to a controller, backfill its retained history, run
    baselines + detectors + SLE over the window, print a readable console summary,
    and (with ``--out``) write a self-contained HTML or JSON report.

    Credentials come from the configured profile (``data/secrets.env``) by default;
    ``--host`` / ``--username`` / ``--password`` / ``--api-key`` / ``--site``
    override individual fields for a controller you are visiting one-off. A visit
    NEVER mutates the controller.

    Exit codes: 0 on success, 1 when the controller is unconfigured / unreachable
    or the run fails, 2 on a usage error.
    """
    from netadmin.visit import console_summary, render_html, render_json, run_visit
    from netadmin.visit.runner import VisitStep

    settings = _visit_settings(args)
    if not settings.unifi.is_configured:
        log.error(
            "no controller credentials: pass --host with --username/--password or "
            "--api-key, or configure data/secrets.env"
        )
        return 1

    out_path: Optional[Path] = None
    if args.out:
        out_path = Path(args.out)
        fmt = out_path.suffix.lower().lstrip(".")
        if fmt not in ("html", "json"):
            log.error("--out must end in .html or .json (got %r)", args.out)
            return 2

    def _on_step(step: VisitStep) -> None:
        if step.status == "running":
            log.info("  %s…", step.label)
        elif step.status == "ok":
            log.info("  %s ✓%s", step.label, f" ({step.detail})" if step.detail else "")
        elif step.status == "failed":
            log.warning("  %s ✗ %s", step.label, step.detail or "")

    log.info("starting tech visit against %s (read-only)", settings.unifi.host)
    try:
        report = run_visit(settings, lookback_days=args.lookback_days, progress=_on_step)
    except Exception as exc:  # noqa: BLE001 - report the failure, do not traceback at the user
        log.error("visit failed: %s", exc)
        log.debug("visit failure detail", exc_info=True)
        return 1

    print(console_summary(report))

    if out_path is not None:
        content = render_json(report) if out_path.suffix.lower() == ".json" else render_html(report)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        log.info("wrote report to %s", out_path)
    return 0


def _visit_settings(args: argparse.Namespace):
    """Settings for a visit: the configured profile with any CLI credential overrides."""
    settings = get_settings()
    overrides: dict[str, object] = {}
    if args.host:
        overrides["unifi_host"] = args.host
    if args.username:
        overrides["unifi_username"] = args.username
    if args.password:
        overrides["unifi_password"] = args.password
    if args.api_key:
        overrides["unifi_api_key"] = args.api_key
    if args.site:
        overrides["unifi_site"] = args.site
        overrides["site_id"] = args.site
    return settings.model_copy(update=overrides) if overrides else settings


def _cmd_detect(args: argparse.Namespace) -> int:
    """Identify a UniFi console and print its auth-setup guide (ARCHITECTURE.md 5.1).

    Read-only and login-free: the probe never authenticates, so it needs no
    credentials and is safe against a rate-limited CloudKey. Prints the identified
    console, whether X-API-KEY auth is available (Network 9.x+), the device-specific
    steps to create/find the key (or the local-admin cookie path where API keys are
    not supported), and the exact ``data/secrets.env`` lines to add. ``--json``
    emits the machine-readable :class:`ConsoleInfo` instead.

    Exit codes: 0 when a console answered, 1 when unreachable, 2 on a usage error.
    """
    import asyncio

    from netadmin.ingest.unifi.detect import detect_console, format_console_report

    if not args.host:
        log.error("usage: netadmin detect --host HOST [--json]")
        return 2

    try:
        info = asyncio.run(detect_console(args.host))
    except ValueError as exc:
        log.error("invalid host: %s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001 - report, do not traceback at the user
        log.error("detection failed: %s", exc)
        log.debug("detect failure detail", exc_info=True)
        return 1

    if args.json:
        print(json.dumps(info.as_dict(), indent=2, sort_keys=True))
    else:
        print(format_console_report(info, args.host))
    return 0 if info.reachable else 1


def _cmd_investigate(args: argparse.Namespace) -> int:
    """Build or import an LLM investigation dossier (ARCHITECTURE.md section 10).

    Two forms:

    * ``netadmin investigate <issue-id> [--provider ...]`` — compile the dossier
      and run the chosen provider. ``manual`` (default) writes a file and leaves
      the investigation *pending*; ``copilot`` / ``anthropic`` return an answer.
    * ``netadmin investigate import <file> --issue <id>`` — attach a completed
      response to the issue's pending investigation.

    Exit codes: 0 on success, 1 on a runtime/provider failure or unknown issue,
    2 on a usage error.
    """
    from netadmin.issues.engine import IssueEngine
    from netadmin.issues.store_repository import StoreIssueRepository
    from netadmin.llm import import_response as attach_response
    from netadmin.llm import run_investigation
    from netadmin.llm.provider import ProviderError, ProviderUnavailableError
    from netadmin.store.repository import Repository

    settings = get_settings()
    store = Repository.open(settings.db_path, site_id=settings.site_id)
    try:
        engine = IssueEngine(StoreIssueRepository(store))

        if args.target == "import":
            if not args.import_file or args.issue is None:
                log.error("usage: netadmin investigate import <file> --issue <id>")
                return 2
            try:
                text = Path(args.import_file).read_text(encoding="utf-8")
            except OSError as exc:
                log.error("cannot read %s: %s", args.import_file, exc)
                return 1
            try:
                outcome = attach_response(store, engine, args.issue, text)
            except KeyError:
                log.error("issue %d not found", args.issue)
                return 1
            log.info(
                "attached response to issue %d (investigation %d, %s)",
                outcome.issue_id,
                outcome.investigation_id,
                outcome.status,
            )
            return 0

        try:
            issue_id = int(args.target)
        except ValueError:
            log.error("invalid issue id %r (expected an integer or 'import')", args.target)
            return 2

        try:
            outcome = run_investigation(store, engine, issue_id, args.provider)
        except KeyError:
            log.error("issue %d not found", issue_id)
            return 1
        except ProviderUnavailableError as exc:
            log.error("provider %r unavailable: %s", args.provider, exc)
            return 1
        except ProviderError as exc:
            log.error("investigation failed: %s", exc)
            return 1

        if outcome.status == "answered":
            log.info("investigation %d answered by %s", outcome.investigation_id, outcome.provider)
            print(outcome.response_md)
        else:
            log.info(
                "dossier written for issue %d (investigation %d, pending)",
                issue_id,
                outcome.investigation_id,
            )
            if outcome.dossier_path:
                log.info("  dossier: %s", outcome.dossier_path)
                log.info(
                    "  run it through any model, then: "
                    "netadmin investigate import <file> --issue %d",
                    issue_id,
                )
        return 0
    finally:
        store.close()


def _cmd_fix(args: argparse.Namespace) -> int:
    """Render or apply a tracked issue's fix (ARCHITECTURE.md section 9).

    Dry-run is the default and prints the exact controller payloads plus a confirm
    token, touching the controller only with a read-only ``stat/device`` GET. A
    real apply happens ONLY with ``--apply --confirm`` (an explicit human action);
    it re-reads the device, re-checks the confirm token, every precondition, and
    the min-RSSI rail before sending, records the before-state to the changes
    ledger, and arms the section-7 verification window. ``--revert <change-id>``
    restores a change's stored before-state.

    Exit codes: 0 on success, 1 on a runtime/refusal/unreachable failure, 2 on a
    usage error (e.g. ``--apply`` without ``--confirm``).
    """
    import asyncio

    return asyncio.run(_fix_async(args))


async def _fix_async(args: argparse.Namespace) -> int:
    from netadmin.fixes.models import (
        ConfirmTokenError,
        FixError,
        MaxStepsExceeded,
        PreconditionDrift,
        SafetyViolation,
        WriterRequired,
    )
    from netadmin.fixes.service import FixService, IssueNotFound, build_fix_seams
    from netadmin.issues.engine import IssueEngine
    from netadmin.issues.store_repository import StoreIssueRepository
    from netadmin.store.repository import Repository

    if args.apply and getattr(args, "dry_run", False):
        log.error("--dry-run and --apply are mutually exclusive")
        return 2
    if args.apply and not args.confirm:
        log.error("refusing to apply without --confirm (dry-run is the default)")
        return 2
    if args.revert is None and args.issue_id is None:
        log.error("usage: netadmin fix <issue-id> [--apply --confirm] | fix --revert <change-id>")
        return 2

    settings = get_settings()
    for_apply = bool(args.apply) or args.revert is not None
    store = Repository.open(settings.db_path, site_id=settings.site_id)
    seams = None
    try:
        engine = IssueEngine(StoreIssueRepository(store))
        try:
            seams = build_fix_seams(settings, for_apply=for_apply)
        except RuntimeError as exc:
            log.error("controller not configured: %s", exc)
            return 1
        service = FixService(
            store,
            engine,
            device_reader=seams.reader,
            writer=seams.writer if for_apply else None,
        )

        if args.revert is not None:
            try:
                result = await service.revert(args.revert)
            except FixError as exc:
                log.error("revert failed: %s", exc)
                return 1
            if result.ok:
                log.info("reverted change %d (restored before-state)", args.revert)
                return 0
            log.error(
                "revert of change %d did not succeed (status=%s)", args.revert, result.status_code
            )
            return 1

        try:
            dry = await service.dry_run(args.issue_id)
        except IssueNotFound:
            log.error("issue %d not found", args.issue_id)
            return 1
        except FixError as exc:
            log.error("cannot plan a fix for issue %d: %s", args.issue_id, exc)
            return 1

        _print_fix_plan(args.issue_id, dry)

        if not args.apply:
            return 0
        if dry.manual_action_required:
            log.error(
                "issue %d has no automatic fix (manual action required); nothing to apply",
                args.issue_id,
            )
            return 1

        token = args.confirm_token or dry.confirm_token
        try:
            result = await service.apply(args.issue_id, confirm_token=token)
        except ConfirmTokenError:
            log.error("plan changed since it was rendered; re-run the dry-run and confirm again")
            return 1
        except PreconditionDrift as exc:
            log.error("aborted — live state drifted from the plan: %s", exc)
            return 1
        except (SafetyViolation, MaxStepsExceeded) as exc:
            log.error("refused: %s", exc)
            return 1
        except WriterRequired as exc:
            log.error("%s", exc)
            return 1
        except FixError as exc:
            log.error("apply failed: %s", exc)
            return 1

        _print_apply_result(result)
        return 0 if result.applied else 1
    finally:
        if seams is not None and seams.closer is not None:
            try:
                await seams.closer()
            except Exception:  # noqa: BLE001 - teardown must not mask the outcome
                log.debug("error closing controller seams", exc_info=True)
        store.close()


def _print_fix_plan(issue_id: int, dry: "object") -> None:
    """Print a dry-run plan (advisory note, or the exact per-step payloads)."""
    import json as _json

    from netadmin.fixes.models import DryRunResult

    assert isinstance(dry, DryRunResult)
    print(f"\nFix plan for issue {issue_id}: {dry.plan.title}")
    if dry.manual_action_required:
        print("  Manual action required — no safe automatic fix:")
        print(f"    {dry.advisory}")
        return
    for i, step in enumerate(dry.rendered, start=1):
        print(f"  Step {i}: {step['description']} [risk={step['risk']}]")
        print(f"    {step['method']} {step['endpoint']}")
        print(f"    payload: {_json.dumps(step['payload'], sort_keys=True)}")
        print(f"    revertible: {step['revertible']}")
    print(f"\n  confirm_token: {dry.confirm_token}")
    print(f"  To apply:  netadmin fix {issue_id} --apply --confirm")


def _print_apply_result(result: "object") -> None:
    from netadmin.fixes.models import ApplyResult

    assert isinstance(result, ApplyResult)
    if result.applied:
        log.info("applied fix (changes %s); verification window armed", result.change_ids)
    else:
        log.warning("apply did not complete: %s", result.aborted_reason)
    for sr in result.steps:
        log.info(
            "  step %s: %s%s", sr.step.action.value, sr.status, f" ({sr.error})" if sr.error else ""
        )


def _cmd_upgrade_run(args: argparse.Namespace) -> int:
    """Run the pip self-upgrade procedure (ARCHITECTURE.md section 23).

    Internal: this is spawned detached by ``POST /api/system/update/apply``, never
    meant to be typed by a user. It requires a journal already primed by that
    handler (matching ``--target``, in the "starting" phase, carrying the live
    daemon's own pid/argv/cwd/env) -- there is no way for a bare CLI invocation to
    know how to stop and restart a daemon it was not told about.

    Exit codes: 0 on a completed upgrade, 1 on any failure (the journal already
    records whether it was auto-rolled-back or left as a clean, untouched failure).
    """
    from netadmin.upgrade.runner import RunnerError, run_upgrade

    settings = get_settings()
    try:
        run_upgrade(args.target, settings=settings)
    except RunnerError as exc:
        log.error("self-upgrade to %s failed: %s", args.target, exc)
        return 1
    except Exception as exc:  # noqa: BLE001 - report, do not traceback at the user
        log.error("self-upgrade to %s crashed: %s", args.target, exc)
        log.debug("self-upgrade failure detail", exc_info=True)
        return 1
    log.info("self-upgrade to %s completed", args.target)
    return 0


def _cmd_demo_seed(args: argparse.Namespace) -> int:
    """Generate a fictional, PII-free demo database (ARCHITECTURE.md sections 4, 6-8).

    Pure data generation: no controller, no network, no MQTT. Deterministic given
    ``--seed`` / ``--now``; ``--now`` defaults to a fixed baseline so a regenerated
    demo is stable (pass the current epoch for a "live" demo whose timestamps read
    as current). Refuses to write to the production db name so it can never clobber
    a real install.

    Exit codes: 0 on success, 2 on a refusal (protected path / misaligned ``--now``).
    """
    from netadmin.demo.seed import DEFAULT_HISTORY_DAYS, DEFAULT_NOW, DEMO_SEED, seed_demo

    now = args.now if args.now is not None else DEFAULT_NOW
    seed = args.seed if args.seed is not None else DEMO_SEED
    history_days = args.history_days if args.history_days is not None else DEFAULT_HISTORY_DAYS

    try:
        stats = seed_demo(args.out, now=now, seed=seed, history_days=history_days)
    except ValueError as exc:
        log.error("demo-seed refused: %s", exc)
        return 2

    data = stats.as_dict()
    log.info("wrote demo database to %s", data["db_path"])
    log.info(
        "  entities=%d series=%d samples=%d events=%d poll_runs=%d",
        data["entities"]["total"],
        data["series"],
        data["samples"],
        data["events"],
        data["poll_runs"],
    )
    log.info(
        "  issues=%d by_state=%s by_severity=%s",
        data["issues"]["total"],
        data["issues"]["by_state"],
        data["issues"]["by_severity"],
    )
    headline = data["sle_headline"]
    log.info(
        "  sle_minutes=%d baselines=%d changes=%d investigations=%d headline=%s",
        data["sle_minutes"],
        data["baselines"],
        data["changes"],
        data["investigations"],
        f"{headline * 100:.1f}%" if headline is not None else "n/a",
    )
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    return 0


def _add_host_port(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--host", default=None, help="bind/target host (default: settings or 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=None, help="bind/target port (default: settings or 8765)"
    )


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser and its subcommands."""
    parser = argparse.ArgumentParser(
        prog="netadmin",
        description="UnifiOptimizer rebuild: a network admin that remembers.",
    )
    parser.add_argument("--version", action="version", version=f"netadmin {__version__}")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="enable DEBUG-level logging",
    )

    # Not required: a bare ``netadmin`` (no subcommand) runs the ``up`` form.
    sub = parser.add_subparsers(dest="command", required=False, metavar="<command>")

    p_up = sub.add_parser(
        "up",
        help="start the daemon and open the dashboard (same as bare `netadmin`)",
    )
    _add_host_port(p_up)
    p_up.set_defaults(func=_cmd_up)

    p_daemon = sub.add_parser(
        "daemon",
        help="run the always-on daemon (service form: no browser is opened)",
    )
    _add_host_port(p_daemon)
    p_daemon.set_defaults(func=_cmd_daemon)

    p_status = sub.add_parser("status", help="report a running daemon's health")
    _add_host_port(p_status)
    p_status.add_argument("--json", action="store_true", help="also emit the raw health JSON")
    p_status.set_defaults(func=_cmd_status)

    p_token = sub.add_parser(
        "token",
        help="print the configured access token (or error if none is set)",
    )
    p_token.set_defaults(func=_cmd_token)

    p_visit = sub.add_parser("visit", help="run an on-demand tech visit")
    p_visit.add_argument("--host", default=None, help="controller host (overrides the profile)")
    p_visit.add_argument("--username", default=None, help="controller username")
    p_visit.add_argument("--password", default=None, help="controller password")
    p_visit.add_argument("--api-key", dest="api_key", default=None, help="controller X-API-KEY")
    p_visit.add_argument("--site", default=None, help="controller site id (default: 'default')")
    p_visit.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        help="history window to analyze (default: controller retention)",
    )
    p_visit.add_argument(
        "--out",
        default=None,
        help="write a self-contained report to this path (.html or .json)",
    )
    p_visit.set_defaults(func=_cmd_visit)

    p_detect = sub.add_parser(
        "detect",
        help="identify a UniFi console (read-only) and print its API-key/auth setup",
    )
    p_detect.add_argument(
        "--host",
        required=True,
        help="controller host, e.g. https://192.168.1.1 (UniFi OS) or 192.168.1.10:8443 (legacy)",
    )
    p_detect.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of the setup guide",
    )
    p_detect.set_defaults(func=_cmd_detect)

    p_investigate = sub.add_parser(
        "investigate",
        help="build or import an LLM investigation dossier for an issue",
    )
    p_investigate.add_argument(
        "target",
        help="issue id to investigate, or the literal 'import' to attach a response",
    )
    p_investigate.add_argument(
        "import_file",
        nargs="?",
        default=None,
        help="with 'import': path to the completed response markdown file",
    )
    p_investigate.add_argument(
        "--issue",
        type=int,
        default=None,
        help="with 'import': the issue id the response belongs to",
    )
    p_investigate.add_argument(
        "--provider",
        choices=list(PROVIDER_NAMES),
        default="manual",
        help="investigator provider (default: manual)",
    )
    p_investigate.set_defaults(func=_cmd_investigate)

    p_fix = sub.add_parser(
        "fix",
        help="render (dry-run) or apply a tracked issue's remediation",
    )
    p_fix.add_argument(
        "issue_id",
        type=int,
        nargs="?",
        help="the issue id to plan/apply a fix for (omit only with --revert)",
    )
    p_fix.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="render the exact payload without sending it (the default)",
    )
    p_fix.add_argument(
        "--apply",
        action="store_true",
        help="apply the fix (requires --confirm); default is a dry-run render",
    )
    p_fix.add_argument(
        "--confirm",
        action="store_true",
        help="explicit confirmation required alongside --apply",
    )
    p_fix.add_argument(
        "--confirm-token",
        dest="confirm_token",
        default=None,
        help="bind the apply to a specific reviewed plan's token (optional)",
    )
    p_fix.add_argument(
        "--revert",
        type=int,
        default=None,
        metavar="CHANGE_ID",
        help="revert a changes-ledger row, restoring its captured before-state",
    )
    p_fix.set_defaults(func=_cmd_fix)

    p_upgrade = sub.add_parser(
        "upgrade",
        help="self-upgrade internals (spawned by POST /api/system/update/apply)",
    )
    upgrade_sub = p_upgrade.add_subparsers(
        dest="upgrade_command", required=True, metavar="<upgrade-command>"
    )
    p_upgrade_run = upgrade_sub.add_parser(
        "run",
        help="run the pip self-upgrade procedure (internal; not for direct use)",
    )
    p_upgrade_run.add_argument(
        "--target",
        required=True,
        help="target version to upgrade to (must match the primed journal)",
    )
    p_upgrade_run.set_defaults(func=_cmd_upgrade_run)

    p_demo = sub.add_parser(
        "demo-seed",
        help="generate a fictional, PII-free demo database (no controller access)",
    )
    p_demo.add_argument(
        "--out",
        default="data/netadmin-demo.db",
        help="output database path (default: data/netadmin-demo.db)",
    )
    p_demo.add_argument(
        "--now",
        type=int,
        default=None,
        help="baseline epoch seconds (default: a fixed stable baseline; pass the "
        "current epoch for a 'live' demo). Must be aligned to 300 s.",
    )
    p_demo.add_argument(
        "--seed",
        type=int,
        default=None,
        help="RNG seed (default: a fixed constant, for deterministic output)",
    )
    p_demo.add_argument(
        "--history-days",
        dest="history_days",
        type=int,
        default=None,
        help="days of history to synthesise (default: 6)",
    )
    p_demo.add_argument("--json", action="store_true", help="also print the full stats JSON")
    p_demo.set_defaults(func=_cmd_demo_seed)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Program entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    configure_logging(level="DEBUG" if getattr(args, "verbose", False) else "INFO")

    # Bare `netadmin` (no subcommand) is the one-command run: start + open browser.
    if getattr(args, "func", None) is None:
        return _cmd_up(args)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
