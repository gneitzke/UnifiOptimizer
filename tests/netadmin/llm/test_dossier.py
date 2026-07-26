"""Dossier builder tests: a golden snapshot (stable except timestamps) + parsing.

The golden fixture is masked so only the *content* is asserted — every ISO-8601
timestamp is normalised to ``<TS>`` before comparison, per the phase brief's
"stable except timestamps" requirement. Regenerate the fixture with::

    python tests/netadmin/llm/test_dossier.py
"""

from __future__ import annotations

import re
from pathlib import Path

from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.llm.dossier import build_dossier, build_incident_dossier, parse_answers
from netadmin.store.metrics import MetricKind
from netadmin.store.repository import Repository, SampleReading

BASE_TS = 1_700_000_000
GOLDEN = Path(__file__).parent / "fixtures" / "dossier_bad_cable.golden.md"

_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def _mask(text: str) -> str:
    """Normalise every ISO-8601 UTC timestamp to a stable placeholder."""
    return _TS_RE.sub("<TS>", text)


def seed_bad_cable(store: Repository) -> int:
    """A deterministic 'bad cable on Port 5' issue with a full context around it.

    Exercises every dossier section: an entity with a parent (switch) and a
    grandparent-free topology, a metric series windowed around first_seen, ruled-
    out confounders, a lifecycle trail, and related issues on the entity + parent.
    Returns the issue id under investigation.
    """
    sw = store.upsert_entity(
        Entity(entity_type=EntityType.SWITCH, native_id="aa:bb:cc:00:00:02", name="sw-core"),
        ts=BASE_TS,
    )
    port = store.upsert_entity(
        Entity(
            entity_type=EntityType.PORT,
            native_id="aa:bb:cc:00:00:02:5",
            name="Port 5",
            parent_id=sw,
        ),
        ts=BASE_TS,
    )
    store.upsert_entity(
        Entity(
            entity_type=EntityType.AP,
            native_id="aa:bb:cc:00:00:01",
            name="ap-office",
            model="U6-Pro",
        ),
        ts=BASE_TS,
    )

    # A gauge series on the port, 5-min spacing across the ±3 h window.
    for i in range(24):
        store.record_samples(
            [
                SampleReading(
                    entity_id=port,
                    metric="rx_dropped_pct",
                    ts=BASE_TS - 3 * 3600 + i * 300,
                    value=float(1 + (i % 5)),
                    unit="%",
                    kind=MetricKind.GAUGE,
                )
            ]
        )

    issue_id = store.insert_issue(
        fingerprint="fp-bad-cable",
        detector_key="wired.bad_cable",
        severity="p2",
        state="active",
        first_seen_ts=BASE_TS,
        last_seen_ts=BASE_TS + 600,
        title="rx_errors climbing on Port 5",
        entity_id=port,
        occurrences=3,
        evidence={
            "rx_errors_per_min": 42,
            "negotiated_mbps": 100,
            "confounders_checked": [
                "known_100mbps_device",
                "counter_age",
                "unmanaged_switch_hop",
            ],
        },
    )
    store.record_issue_event(issue_id, "detected", ts=BASE_TS, detail={"severity": "p2"})
    store.record_issue_event(issue_id, "escalated", ts=BASE_TS + 600, detail={"m": 3})

    # A related, still-open issue on the same port and one on its parent switch.
    store.insert_issue(
        fingerprint="fp-flap",
        detector_key="wired.port_flapping",
        severity="p2",
        state="active",
        first_seen_ts=BASE_TS - 100,
        last_seen_ts=BASE_TS + 200,
        title="Port 5 flapping",
        entity_id=port,
    )
    store.insert_issue(
        fingerprint="fp-poe",
        detector_key="wired.poe_budget",
        severity="p2",
        state="active",
        first_seen_ts=BASE_TS - 50,
        last_seen_ts=BASE_TS + 100,
        title="PoE budget pressure on sw-core",
        entity_id=sw,
    )
    return issue_id


def test_dossier_matches_golden(tmp_db_path: Path) -> None:
    store = Repository.open(tmp_db_path, site_id="default")
    try:
        issue_id = seed_bad_cable(store)
        dossier = build_dossier(issue_id, store, now=BASE_TS + 600)
    finally:
        store.close()

    assert (
        GOLDEN.exists()
    ), "golden fixture missing; regenerate with `python tests/.../test_dossier.py`"
    assert _mask(dossier) == GOLDEN.read_text(encoding="utf-8")


