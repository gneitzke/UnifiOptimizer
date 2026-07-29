"""Tests for the Home Assistant MQTT bridge (``docs/ARCHITECTURE.md`` 11).

A protocol-level fake broker (:class:`FakeMqttClient`) records every publish, so we
assert on the exact discovery JSON shapes, the LWT wiring, the per-issue lifecycle
(add on active, remove on resolve), the disabled-by-default no-op, and — the safety
property that matters most for a public repo — that broker credentials never reach
the logs. No real ``aiomqtt`` and no real broker are involved anywhere.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path
from typing import Any, Optional

import pytest

from netadmin.config import Settings, SleRuntimeConfig
from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType, IssueState, Severity
from netadmin.integrations.home_assistant import _IssueView  # noqa: PLC2701 - test-internal
from netadmin.integrations.home_assistant import (
    HaPublisher,
    HaTopics,
    build_ha_publisher,
    health_discovery,
    issue_attributes,
    issue_binary_sensor_discovery,
    issue_count_discovery,
    issue_uid,
    sle_discovery,
)
from netadmin.issues.engine import IssueEngine
from netadmin.issues.store_repository import StoreIssueRepository
from netadmin.sle.classifiers import ALL_SLES
from netadmin.store.repository import Repository

NOW = 1_900_000_000
SECRET_PASSWORD = "super-secret-broker-pw-8123"  # noqa: S105 - fake test credential
SECRET_USER = "mqtt-service-account-xyz"


# --- fakes ----------------------------------------------------------------- #


class _Published:
    __slots__ = ("topic", "payload", "qos", "retain")

    def __init__(self, topic: str, payload: Any, qos: int, retain: bool) -> None:
        self.topic = topic
        self.payload = payload
        self.qos = qos
        self.retain = retain


class FakeMqttClient:
    """An in-memory ``aiomqtt.Client`` stand-in: records publishes, connects clean.

    ``fail_after`` lets a test simulate a mid-session broker drop to exercise the
    reconnect path. Retained state (last payload per topic) is exposed so tests can
    assert the current broker view the way HA would see it.
    """

    def __init__(self, *, fail_after: Optional[int] = None) -> None:
        self.published: list[_Published] = []
        self.connects = 0
        self._fail_after = fail_after

    async def __aenter__(self) -> "FakeMqttClient":
        self.connects += 1
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def publish(
        self, topic: str, payload: Any = None, qos: int = 0, retain: bool = False
    ) -> None:
        if self._fail_after is not None:
            if len(self.published) >= self._fail_after:
                raise RuntimeError("simulated broker disconnect")
        self.published.append(_Published(topic, payload, qos, retain))

    # -- assertion helpers --
    def topics(self) -> list[str]:
        return [p.topic for p in self.published]

    def last_payload(self, topic: str) -> Any:
        for p in reversed(self.published):
            if p.topic == topic:
                return p.payload
        raise KeyError(topic)

    def payloads_on(self, topic: str) -> list[Any]:
        return [p.payload for p in self.published if p.topic == topic]

    def json_on(self, topic: str) -> Any:
        return json.loads(self.last_payload(topic))


# --- fixtures -------------------------------------------------------------- #


@pytest.fixture
def store(tmp_db_path: Path) -> Repository:
    r = Repository.open(tmp_db_path)
    yield r
    r.close()


@pytest.fixture
def engine(store: Repository) -> IssueEngine:
    return IssueEngine(StoreIssueRepository(store))


def _settings(
    tmp_db_path: Path,
    *,
    enabled: bool = True,
    host: Optional[str] = "broker.test.local",
) -> Settings:
    return Settings(
        _env_file=None,
        db_path=tmp_db_path,
        ha_mqtt_host=host,
        ha_mqtt_port=1883,
        ha_mqtt_username=SECRET_USER,
        ha_mqtt_password=SECRET_PASSWORD,
        ha={"enabled": enabled, "state_refresh_s": 3600},
    )


def _seed_issue(
    store: Repository,
    *,
    fingerprint: str,
    severity: str,
    state: str,
    detector_key: str = "wired.bad_cable",
    title: str = "Bad cable on port 3",
    entity_id: Optional[int] = None,
    evidence: Optional[dict[str, Any]] = None,
) -> int:
    return store.insert_issue(
        fingerprint=fingerprint,
        detector_key=detector_key,
        severity=severity,
        state=state,
        first_seen_ts=NOW - 3600,
        last_seen_ts=NOW,
        title=title,
        entity_id=entity_id,
        evidence=evidence or {"rx_errors_per_min": 42, "confounders_checked": ["counter_age"]},
    )


def _view(**kw: Any) -> _IssueView:
    base: dict[str, Any] = {
        "uid": "abc123def456",
        "fingerprint": "abc123def456" + "0" * 28,
        "title": "Bad cable on port 3",
        "detector_key": "wired.bad_cable",
        "severity": "p2",
        "first_seen_ts": NOW - 3600,
        "occurrences": 5,
        "entity_name": "sw-core:3",
        "evidence": {"rx_errors_per_min": 42, "confounders_checked": ["counter_age"]},
    }
    base.update(kw)
    return _IssueView(**base)


# --- discovery payload shapes ---------------------------------------------- #


def test_health_discovery_is_valid_ha_shape(tmp_db_path: Path) -> None:
    cfg = _settings(tmp_db_path).ha
    topics = HaTopics.from_config(cfg)
    payload = health_discovery(cfg, topics)

    assert payload["unique_id"] == "netadmin_health"
    assert payload["object_id"] == "netadmin_health"
    assert payload["state_topic"] == "netadmin/state"
    # Null-safe template (finding 6): a JSON null (no data) renders HA 'unknown',
    # never a bogus 0 in a % measurement sensor.
    assert payload["value_template"] == (
        "{{ 'unknown' if value_json.health is none else value_json.health }}"
    )
    assert "value_json.health" in payload["value_template"]
    assert payload["unit_of_measurement"] == "%"
    # Availability wiring points at the LWT topic — this is what greys HA out.
    assert payload["availability_topic"] == "netadmin/status"
    assert payload["payload_not_available"] == "offline"
    # Grouped under one HA device.
    assert payload["device"]["identifiers"] == ["netadmin"]
    # Serialises to JSON with no surprises.
    assert json.loads(json.dumps(payload)) == payload


def test_sle_discovery_covers_every_sle(tmp_db_path: Path) -> None:
    cfg = _settings(tmp_db_path).ha
    topics = HaTopics.from_config(cfg)
    for sle in ALL_SLES:
        payload = sle_discovery(cfg, topics, sle)
        assert payload["unique_id"] == f"netadmin_sle_{sle}"
        # Null-safe template (finding 6): no-data SLE renders 'unknown', not 0.
        assert payload["value_template"] == (
            f"{{{{ 'unknown' if value_json.sle.{sle} is none else value_json.sle.{sle} }}}}"
        )
        assert payload["state_topic"] == "netadmin/state"


def test_issue_count_discovery_shape(tmp_db_path: Path) -> None:
    cfg = _settings(tmp_db_path).ha
    topics = HaTopics.from_config(cfg)
    payload = issue_count_discovery(cfg, topics, "p1")
    assert payload["unique_id"] == "netadmin_issues_p1"
    assert payload["value_template"] == "{{ value_json.issues.p1 }}"


def test_rebrand_keeps_entity_ids_and_topics_but_renames_device(tmp_db_path: Path) -> None:
    """Rebrand safety net: the visible product name is 'UnifiOptimizer', but every
    HA-facing identifier that the user's live dashboard depends on stays 'netadmin'.

    The dashboard reads ``sensor.netadmin_*`` / ``binary_sensor.netadmin_*``; those
    object_ids derive from ``node_id`` and the topics from ``base_topic``. Only the
    device *friendly display name* may change. Renaming an object_id, unique_id, or
    topic would silently orphan every entity — a critical regression this guards.
    """
    cfg = _settings(tmp_db_path).ha

    # Identifiers that must NOT drift from 'netadmin'.
    assert cfg.node_id == "netadmin"
    assert cfg.base_topic == "netadmin"
    # The one field that carries the new visible brand.
    assert cfg.device_name == "UnifiOptimizer"

    topics = HaTopics.from_config(cfg)

    payloads: list[dict[str, Any]] = [
        health_discovery(cfg, topics),
        issue_count_discovery(cfg, topics, "p1"),
        *(sle_discovery(cfg, topics, sle) for sle in ALL_SLES),
    ]
    payloads.append(issue_binary_sensor_discovery(cfg, topics, _view()))

    for payload in payloads:
        # object_id / unique_id stem stays 'netadmin_*' so sensor.netadmin_* holds.
        assert payload["object_id"].startswith("netadmin_")
        assert payload["unique_id"].startswith("netadmin_")
        # All state/availability topics still hang off the 'netadmin' base_topic.
        assert payload["state_topic"].startswith("netadmin/")
        assert payload["availability_topic"].startswith("netadmin/")
        # The shared device groups by the stable identifier, but shows the new name.
        assert payload["device"]["identifiers"] == ["netadmin"]
        assert payload["device"]["name"] == "UnifiOptimizer"


def test_issue_binary_sensor_discovery_shape() -> None:
    cfg = _settings(Path("x")).ha
    topics = HaTopics.from_config(cfg)
    view = _view()
    payload = issue_binary_sensor_discovery(cfg, topics, view)
    assert payload["unique_id"] == "netadmin_issue_abc123def456"
    assert payload["device_class"] == "problem"
    assert payload["state_topic"] == "netadmin/issue/abc123def456/state"
    assert payload["json_attributes_topic"] == "netadmin/issue/abc123def456/attributes"
    assert payload["payload_on"] == "ON"


def test_issue_attributes_digest_is_capped() -> None:
    big_evidence = {f"metric_{i}": "x" * 50 for i in range(50)}
    big_evidence["confounders_checked"] = ["a", "b"]
    view = _view(evidence=big_evidence)
    attrs = issue_attributes(view, now=NOW)
    assert attrs["title"] == "Bad cable on port 3"
    assert attrs["detector"] == "wired.bad_cable"
    assert attrs["severity"] == "p2"
    assert attrs["duration_s"] == 3600
    assert attrs["entity"] == "sw-core:3"
    # HA caps attribute strings at 255 chars; the digest must never exceed it, and
    # confounders are excluded from the digest.
    assert len(attrs["evidence"]) <= 255
    assert "confounders_checked" not in attrs["evidence"]


# --- lifecycle: connect announces everything ------------------------------- #


@pytest.mark.asyncio
async def test_on_connect_publishes_availability_and_full_discovery(
    store: Repository, engine: IssueEngine, tmp_db_path: Path
) -> None:
    client = FakeMqttClient()
    pub = build_ha_publisher(_settings(tmp_db_path), store, engine, client_factory=lambda: client)
    await pub._on_connect(client)  # exercise the announce path directly

    # LWT availability announced online, retained.
    avail = [p for p in client.published if p.topic == "netadmin/status"]
    assert avail and avail[0].payload == "online" and avail[0].retain is True

    topics = client.topics()
    # Static discovery: health + 6 SLE + 3 count sensors all announced.
    assert "homeassistant/sensor/netadmin/netadmin_health/config" in topics
    for sle in ALL_SLES:
        assert f"homeassistant/sensor/netadmin/netadmin_sle_{sle}/config" in topics
    for sev in ("p1", "p2", "p3"):
        assert f"homeassistant/sensor/netadmin/netadmin_issues_{sev}/config" in topics
    # Initial state doc published (retained).
    assert "netadmin/state" in topics


@pytest.mark.asyncio
async def test_refresh_loop_reasserts_online_availability(
    store: Repository, engine: IssueEngine, tmp_db_path: Path
) -> None:
    """The refresh loop re-publishes retained ``online`` every cycle, so a stray
    ``offline`` from another actor self-heals within one interval instead of
    greying HA out until the next reconnect."""
    client = FakeMqttClient()
    pub = build_ha_publisher(_settings(tmp_db_path), store, engine, client_factory=lambda: client)

    # Run one refresh iteration. The loop sleeps `interval` (floored at 1s) before
    # its first publish, so patch sleep to a no-op and cancel after one pass.
    pub._cfg.state_refresh_s = 1
    real_sleep = asyncio.sleep

    async def _instant(_seconds: float) -> None:
        await real_sleep(0)

    task = asyncio.create_task(pub._refresh_loop(client))
    import unittest.mock

    with unittest.mock.patch.object(asyncio, "sleep", _instant):
        await real_sleep(0.05)  # let several no-sleep iterations run
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    avail = [p for p in client.published if p.topic == "netadmin/status"]
    assert avail, "refresh loop never touched the availability topic"
    assert avail[-1].payload == "online" and avail[-1].retain is True


@pytest.mark.asyncio
async def test_lwt_is_configured_on_the_client(
    store: Repository, engine: IssueEngine, tmp_db_path: Path
) -> None:
    """The default factory arms the will; assert the publisher hands the right
    availability topic + offline payload to the factory it builds."""
    pub = HaPublisher(store, engine, _settings(tmp_db_path))
    assert pub._will.topic == "netadmin/status"
    assert pub._will.payload == "offline"
    assert pub._will.retain is True


# --- per-issue add / remove ------------------------------------------------ #


@pytest.mark.asyncio
async def test_active_p2_issue_gets_a_binary_sensor(
    store: Repository, engine: IssueEngine, tmp_db_path: Path
) -> None:
    eid = store.upsert_entity(
        Entity(entity_type=EntityType.PORT, native_id="sw:3", name="sw-core:3"), ts=NOW
    )
    _seed_issue(
        store,
        fingerprint="f" * 40,
        severity=Severity.P2.value,
        state=IssueState.ACTIVE.value,
        entity_id=eid,
    )
    client = FakeMqttClient()
    pub = build_ha_publisher(_settings(tmp_db_path), store, engine, client_factory=lambda: client)
    await pub._reconcile(client)

    uid = issue_uid("f" * 40)
    cfg_topic = f"homeassistant/binary_sensor/netadmin/netadmin_issue_{uid}/config"
    assert cfg_topic in client.topics()
    # State ON + attributes published for the issue.
    assert client.last_payload(f"netadmin/issue/{uid}/state") == "ON"
    attrs = client.json_on(f"netadmin/issue/{uid}/attributes")
    assert attrs["entity"] == "sw-core:3"
    assert attrs["severity"] == "p2"


@pytest.mark.asyncio
async def test_p3_issue_gets_no_binary_sensor(
    store: Repository, engine: IssueEngine, tmp_db_path: Path
) -> None:
    _seed_issue(
        store,
        fingerprint="a" * 40,
        severity=Severity.P3.value,
        state=IssueState.ACTIVE.value,
    )
    client = FakeMqttClient()
    pub = build_ha_publisher(_settings(tmp_db_path), store, engine, client_factory=lambda: client)
    await pub._reconcile(client)
    uid = issue_uid("a" * 40)
    assert not any(f"issue_{uid}" in t for t in client.topics())


@pytest.mark.asyncio
async def test_resolving_issue_is_discovery_removed(
    store: Repository, engine: IssueEngine, tmp_db_path: Path
) -> None:
    issue_id = _seed_issue(
        store,
        fingerprint="b" * 40,
        severity=Severity.P1.value,
        state=IssueState.ACTIVE.value,
    )
    client = FakeMqttClient()
    pub = build_ha_publisher(_settings(tmp_db_path), store, engine, client_factory=lambda: client)
    await pub._reconcile(client)
    uid = issue_uid("b" * 40)
    cfg_topic = f"homeassistant/binary_sensor/netadmin/netadmin_issue_{uid}/config"
    assert json.loads(client.last_payload(cfg_topic))  # non-empty config present

    # Resolve the issue, reconcile again -> discovery removed via empty retained payload.
    store.update_issue(issue_id, state=IssueState.RESOLVED.value, resolved_ts=NOW)
    await pub._reconcile(client)
    assert client.last_payload(cfg_topic) == ""
    assert client.published[-1].retain is True
    assert uid not in pub._published


@pytest.mark.asyncio
async def test_resolve_clears_all_retained_issue_topics(
    store: Repository, engine: IssueEngine, tmp_db_path: Path
) -> None:
    # Finding 5: a resolve must clear the discovery config AND the retained state and
    # attributes topics — all three were published retained for the live issue, so
    # leaving state/attributes behind would resurrect a stale ON/attributes pair.
    issue_id = _seed_issue(
        store,
        fingerprint="e" * 40,
        severity=Severity.P1.value,
        state=IssueState.ACTIVE.value,
    )
    client = FakeMqttClient()
    pub = build_ha_publisher(_settings(tmp_db_path), store, engine, client_factory=lambda: client)
    await pub._reconcile(client)
    uid = issue_uid("e" * 40)
    cfg_topic = f"homeassistant/binary_sensor/netadmin/netadmin_issue_{uid}/config"
    state_topic = f"netadmin/issue/{uid}/state"
    attrs_topic = f"netadmin/issue/{uid}/attributes"
    assert client.last_payload(state_topic) == "ON"

    store.update_issue(issue_id, state=IssueState.RESOLVED.value, resolved_ts=NOW)
    await pub._reconcile(client)

    # All three retained topics cleared with an empty retained payload.
    for topic in (cfg_topic, state_topic, attrs_topic):
        assert client.last_payload(topic) == ""
        cleared = [p for p in client.published if p.topic == topic and p.payload == ""]
        assert cleared and cleared[-1].retain is True


# --- finding 6: null SLE states never publish 0 into a % sensor ------------ #


def test_nullable_pct_template_maps_none_to_unknown() -> None:
    from netadmin.integrations.home_assistant import (  # noqa: PLC2701 - test-internal
        _HA_UNKNOWN,
        _nullable_pct_template,
    )

    tpl = _nullable_pct_template("value_json.health")
    assert _HA_UNKNOWN in tpl and "is none" in tpl
    jinja2 = pytest.importorskip("jinja2")
    render = jinja2.Template(tpl).render
    assert render(value_json={"health": None}).strip() == "unknown"
    assert render(value_json={"health": 87}).strip() == "87"


@pytest.mark.asyncio
async def test_state_doc_publishes_null_not_zero_when_no_data(
    store: Repository, engine: IssueEngine, tmp_db_path: Path
) -> None:
    # No SLE minutes at all: every score is unknown. The state doc must carry JSON
    # null (honest "no data"), never a fabricated 0; the sensors' null-safe template
    # then renders that as HA 'unknown'.
    mqtt = FakeMqttClient()
    pub = build_ha_publisher(_settings(tmp_db_path), store, engine, client_factory=lambda: mqtt)
    await pub._publish_state(mqtt)
    doc = json.loads(mqtt.last_payload("netadmin/state"))
    assert doc["health"] is None
    assert doc["sle"]  # SLEs are present in the doc
    assert all(v is None for v in doc["sle"].values())


# --- finding 7: backpressure — coalesce bursts, throttle the score pass ---- #


@pytest.mark.asyncio
async def test_score_pass_is_throttled_across_rapid_publishes(
    store: Repository, engine: IssueEngine, tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import netadmin.integrations.home_assistant as ha_mod

    calls = {"n": 0}
    real = ha_mod.sle_scores

    def counting_scores(*a: Any, **k: Any) -> Any:
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(ha_mod, "sle_scores", counting_scores)

    mqtt = FakeMqttClient()
    # A large debounce window: three back-to-back publishes must share ONE score pass.
    pub = HaPublisher(
        store, engine, _settings(tmp_db_path), client_factory=lambda: mqtt, sle_min_interval_s=1000
    )
    await pub._publish_state(mqtt)
    await pub._publish_state(mqtt)
    await pub._publish_state(mqtt)
    assert calls["n"] == 1
    # All three still published a state doc (counts stay fresh; only the score pass
    # is throttled).
    assert len([p for p in mqtt.published if p.topic == "netadmin/state"]) == 3


@pytest.mark.asyncio
async def test_drain_loop_coalesces_a_burst(
    store: Repository, engine: IssueEngine, tmp_db_path: Path
) -> None:
    import asyncio
    import contextlib

    from netadmin.issues.models import EventKind, Transition

    def _t(kind: Any) -> Transition:
        return Transition(
            issue_id=1,
            fingerprint="c" * 40,
            detector_key="wired.bad_cable",
            severity=Severity.P2,
            title="Bad cable",
            kind=kind,
            ts=NOW,
            from_state=None,
            to_state=IssueState.ACTIVE,
            detail={},
        )

    mqtt = FakeMqttClient()
    pub = build_ha_publisher(_settings(tmp_db_path), store, engine, client_factory=lambda: mqtt)

    calls = {"reconcile": 0, "state": 0}

    async def fake_reconcile(_client: Any) -> None:
        calls["reconcile"] += 1

    async def fake_state(_client: Any) -> None:
        calls["state"] += 1

    pub._reconcile = fake_reconcile  # type: ignore[method-assign]
    pub._publish_state = fake_state  # type: ignore[method-assign]

    # Enqueue a burst of five issue-set-changing transitions before the loop runs.
    for _ in range(5):
        pub._queue.put_nowait(_t(EventKind.DETECTED))

    task = asyncio.create_task(pub._drain_loop(mqtt))
    for _ in range(50):
        await asyncio.sleep(0)
        if pub._queue.empty():
            break
    await asyncio.sleep(0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task

    # Every transition reached the events topic, but reconcile + state ran ONCE for
    # the whole batch (finding 7 coalescing).
    assert len([p for p in mqtt.published if p.topic == "netadmin/events"]) == 5
    assert calls["reconcile"] == 1
    assert calls["state"] == 1


# --- state doc reflects scores + counts ------------------------------------ #


@pytest.mark.asyncio
async def test_state_doc_carries_health_sle_and_counts(
    store: Repository, engine: IssueEngine, tmp_db_path: Path
) -> None:
    # One client with 90 ok coverage minutes and 10 weak -> coverage 90 %. The
    # state doc scores over ``[now - window, now]`` on the real clock, so the
    # bucket is seeded near real now (not the fixed NOW, which is years away).
    import time as _time

    real_now = int(_time.time())
    client_eid = store.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id="cl:1"), ts=real_now
    )
    store.add_sle_minutes(
        bucket_ts=real_now - 300, sle="coverage", classifier="ok", entity_id=client_eid, minutes=90
    )
    store.add_sle_minutes(
        bucket_ts=real_now - 300,
        sle="coverage",
        classifier="weak_signal",
        entity_id=client_eid,
        minutes=10,
    )
    _seed_issue(store, fingerprint="1" * 40, severity="p1", state=IssueState.ACTIVE.value)
    _seed_issue(store, fingerprint="2" * 40, severity="p2", state=IssueState.ACTIVE.value)
    _seed_issue(store, fingerprint="3" * 40, severity="p2", state=IssueState.RESOLVING.value)
    # A pending (unconfirmed) issue must NOT be counted.
    _seed_issue(store, fingerprint="4" * 40, severity="p1", state=IssueState.PENDING.value)

    ha_settings = _settings(tmp_db_path)
    # A narrower score window so the one seeded bucket is real, comfortable
    # exposure (1 of 4 buckets, well above the confidence floor) rather than a
    # single bucket lost in the default 24h/288-bucket window -- this test is
    # about the state doc's shape and values, not exposure semantics (see
    # netadmin/sle/scores.py's own tests for those).
    ha_settings.sle = SleRuntimeConfig(score_window_s=1200)

    mqtt = FakeMqttClient()
    pub = build_ha_publisher(ha_settings, store, engine, client_factory=lambda: mqtt)
    await pub._publish_state(mqtt)

    doc = mqtt.json_on("netadmin/state")
    assert doc["sle"]["coverage"] == 90
    assert doc["issues"]["p1"] == 1  # pending p1 excluded
    assert doc["issues"]["p2"] == 2  # active + resolving
    assert doc["health"] is not None


# --- event bridge ---------------------------------------------------------- #


@pytest.mark.asyncio
async def test_transition_is_mirrored_to_events_topic(
    store: Repository, engine: IssueEngine, tmp_db_path: Path
) -> None:
    from netadmin.issues.models import Transition

    mqtt = FakeMqttClient()
    pub = build_ha_publisher(_settings(tmp_db_path), store, engine, client_factory=lambda: mqtt)
    transition = Transition(
        issue_id=7,
        fingerprint="c" * 40,
        detector_key="wan.flapping",
        severity=Severity.P1,
        title="WAN flapping",
        kind="detected",
        ts=NOW,
        from_state=None,
        to_state=IssueState.PENDING,
    )
    await pub._publish_event(mqtt, transition)
    evt = mqtt.json_on("netadmin/events")
    assert evt["kind"] == "detected"
    assert evt["severity"] == "p1"
    assert evt["detector"] == "wan.flapping"
    assert evt["to_state"] == "pending"


# --- disabled / unconfigured no-op ----------------------------------------- #


@pytest.mark.asyncio
async def test_disabled_publisher_is_total_noop(
    store: Repository, engine: IssueEngine, tmp_db_path: Path
) -> None:
    calls: list[int] = []

    def factory() -> FakeMqttClient:
        calls.append(1)
        return FakeMqttClient()

    pub = build_ha_publisher(
        _settings(tmp_db_path, enabled=False), store, engine, client_factory=factory
    )
    await pub.start()
    # No task, no callback registered, factory never called.
    assert pub._task is None
    assert calls == []
    assert pub.on_transition.__self__ not in getattr(engine, "_callbacks", [])  # not registered
    await pub.stop()  # safe to stop a never-started publisher


@pytest.mark.asyncio
async def test_enabled_but_no_broker_host_stays_inert(
    store: Repository, engine: IssueEngine, tmp_db_path: Path
) -> None:
    pub = build_ha_publisher(
        _settings(tmp_db_path, host=None), store, engine, client_factory=lambda: FakeMqttClient()
    )
    await pub.start()
    assert pub._task is None
    await pub.stop()


@pytest.mark.asyncio
async def test_on_transition_before_start_is_ignored(
    store: Repository, engine: IssueEngine, tmp_db_path: Path
) -> None:
    from netadmin.issues.models import Transition

    pub = build_ha_publisher(_settings(tmp_db_path), store, engine)
    # Not running yet -> enqueue is a no-op, never raises.
    pub.on_transition(
        Transition(
            issue_id=1,
            fingerprint="x" * 40,
            detector_key="d",
            severity=Severity.P1,
            title="t",
            kind="detected",
            ts=NOW,
            from_state=None,
            to_state=None,
        )
    )
    assert pub._queue.empty()


# --- credentials never logged ---------------------------------------------- #


@pytest.mark.asyncio
async def test_credentials_never_appear_in_logs(
    store: Repository,
    engine: IssueEngine,
    tmp_db_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="integrations.home_assistant")

    # Drive start (logs "started"), a connect (logs "connected"), and a failure that
    # logs the reconnect line — every path that could leak a credential.
    fail_client = FakeMqttClient(fail_after=0)  # publish raises immediately -> reconnect log

    def factory() -> FakeMqttClient:
        return fail_client

    pub = build_ha_publisher(_settings(tmp_db_path), store, engine, client_factory=factory)
    await pub.start()
    # Let the supervisor connect, fail, and log the reconnect line at least once.
    import asyncio

    await asyncio.sleep(0.05)
    await pub.stop()

    blob = caplog.text
    assert SECRET_PASSWORD not in blob
    assert SECRET_USER not in blob


@pytest.mark.asyncio
async def test_graceful_stop_announces_offline(
    store: Repository, engine: IssueEngine, tmp_db_path: Path
) -> None:
    """A clean stop publishes a retained ``offline`` before disconnecting.

    The MQTT LWT only fires on an *ungraceful* drop, so a graceful shutdown must
    announce offline itself — otherwise the retained ``online`` from connect lingers
    and HA shows netadmin (and every entity) stale-but-online after a deploy/stop.
    """
    import asyncio

    client = FakeMqttClient()
    pub = build_ha_publisher(_settings(tmp_db_path), store, engine, client_factory=lambda: client)
    await pub.start()
    await asyncio.sleep(0.05)  # let the supervisor connect + announce online
    assert client.last_payload("netadmin/status") == "online"

    await pub.stop()

    # The final retained availability payload is offline (a clean grey-out in HA).
    assert client.last_payload("netadmin/status") == "offline"
    offline = [
        p for p in client.published if p.topic == "netadmin/status" and p.payload == "offline"
    ]
    assert offline and offline[-1].retain is True


# --- restart inside one process -------------------------------------------- #


async def _wait_for(predicate: Any, *, timeout: float = 2.0) -> None:
    """Poll until true, failing the test on timeout."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not reached within timeout")


