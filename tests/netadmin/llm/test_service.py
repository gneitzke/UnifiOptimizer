"""Investigation orchestration: persistence, the issue trail, and import round-trip."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest

from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.issues.engine import IssueEngine
from netadmin.issues.store_repository import StoreIssueRepository
from netadmin.llm import service
from netadmin.store.repository import Repository

BASE_TS = 1_700_000_000


def _store_engine(tmp_db_path: Path) -> tuple[Repository, IssueEngine, int]:
    store = Repository.open(tmp_db_path, site_id="default")
    port = store.upsert_entity(
        Entity(entity_type=EntityType.PORT, native_id="aa:bb:cc:00:00:02:5", name="Port 5"),
        ts=BASE_TS,
    )
    issue_id = store.insert_issue(
        fingerprint="fp-cable",
        detector_key="wired.bad_cable",
        severity="p2",
        state="active",
        first_seen_ts=BASE_TS,
        last_seen_ts=BASE_TS + 600,
        title="rx_errors climbing on Port 5",
        entity_id=port,
        evidence={"rx_errors_per_min": 42},
    )
    engine = IssueEngine(StoreIssueRepository(store))
    return store, engine, issue_id


class _FakeProvider:
    """A blocking provider that returns a fixed answer, for the answered path."""

    name = "fake"
    blocking = True

    def __init__(self, answer: str) -> None:
        self._answer = answer

    def investigate(self, dossier: str) -> Optional[str]:
        assert "Investigation dossier" in dossier  # it was handed the real dossier
        return self._answer


def test_manual_run_is_pending_and_writes_file(tmp_db_path: Path, tmp_path: Path) -> None:
    store, engine, issue_id = _store_engine(tmp_db_path)
    try:
        outcome = service.run_investigation(
            store, engine, issue_id, "manual", now=BASE_TS + 700, base_dir=tmp_path
        )
        assert outcome.status == "pending"
        assert outcome.response_md is None
        assert outcome.dossier_path is not None
        assert Path(outcome.dossier_path).exists()

        # persisted pending row
        row = store.get_investigation(outcome.investigation_id)
        assert row["status"] == "pending"
        assert row["provider"] == "manual"
        assert "Investigation dossier" in row["dossier_md"]

        # a single 'investigated' event on the trail, status pending
        events = [e for e in store.list_issue_events(issue_id) if e["kind"] == "investigated"]
        assert len(events) == 1
    finally:
        store.close()


def test_import_round_trip_answers_pending(tmp_db_path: Path, tmp_path: Path) -> None:
    store, engine, issue_id = _store_engine(tmp_db_path)
    try:
        started = service.run_investigation(
            store, engine, issue_id, "manual", now=BASE_TS + 700, base_dir=tmp_path
        )
        response = "## Answers\n### Root cause\nFailing cable pair.\n### Confidence\nhigh"
        answered = service.import_response(store, engine, issue_id, response, now=BASE_TS + 3600)

        # attached to the same pending investigation, now answered
        assert answered.investigation_id == started.investigation_id
        assert answered.status == "answered"
        row = store.get_investigation(started.investigation_id)
        assert row["status"] == "answered"
        assert row["response_md"] == response

        # two 'investigated' events: dossier generated, then response imported
        events = [e for e in store.list_issue_events(issue_id) if e["kind"] == "investigated"]
        assert len(events) == 2
        assert '"imported":true' in events[-1]["detail"].replace(" ", "")
    finally:
        store.close()


def test_import_without_pending_creates_answered_row(tmp_db_path: Path) -> None:
    store, engine, issue_id = _store_engine(tmp_db_path)
    try:
        response = "## Answers\n### Root cause\nNo dossier was generated first."
        outcome = service.import_response(store, engine, issue_id, response, now=BASE_TS + 10)
        assert outcome.status == "answered"
        row = store.get_investigation(outcome.investigation_id)
        assert row["status"] == "answered"
        assert row["response_md"] == response
        assert store.list_investigations(issue_id)[0]["id"] == outcome.investigation_id
    finally:
        store.close()


def test_blocking_provider_run_is_answered(
    tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, engine, issue_id = _store_engine(tmp_db_path)
    try:
        fake = _FakeProvider("## Answers\n### Root cause\nBad cable.")
        monkeypatch.setattr(service, "build_provider", lambda *a, **k: fake)
        outcome = service.run_investigation(store, engine, issue_id, "fake", now=BASE_TS + 800)
        assert outcome.status == "answered"
        assert outcome.response_md == "## Answers\n### Root cause\nBad cable."
        row = store.get_investigation(outcome.investigation_id)
        assert row["status"] == "answered"
        assert row["provider"] == "fake"

        # start (pending) + complete (answered) → two 'investigated' events
        events = [e for e in store.list_issue_events(issue_id) if e["kind"] == "investigated"]
        assert len(events) == 2
    finally:
        store.close()


def test_copilot_absent_cli_writes_no_pending_row(
    tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On a box without the Copilot CLI, start_investigation must raise
    # ProviderUnavailableError BEFORE writing a pending investigations row or
    # emitting an 'investigated' lifecycle event — no orphan row, no spurious event.
    from netadmin.llm import provider as prov
    from netadmin.llm.provider import ProviderUnavailableError

    monkeypatch.delenv("NETADMIN_COPILOT_CMD", raising=False)
    monkeypatch.setattr(prov.shutil, "which", lambda _cmd: None)

    store, engine, issue_id = _store_engine(tmp_db_path)
    try:
        with pytest.raises(ProviderUnavailableError):
            service.start_investigation(store, engine, issue_id, "copilot", now=BASE_TS + 700)
        assert store.list_investigations(issue_id) == []
        detail = store.get_issue(issue_id)
        events = store.list_issue_events(issue_id) if hasattr(store, "list_issue_events") else []
        assert "investigated" not in [e["kind"] for e in events]
        assert detail is not None
    finally:
        store.close()


def test_run_unknown_issue_raises(tmp_db_path: Path) -> None:
    store, engine, _ = _store_engine(tmp_db_path)
    try:
        with pytest.raises(KeyError):
            service.run_investigation(store, engine, 9999, "manual")
        with pytest.raises(KeyError):
            service.import_response(store, engine, 9999, "x")
    finally:
        store.close()