def test_dossier_is_deterministic(tmp_db_path: Path) -> None:
    store = Repository.open(tmp_db_path, site_id="default")
    try:
        issue_id = seed_bad_cable(store)
        first = build_dossier(issue_id, store, now=BASE_TS + 600)
        second = build_dossier(issue_id, store, now=BASE_TS + 600)
    finally:
        store.close()
    assert first == second


def test_dossier_contains_required_sections(tmp_db_path: Path) -> None:
    store = Repository.open(tmp_db_path, site_id="default")
    try:
        issue_id = seed_bad_cable(store)
        dossier = build_dossier(issue_id, store, now=BASE_TS + 600)
    finally:
        store.close()

    for heading in (
        "# Investigation dossier",
        "## Lifecycle trail",
        "## Evidence",
        "## Confounders ruled out",
        "## Related issues",
        "## Metric windows around first seen",
        "## Site context",
        "## Detector playbook — `wired.bad_cable`",
        "## STRUCTURED QUESTIONS",
    ):
        assert heading in dossier, heading
    # the playbook signature and a ruled-out confounder both surface
    assert "broken-pair downshift" in dossier
    assert "Unmanaged switch hop" in dossier
    # related issues on the entity and its parent are correlated in
    assert "Port 5 flapping" in dossier
    assert "PoE budget pressure on sw-core" in dossier
    # the windowed metric renders bucketed stats, not raw samples
    assert "rx_dropped_pct" in dossier
    assert "| Hour (UTC) | n | min | mean | max |" in dossier


def test_dossier_unknown_issue_raises(tmp_db_path: Path) -> None:
    store = Repository.open(tmp_db_path, site_id="default")
    try:
        try:
            build_dossier(999, store)
        except KeyError as exc:
            assert "999" in str(exc)
        else:  # pragma: no cover - the call must raise
            raise AssertionError("expected KeyError for an unknown issue")
    finally:
        store.close()


def test_incident_dossier_narrates_root_and_symptoms(tmp_db_path: Path) -> None:
    from netadmin.correlate.engine import CorrelationEngine
    from netadmin.correlate.store_repository import StoreCorrelationRepository

    store = Repository.open(tmp_db_path, site_id="default")
    try:
        ap = store.upsert_entity(
            Entity(entity_type=EntityType.AP, native_id="02:00:00:00:00:01", name="Back Porch"),
            ts=BASE_TS,
        )
        root = store.insert_issue(
            fingerprint="mesh-root",
            detector_key="wifi.mesh_uplink",
            severity="p2",
            state="active",
            first_seen_ts=BASE_TS,
            last_seen_ts=BASE_TS + 600,
            title="Weak mesh backhaul on Back Porch",
            entity_id=ap,
            evidence={"uplink_rssi_dbm": -78},
        )
        store.insert_issue(
            fingerprint="cov-hole",
            detector_key="net.coverage_hole",
            severity="p2",
            state="active",
            first_seen_ts=BASE_TS + 300,
            last_seen_ts=BASE_TS + 600,
            title="Coverage hole on Back Porch",
            entity_id=ap,
        )
        store.record_issue_event(root, "detected", ts=BASE_TS)
        CorrelationEngine(StoreCorrelationRepository(store)).run(BASE_TS + 900)
        incident = store.list_incidents(open_only=True)[0]

        dossier = build_incident_dossier(int(incident["id"]), store, now=BASE_TS + 900)
        assert dossier.startswith("# Incident:")
        assert "## Symptoms attributed to this root" in dossier
        assert "net.coverage_hole" in dossier
        assert "## Root cause" in dossier
        # The root's full issue dossier is embedded.
        assert "Investigation dossier — issue #" in dossier
    finally:
        store.close()


def test_incident_dossier_unknown_raises(tmp_db_path: Path) -> None:
    store = Repository.open(tmp_db_path, site_id="default")
    try:
        try:
            build_incident_dossier(999, store)
        except KeyError as exc:
            assert "999" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("expected KeyError for an unknown incident")
    finally:
        store.close()