def _bad_cable_finding() -> Any:
    from netadmin.domain.entities import Finding

    return Finding(
        detector_key="wired.bad_cable",
        entity=Entity(
            entity_type=EntityType.PORT, native_id="aa:bb:cc:00:00:01", site_id="default"
        ),
        severity=Severity.P2,
        title="rx_errors climbing",
    )


@pytest.mark.asyncio
async def test_stop_unregisters_the_engine_callback(
    store: Repository, engine: IssueEngine, tmp_db_path: Path
) -> None:
    """Inert is not enough: a callback left attached is delivered twice after a
    restart, because the engine appends unconditionally."""
    client = FakeMqttClient()
    pub = build_ha_publisher(_settings(tmp_db_path), store, engine, client_factory=lambda: client)
    await pub.start()
    assert engine._callbacks.count(pub.on_transition) == 1
    await pub.stop()
    assert pub.on_transition not in engine._callbacks


@pytest.mark.asyncio
async def test_restart_publishes_each_transition_exactly_once(
    store: Repository, engine: IssueEngine, tmp_db_path: Path
) -> None:
    client = FakeMqttClient()
    pub = build_ha_publisher(_settings(tmp_db_path), store, engine, client_factory=lambda: client)
    await pub.start()
    await asyncio.sleep(0.05)  # let the supervisor connect
    await pub.stop()
    await pub.start()
    try:
        await asyncio.sleep(0.05)
        assert engine._callbacks.count(pub.on_transition) == 1
        # One real transition through the engine's public path: a first fire is one
        # ``detected``.
        transitions = engine.process_cycle(NOW, findings=[_bad_cable_finding()])
        assert len(transitions) == 1
        await _wait_for(lambda: client.topics().count("netadmin/events") >= 1)
        await asyncio.sleep(0.05)  # a duplicate would have landed by now
    finally:
        await pub.stop()

    events = [p for p in client.published if p.topic == "netadmin/events"]
    assert len(events) == 1, "a restarted publisher mirrored one transition twice"


