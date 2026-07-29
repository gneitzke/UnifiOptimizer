"""The suppression invariant: measured experience never moves (Gitea #49).

The load-bearing safety property of the whole suppression feature. Suppressing an
issue parks its claim on *attention* (counts, badges, alerts, HA sensors). It must
NOT touch a single *measured* number: the health score, the per-SLE scores, the
offenders burden, or impact are all derived from ``sle_minutes`` — facts about what
clients suffered — and suppressing an issue does not un-suffer those minutes. If
suppression could move the health score, an operator could make the network look
perfect by muting everything ("green dashboard syndrome"). This test forbids that.

It deliberately seeds an entity whose offenders burden includes the open-issue
channel (``Repository.open_issue_counts`` feeds both the offenders burden and the
inventory badges — one shared query), so it catches the exact leak the design
review flagged: filter suppressed issues inside that query and the burden moves.
The query is left raw for precisely this reason; this test pins it.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from netadmin.analytics.offenders import rank_offenders
from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.issues.engine import IssueEngine
from netadmin.issues.store_repository import StoreIssueRepository
from netadmin.sle.scores import sle_scores
from netadmin.store.repository import Repository

BASE = 1_700_000_000
WIN = (BASE - 60, BASE + 3600)
DEVICE_TYPES = (EntityType.AP.value, EntityType.SWITCH.value, EntityType.GATEWAY.value)


def _seed(repo: Repository) -> None:
    ap = repo.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id="aa:bb:cc:00:00:01", name="ap-bad"), ts=BASE
    )
    phone = repo.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id="11:22:33:44:55:01", name="phone"), ts=BASE
    )
    # Failed client-minutes attributed to the AP — the measured grief.
    repo.upsert_sle_minute(
        bucket_ts=BASE,
        sle="coverage",
        classifier="weak_signal",
        entity_id=phone,
        attributed_entity_id=ap,
        minutes=500.0,
    )
    repo.upsert_sle_minute(
        bucket_ts=BASE,
        sle="coverage",
        classifier="ok",
        entity_id=phone,
        attributed_entity_id=ap,
        minutes=100.0,
    )
    # Open issues on the AP: these feed the offenders burden's open-issue channel
    # via Repository.open_issue_counts. Suppressing them must not move the burden.
    for i, sev in enumerate(("p1", "p2", "p3")):
        repo.insert_issue(
            fingerprint=f"fp-{i}",
            detector_key="wifi.airtime_saturation",
            severity=sev,
            state="active",
            first_seen_ts=BASE,
            last_seen_ts=BASE,
            title=f"issue {i}",
            entity_id=ap,
        )


def _measured(repo: Repository) -> tuple[object, list[object]]:
    """Every measured surface, as comparable plain data."""
    report = sle_scores(repo, *WIN)
    offenders = rank_offenders(repo, DEVICE_TYPES, *WIN)
    return dataclasses.asdict(report), [dataclasses.asdict(o) for o in offenders]


def test_suppressing_every_issue_moves_no_measured_number(tmp_db_path: Path) -> None:
    repo = Repository.open(tmp_db_path)
    try:
        _seed(repo)

        before_report, before_offenders = _measured(repo)
        # The burden actually reads the issue channel — guard against a fixture that
        # silently seeds no issue counts, which would make this test vacuous.
        assert before_offenders[0]["issue_counts"]["total"] == 3

        engine = IssueEngine(StoreIssueRepository(repo))
        for row in repo.list_issues(open_only=True):
            engine.suppress(int(row["id"]), BASE + 10)
        # Confirm the suppression actually took (fields written, derivation true).
        suppressed_now = [
            r for r in repo.list_issues(open_only=True) if r["suppressed_ts"] is not None
        ]
        assert len(suppressed_now) == 3

        after_report, after_offenders = _measured(repo)

        # Byte-identical: the health score, per-SLE scores, headline, and the whole
        # offenders ranking (burden included) are unchanged by suppression.
        assert after_report == before_report
        assert after_offenders == before_offenders
        assert repr(after_report) == repr(before_report)
        assert after_report["headline"] == before_report["headline"]
    finally:
        repo.close()