def test_parse_answers_extracts_subheadings() -> None:
    response = (
        "Some preamble.\n\n## Answers\n\n"
        "### Root cause\nA failing cable pair on Port 5.\n\n"
        "### Evidence to collect next\nSwap the patch cable and re-check rx_errors.\n\n"
        "### Recommended fix and risk\nReplace the cable; low risk.\n\n"
        "### Confidence\nhigh — errors track exactly one port.\n"
    )
    parsed = parse_answers(response)
    assert parsed["Root cause"] == "A failing cable pair on Port 5."
    assert parsed["Confidence"].startswith("high")
    assert "Recommended fix and risk" in parsed


def test_parse_answers_without_heading_is_empty() -> None:
    assert parse_answers("no answers block here, just prose") == {}


def test_num_large_fractional_keeps_precision() -> None:
    # Finding 8: `.3g` collapses a 4-digit fractional to scientific notation and
    # drops the magnitude a reader needs; from 1000 up we keep one decimal instead.
    from netadmin.llm.dossier import _num  # noqa: PLC2701 - test-internal

    assert _num(1234.5) == "1234.5"  # not "1.23e+03"
    assert _num(1_000_000.25) == "1000000.2"
    assert _num(-4200.75) == "-4200.8"
    # whole-valued floats still render bare; sub-1000 keeps 3 sig figs
    assert _num(1500.0) == "1500"
    assert _num(42.1234) == "42.1"
    assert _num(7) == "7"


def test_related_children_issues_use_one_batched_query(tmp_db_path: Path) -> None:
    # Finding 8 (N+1): the related-issues section must resolve every child's issues
    # in a single query, not one list_issues call per child.
    store = Repository.open(tmp_db_path, site_id="default")
    try:
        sw = store.upsert_entity(
            Entity(entity_type=EntityType.SWITCH, native_id="aa:bb:cc:00:00:02", name="sw-core"),
            ts=BASE_TS,
        )
        ports = [
            store.upsert_entity(
                Entity(
                    entity_type=EntityType.PORT,
                    native_id=f"aa:bb:cc:00:00:02:{i}",
                    name=f"Port {i}",
                    parent_id=sw,
                ),
                ts=BASE_TS,
            )
            for i in range(3)
        ]
        for i, pid in enumerate(ports):
            store.insert_issue(
                fingerprint=f"fp-port-{i}",
                detector_key="wired.bad_cable",
                severity="p3",
                state="active",
                first_seen_ts=BASE_TS,
                last_seen_ts=BASE_TS,
                title=f"child issue on Port {i}",
                entity_id=pid,
            )
        # The issue under investigation is on the SWITCH, so the children branch runs.
        sw_issue = store.insert_issue(
            fingerprint="fp-sw",
            detector_key="wired.poe_budget",
            severity="p2",
            state="active",
            first_seen_ts=BASE_TS,
            last_seen_ts=BASE_TS + 600,
            title="PoE budget pressure",
            entity_id=sw,
        )

        batch_calls = {"n": 0}
        per_entity_ids: list[int] = []
        orig_batch = store.list_issues_for_entities
        orig_single = store.list_issues

        def spy_batch(ids):  # type: ignore[no-untyped-def]
            batch_calls["n"] += 1
            return orig_batch(ids)

        def spy_single(**kw):  # type: ignore[no-untyped-def]
            if kw.get("entity_id") is not None:
                per_entity_ids.append(kw["entity_id"])
            return orig_single(**kw)

        store.list_issues_for_entities = spy_batch  # type: ignore[method-assign]
        store.list_issues = spy_single  # type: ignore[method-assign]

        dossier = build_dossier(sw_issue, store, now=BASE_TS + 600)
    finally:
        store.close()

    # Every child's issue is correlated into the dossier.
    for i in range(3):
        assert f"child issue on Port {i}" in dossier
    assert "### On children" in dossier
    # Exactly one batched query for the three children (the N+1 is gone).
    assert batch_calls["n"] == 1
    # list_issues is never called per-child; only the self lookup uses it (the
    # switch has no parent), so no port id appears among the per-entity calls.
    assert set(per_entity_ids).isdisjoint(set(ports))


def _regenerate() -> None:  # pragma: no cover - dev helper
    import tempfile

    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        store = Repository.open(Path(tmp) / "gen.db", site_id="default")
        issue_id = seed_bad_cable(store)
        dossier = build_dossier(issue_id, store, now=BASE_TS + 600)
        store.close()
    GOLDEN.write_text(_mask(dossier), encoding="utf-8")
    print(f"wrote {GOLDEN}")


if __name__ == "__main__":  # pragma: no cover
    _regenerate()