@pytest.mark.asyncio
async def test_repeated_start_stop_cycles_never_accumulate_callbacks(
    store: Repository, engine: IssueEngine, tmp_db_path: Path
) -> None:
    client = FakeMqttClient()
    pub = build_ha_publisher(_settings(tmp_db_path), store, engine, client_factory=lambda: client)
    for _ in range(3):
        await pub.start()
        await pub.start()  # a second start is a no-op, not a second subscription
        assert engine._callbacks.count(pub.on_transition) == 1
        await pub.stop()
        await pub.stop()  # stopping twice is safe
        assert pub.on_transition not in engine._callbacks


# --- reconnect ------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_supervisor_reconnects_after_broker_drop(
    store: Repository, engine: IssueEngine, tmp_db_path: Path
) -> None:
    import asyncio

    clients: list[FakeMqttClient] = []

    def factory() -> FakeMqttClient:
        # First client dies mid-session; the second stays up.
        c = FakeMqttClient(fail_after=3 if not clients else None)
        clients.append(c)
        return c

    settings = _settings(tmp_db_path)
    pub = build_ha_publisher(settings, store, engine, client_factory=factory)
    # Shorten backoff so the test does not wait a second.
    import netadmin.integrations.home_assistant as ha_mod

    monkey = ha_mod._BACKOFF_INITIAL_S
    ha_mod._BACKOFF_INITIAL_S = 0.01
    try:
        await pub.start()
        await asyncio.sleep(0.1)
    finally:
        await pub.stop()
        ha_mod._BACKOFF_INITIAL_S = monkey

    assert len(clients) >= 2  # dropped, then reconnected


# --- read-only guarantee --------------------------------------------------- #


def test_publisher_has_no_subscribe_surface(
    store: Repository, engine: IssueEngine, tmp_db_path: Path
) -> None:
    """Structural proof the integration is publish-only: it never references a
    subscribe/message API. A read-only integration by construction (section 11)."""
    pub = build_ha_publisher(_settings(tmp_db_path), store, engine)
    assert not hasattr(pub, "subscribe")
    assert not hasattr(pub, "on_message")


@pytest.mark.asyncio
async def test_state_doc_excludes_suppressed_from_counts_and_discloses(
    store: Repository, engine: IssueEngine, tmp_db_path: Path
) -> None:
    """A suppressed issue leaves the per-severity counts and is disclosed as its
    own ``suppressed`` field, never a silent shrink (Gitea #49)."""
    import time as _time

    now = int(_time.time())
    a = _seed_issue(store, fingerprint="a" * 40, severity="p2", state=IssueState.ACTIVE.value)
    _seed_issue(store, fingerprint="b" * 40, severity="p2", state=IssueState.ACTIVE.value)
    engine.suppress(a, now)

    pub = build_ha_publisher(_settings(tmp_db_path), store, engine, client_factory=FakeMqttClient)
    doc = pub._build_state_doc()

    assert doc.issues["p2"] == 1  # the suppressed p2 is excluded from the count
    assert doc.suppressed == 1  # ...and disclosed here
    assert '"suppressed": 1' in doc.as_json()


@pytest.mark.asyncio
async def test_suppressed_severe_issue_gets_no_binary_sensor(
    store: Repository, engine: IssueEngine, tmp_db_path: Path
) -> None:
    """A suppressed P1/P2 loses its dynamic binary_sensor — no attention pull."""
    import time as _time

    p1 = _seed_issue(store, fingerprint="c" * 40, severity="p1", state=IssueState.ACTIVE.value)
    engine.suppress(p1, int(_time.time()))

    pub = build_ha_publisher(_settings(tmp_db_path), store, engine, client_factory=FakeMqttClient)
    assert pub._active_issue_views() == {}
