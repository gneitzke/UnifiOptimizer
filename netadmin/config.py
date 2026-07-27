"""Runtime configuration for netadmin.

Settings are assembled from three places, highest priority first:

1. explicit keyword arguments (tests, overrides),
2. process environment + ``data/secrets.env`` (controller credentials),
3. the ``netadmin:`` section of ``data/config.yaml`` (structural defaults).

Credentials (``UNIFI_HOST`` / ``UNIFI_USERNAME`` / ``UNIFI_PASSWORD`` /
``UNIFI_SITE`` and the optional ``UNIFI_API_KEY``) live only in
``data/secrets.env`` (chmod 600, gitignored) and are read at runtime. Nothing
is instantiated at import time; call :func:`get_settings`.
"""

from __future__ import annotations

import io
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Mapping

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


def _runtime_data_dir() -> Path:
    """Runtime ``data/`` directory (secrets.env, config.yaml, the SQLite DB, logs),
    resolved from the environment -- NOT the installed package location.

    A wheel install puts ``netadmin/`` inside site-packages, so deriving ``data/``
    from ``__file__`` there would hide ``data/secrets.env`` from the daemon and write
    the database into site-packages, where ``pip install --upgrade`` silently wipes
    it. Resolve it from the runtime instead:

      * ``NETADMIN_DATA_DIR`` if set (``~`` expanded), else
      * ``./data`` relative to the current working directory.

    So the documented quickstart -- create ``data/secrets.env``, run ``netadmin
    daemon`` from that directory -- works for a wheel install, and the database
    persists across upgrades. For a source checkout run from the repo root this is
    the same ``./data`` as before.
    """
    override = os.environ.get("NETADMIN_DATA_DIR")
    return Path(override).expanduser() if override else Path.cwd() / "data"


# Installed package location -- exported for back-compat only; the data paths below
# deliberately do NOT derive from it (see _runtime_data_dir).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = _runtime_data_dir()
CONFIG_YAML = DATA_DIR / "config.yaml"
SECRETS_ENV = DATA_DIR / "secrets.env"
DEFAULT_DB_PATH = DATA_DIR / "netadmin.db"
DEFAULT_LOG_DIR = DATA_DIR / "logs"


class PollIntervals(BaseModel):
    """Collector cadences in seconds (section 5.2). Offsets keep them unaligned."""

    device_s: int = 60  # stat/device
    sta_s: int = 60  # stat/sta
    health_s: int = 60  # stat/health
    event_catchup_s: int = 300  # stat/event dedupe sweep
    probe_s: int = 60  # DNS / ICMP active probes
    alarm_s: int = 900  # list/alarm, stat/anomalies
    report_5min_s: int = 21_600  # stat/report/5minutes.* (6 h)
    report_daily_s: int = 86_400  # stat/report/hourly.*, daily.*
    rogueap_s: int = 86_400  # stat/rogueap (within=24)
    wlanconf_s: int = 86_400  # rest/wlanconf (our own SSIDs; GET, read-only)


class Retention(BaseModel):
    """Per-tier retention (section 4). Daily rollups are kept forever."""

    raw_days: int = 30
    hourly_months: int = 18
    daily_forever: bool = True
    # Hour of day (UTC, 0-23) the nightly retention prune job fires.
    prune_hour: int = 3


class ProbeConfig(BaseModel):
    """Active-probe targets (section 5.4).

    The controller exposes no DNS/DHCP timing, so a local prober measures it.
    ``gateway_ip`` is the ICMP/TCP RTT target; ``gateway_resolver`` is the
    resolver the DNS probe times (typically the gateway IP). Both are optional:
    when neither is set nor discoverable from inventory, the probe runner idles
    honestly rather than fabricating a target. ``anchor`` is the public resolver
    used as the "is it me or my ISP" comparison.
    """

    enabled: bool = True
    gateway_ip: str | None = None
    gateway_resolver: str | None = None
    anchor: str = "1.1.1.1"


class Backfill(BaseModel):
    """Controller-retention caps used by startup backfill (section 5.3).

    These are conservative defaults; the ingest layer verifies them per install
    at runtime because Network 9.x "auto" retention defaults are unpublished.
    """

    fivemin_hours: int = 24
    hourly_days: int = 7
    daily_days: int = 31


class DetectConfig(BaseModel):
    """Detection-engine scheduler cadences (section 6).

    The three detector tiers plus the incremental baseline update, all wired onto
    the daemon's one AsyncIOScheduler by :func:`netadmin.ingest.factory.build_components`.
    Per-detector *thresholds* live in :attr:`Settings.thresholds` keyed by
    ``detector_key`` (e.g. ``thresholds["wired.bad_cable"]["errors_per_min"]``);
    this block is only the *how often* the engine runs.
    """

    fast_s: int = 60  # FAST tier: every collector fast cadence
    window_s: int = 900  # WINDOW tier: 15-minute rolling window
    daily_hour: int = 3  # DAILY tier: UTC hour for config audits
    baseline_s: int = 300  # EWMA / rolling-quantile incremental update cadence


class CorrelateConfig(BaseModel):
    """Correlation-engine scheduler cadence + temporal guard (section 17).

    The correlation pass groups the confirmed open-issue set into **incidents**
    (one root cause + its symptoms) on a concrete topological/causal-rule basis.
    It runs as one more job on the daemon's single scheduler, offset *after* the
    detector passes so it reasons over the issues those passes just wrote. The
    pass is pure logic over the store and idempotent, so an interval cadence
    (recompute from scratch each tick) is the whole contract — it needs no hook
    into the detect passes beyond running shortly after them.

    ``temporal_slack_s`` is the guard that keeps grouping conservative: a symptom
    whose ``first_seen`` predates its candidate root by more than this window is
    *not* attributed to it (a symptom cannot precede its cause). Mirrors
    :class:`netadmin.correlate.models.CorrelationConfig`, which the factory builds
    from this block.
    """

    enabled: bool = True  # off -> no correlate job is scheduled; incidents go stale
    interval_s: int = 60  # recompute cadence; offset after the detect passes
    temporal_slack_s: int = 900  # symptom-may-predate-root slack (the temporal guard)


# The investigator providers auto-investigation may name (section 21). Mirrors
# ``netadmin.llm.provider.PROVIDER_NAMES``, restated here so importing settings
# never drags in the llm package (config sits below it in the import graph).
INVESTIGATE_PROVIDERS: tuple[str, ...] = ("manual", "copilot", "anthropic")

# Severities auto-investigation may be armed for. Mirrors
# :class:`netadmin.domain.types.Severity`, restated for the same reason.
INVESTIGATE_SEVERITIES: tuple[str, ...] = ("p1", "p2", "p3")


class AutoInvestigateConfig(BaseModel):
    """Unattended investigation of newly-confirmed issues (section 21).

    Spend safety is *structural*, not documentary. ``enabled`` is false and
    ``provider`` is ``manual`` (free, no key, local file) by default, so turning
    on a metered provider takes two deliberate edits -- flipping ``enabled`` alone
    buys a dossier file, never an API bill. ``max_per_hour`` / ``max_per_day`` are
    the hard ceiling underneath that: they are counted per auto-run and, once
    exhausted, skip rather than queue into the next window, so a bad night costs a
    known maximum instead of an unbounded one.

    Credentials are untouched by this block: the anthropic provider still reads
    ``ANTHROPIC_API_KEY`` from the environment / ``data/secrets.env``.
    """

    enabled: bool = False  # master switch; nothing is wired when false
    provider: str = "manual"  # manual | copilot | anthropic
    severities: list[str] = Field(default_factory=lambda: ["p1"])
    settle_s: int = Field(default=120, ge=0)  # post-activation wait before compiling
    storm_threshold: int = Field(default=5, ge=1)  # >N triggers in the window = storm
    storm_window_s: int = Field(default=300, ge=1)
    max_per_hour: int = Field(default=4, ge=0)
    max_per_day: int = Field(default=12, ge=0)
    fallback_to_manual: bool = True  # paid provider unusable -> free manual dossier

    @field_validator("provider")
    @classmethod
    def _known_provider(cls, value: str) -> str:
        """Reject an unknown provider at startup rather than at the first P1.

        Validated unconditionally, not only when ``enabled``: a typo that lies
        dormant until someone flips the switch is exactly the silent failure this
        daemon exists to eliminate.
        """
        key = value.strip().lower()
        if key not in INVESTIGATE_PROVIDERS:
            raise ValueError(
                f"unknown investigate provider {value!r}; "
                f"expected one of {list(INVESTIGATE_PROVIDERS)}"
            )
        return key

    @field_validator("severities")
    @classmethod
    def _known_severities(cls, value: list[str]) -> list[str]:
        """Reject unknown severities loudly rather than silently never firing."""
        normalised = [str(v).strip().lower() for v in value]
        unknown = sorted({s for s in normalised if s not in INVESTIGATE_SEVERITIES})
        if unknown:
            raise ValueError(
                f"unknown investigate severity {unknown}; "
                f"expected any of {list(INVESTIGATE_SEVERITIES)}"
            )
        if not normalised:
            raise ValueError(
                "severities must list at least one of " + ", ".join(INVESTIGATE_SEVERITIES)
            )
        seen: set[str] = set()
        return [s for s in normalised if not (s in seen or seen.add(s))]


class InvestigateConfig(BaseModel):
    """The ``investigate:`` block: how investigations run without a human click."""

    auto: AutoInvestigateConfig = Field(default_factory=AutoInvestigateConfig)


class SleRuntimeConfig(BaseModel):
    """SLE minutes-job scheduler cadence + default scoring window (section 8).

    The classifier *thresholds* (coverage floor, sticky RSSI, cu_total, ...) and the
    headline blend *weights* are read from ``settings.thresholds["sle"]`` by
    :class:`~netadmin.sle.classifiers.SleConfig` and
    :func:`~netadmin.sle.scores.load_weights`; this block is only the job cadence
    and the window ``GET /api/sle`` scores over by default.
    """

    minutes_s: int = 300  # 5-minute-bucket job cadence
    score_window_s: int = 86_400  # default /api/sle look-back window (24 h)


class UpdatesConfig(BaseModel):
    """Self-update version-check cadence (section 23).

    Nothing here is a credential and nothing here enables the pip self-upgrade
    runner (a later phase); this block only gates the read-only PyPI check
    that powers the "an update is available" banner. ``check`` defaults on --
    the check sends nothing but a ``GET`` and a ``User-Agent`` naming this
    build's version, so there is no spend or write risk in leaving it on -- but
    ``UPDATES__CHECK=0`` (or ``check: false`` in ``config.yaml``) must always
    turn it off completely: no task is even created.
    """

    check: bool = True
    interval_s: int = Field(default=86_400, ge=60)  # re-check cadence after the first


class HaConfig(BaseModel):
    """Home Assistant MQTT-discovery integration (section 11).

    Structural config only, and **off by default**: the daemon publishes nothing
    to MQTT until an operator sets ``enabled: true``. Broker *credentials* never
    live here (nor in ``config.yaml``) — they are read from the environment /
    ``data/secrets.env`` (``HA_MQTT_HOST`` / ``HA_MQTT_PORT`` / ``HA_MQTT_USERNAME``
    / ``HA_MQTT_PASSWORD``) through :attr:`Settings.mqtt`. This block carries only
    the non-secret topology of the integration: the discovery prefix HA listens on,
    the base topic our own state/event topics hang off, and the state-refresh
    cadence.
    """

    enabled: bool = False
    discovery_prefix: str = "homeassistant"  # HA's MQTT-discovery listen prefix
    base_topic: str = "netadmin"  # our state/event/availability topic root
    node_id: str = "netadmin"  # discovery node id + object_id prefix
    device_name: str = "UnifiOptimizer"  # HA device friendly display name (entities group under it)
    state_refresh_s: int = 60  # periodic score/count republish cadence


class MqttCredentials(BaseModel):
    """A grouped, read-only view of the MQTT broker credentials (section 11).

    Read from the environment / ``data/secrets.env`` only, mirroring
    :class:`UnifiCredentials`. ``host`` unset means the integration stays inert
    even when ``ha.enabled`` is true — an honest no-op over a half-configured
    connection.
    """

    host: str | None = None
    port: int = 1883
    username: str | None = None
    password: str | None = None

    @property
    def is_configured(self) -> bool:
        """True when at least a broker host is known (auth may be anonymous)."""
        return bool(self.host)


# Channel names key their own secret env vars (``ALERT_URLS__<NAME>``), so the
# alphabet is restricted to what survives an env-var name intact.
_ALERT_NAME_PATTERN = r"^[a-z0-9_]+$"

# The three notifiable lifecycle events (section 20). Deliberately NOT every
# ``EventKind``: a ``detected`` issue is unconfirmed noise and ack/snooze/fix rows
# are bookkeeping, neither of which is worth waking someone up for.
ALERT_EVENTS: tuple[str, ...] = ("opened", "reopened", "resolved")


class AlertChannelConfig(BaseModel):
    """One outbound alert destination (section 20). Structural config only.

    The delivery URL is a **credential** (a Discord/Slack webhook URL is a bearer
    token wearing a URL costume: anyone holding it can post as you), so it never
    appears in this block nor anywhere else in ``config.yaml``. It is read from
    ``ALERT_URLS__<NAME>`` in ``data/secrets.env``, keyed by this ``name``
    uppercased; an optional ``ALERT_TOKENS__<NAME>`` rides along as a bearer
    header for ntfy access tokens and authenticated custom webhooks. A channel
    with no URL configured stays inert -- announced once at startup, then silent.
    """

    # Reject unknown keys instead of dropping them. ``data/config.yaml`` is a
    # TRACKED file, so a user who writes `url: https://discord.com/api/webhooks/...`
    # here would otherwise get a silently inert channel *and* commit a live
    # credential to a public repo. Forbidding extras turns both failures into a
    # startup error that names the key, and catches option typos for free.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=_ALERT_NAME_PATTERN, min_length=1, max_length=64)
    type: Literal["discord", "slack", "ntfy", "webhook"]
    min_severity: Literal["p1", "p2", "p3"] = "p2"
    events: list[str] = Field(default_factory=lambda: list(ALERT_EVENTS))
    timeout_s: float = Field(default=10.0, gt=0, le=120)
    rate_limit_per_min: int = Field(default=10, ge=1, le=600)

    @field_validator("events")
    @classmethod
    def _known_events(cls, value: list[str]) -> list[str]:
        """Reject unknown event names loudly rather than silently never firing."""
        unknown = sorted({e for e in value if e not in ALERT_EVENTS})
        if unknown:
            raise ValueError(
                f"unknown alert event(s) {unknown}; expected any of {list(ALERT_EVENTS)}"
            )
        if not value:
            raise ValueError("events must list at least one of " + ", ".join(ALERT_EVENTS))
        seen: set[str] = set()
        return [e for e in value if not (e in seen or seen.add(e))]


class AlertsConfig(BaseModel):
    """Outbound alert channels (section 20). Off by default, like every integration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    channels: list[AlertChannelConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_names(self) -> "AlertsConfig":
        """Names key the secrets, so a duplicate would silently shadow a channel."""
        names = [c.name for c in self.channels]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"duplicate alert channel name(s): {dupes}")
        return self


class AlertSecrets(BaseModel):
    """Grouped, case-normalised view of the per-channel alert credentials.

    Mirrors :class:`MqttCredentials`: read only from the environment /
    ``data/secrets.env``, never yaml, never code. Keys are lowercased on the way
    in so ``ALERT_URLS__DISCORD_OPS`` and a hand-written lowercase key both
    resolve for a channel named ``discord_ops``, and values are stripped so a
    stray trailing newline in ``secrets.env`` cannot produce an unusable URL.
    """

    urls: dict[str, str] = Field(default_factory=dict)
    tokens: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _normalise(self) -> "AlertSecrets":
        self.urls = {str(k).lower(): str(v).strip() for k, v in self.urls.items() if str(v).strip()}
        self.tokens = {
            str(k).lower(): str(v).strip() for k, v in self.tokens.items() if str(v).strip()
        }
        return self

    def url_for(self, name: str) -> str | None:
        """The delivery URL for a channel, or ``None`` (the channel stays inert)."""
        return self.urls.get(name.lower()) or None

    def token_for(self, name: str) -> str | None:
        """The optional bearer token for a channel, or ``None``."""
        return self.tokens.get(name.lower()) or None


class UnifiCredentials(BaseModel):
    """A grouped, read-only view of the controller credentials."""

    host: str | None = None
    username: str | None = None
    password: str | None = None
    site: str = "default"
    api_key: str | None = None

    @property
    def is_configured(self) -> bool:
        """True when enough is present to attempt a connection."""
        return bool(self.host) and bool(self.api_key or (self.username and self.password))


class _YamlNetadminSource(PydanticBaseSettingsSource):
    """Feed the ``netadmin:`` section of ``data/config.yaml`` into Settings."""

    def __init__(self, settings_cls: type[BaseSettings], yaml_path: Path) -> None:
        super().__init__(settings_cls)
        self._data: dict[str, Any] = self._load(yaml_path)

    @staticmethod
    def _load(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        section = raw.get("netadmin", {}) if isinstance(raw, dict) else {}
        return section if isinstance(section, dict) else {}

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(self._data)


class Settings(BaseSettings):
    """Top-level netadmin configuration.

    Credential fields default to ``None`` so the package imports and tests
    collect without ``data/secrets.env`` present; the ingest layer enforces
    presence before connecting.
    """

    model_config = SettingsConfigDict(
        env_file=str(SECRETS_ENV),
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    # --- controller credentials (from data/secrets.env / environment) ---
    unifi_host: str | None = None
    unifi_username: str | None = None
    unifi_password: str | None = None
    unifi_site: str = "default"
    unifi_api_key: str | None = None

    # --- MQTT broker credentials (section 11; from data/secrets.env / environment,
    #     read as HA_MQTT_HOST / HA_MQTT_PORT / HA_MQTT_USERNAME / HA_MQTT_PASSWORD).
    #     Never yaml, never code — grouped for the HA publisher via ``.mqtt``. ---
    ha_mqtt_host: str | None = None
    ha_mqtt_port: int = 1883
    ha_mqtt_username: str | None = None
    ha_mqtt_password: str | None = None

    # --- outbound alert-channel credentials (section 20; from data/secrets.env /
    #     environment as ``ALERT_URLS__<CHANNEL>`` / ``ALERT_TOKENS__<CHANNEL>``,
    #     filled by pydantic-settings' nested-delimiter support). A webhook URL IS
    #     the credential, so both fields carry ``repr=False``: they are excluded
    #     from ``repr(Settings)`` and can never be leaked by a stray log of the
    #     settings object. Read through the grouped ``.alert_secrets`` view. ---
    alert_urls: dict[str, str] = Field(default_factory=dict, repr=False)
    alert_tokens: dict[str, str] = Field(default_factory=dict, repr=False)

    # --- storage / runtime ---
    db_path: Path = DEFAULT_DB_PATH
    log_dir: Path = DEFAULT_LOG_DIR
    log_level: str = "INFO"
    site_id: str = "default"

    # --- API server (daemon bind + CLI status target; section 12) ---
    server_host: str = "127.0.0.1"
    server_port: int = 8765
    web_dist_path: str | None = None  # built SPA dir; default web/dist relative to cwd
    # Optional static API token. When set (via ``NETADMIN_API_TOKEN`` in
    # ``data/secrets.env`` / the environment — never yaml, never code), every
    # ``/api/*`` route except ``GET /api/health`` and the ``/ws`` socket require it
    # (``Authorization: Bearer <token>`` / ``?token=``, constant-time compared).
    # Unset (the default) means open access with a startup WARNING. Named to match
    # the env var like the other secrets (``unifi_host`` -> ``UNIFI_HOST``); read
    # through the :attr:`api_token` property so callers never touch the raw field.
    netadmin_api_token: str | None = None
    # Optional remote-MCP bearer token (Gitea #29; docs/MCP_SERVER.md's remote
    # streamable-HTTP mount design). Deliberately a SEPARATE credential from
    # ``netadmin_api_token``: the API token authorizes controller *mutations*, so
    # reusing it for a read-only MCP mount would turn a config leaked from one
    # laptop into network control, not just a privacy leak. Read only from
    # ``NETADMIN_MCP_TOKEN`` in ``data/secrets.env`` / the environment -- never
    # yaml, never code, no fallback to ``netadmin_api_token`` -- through the
    # :attr:`mcp_token` property. Consumed by
    # :mod:`netadmin.server.mcp_mount`: unset means ``/mcp`` answers 404 and the
    # feature is simply absent.
    netadmin_mcp_token: str | None = None
    # Pinned CORS origins (section 12); ``*`` is stripped by the server, never
    # allowed. Empty -> the server's localhost dev defaults.
    cors_origins: list[str] = Field(default_factory=list)

    # --- structural config (from data/config.yaml -> netadmin:) ---
    poll: PollIntervals = Field(default_factory=PollIntervals)
    retention: Retention = Field(default_factory=Retention)
    backfill: Backfill = Field(default_factory=Backfill)
    probe: ProbeConfig = Field(default_factory=ProbeConfig)
    detect: DetectConfig = Field(default_factory=DetectConfig)
    correlate: CorrelateConfig = Field(default_factory=CorrelateConfig)
    sle: SleRuntimeConfig = Field(default_factory=SleRuntimeConfig)
    ha: HaConfig = Field(default_factory=HaConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    investigate: InvestigateConfig = Field(default_factory=InvestigateConfig)
    updates: UpdatesConfig = Field(default_factory=UpdatesConfig)

    # WAN plan rate (Mbps), the optional "under load" gate for the bufferbloat
    # detector / SLE. Default ``None`` means "auto": the gate is disabled and
    # bufferbloat stays honestly UNKNOWN. On a Starlink uplink this is the correct
    # default — throughput varies so widely that a fixed plan rate does not
    # represent saturation, and there is no UniFi WAN-throughput series to measure
    # against anyway. Set an explicit value only on a non-Starlink link that exposes
    # ``wan_xput_*`` and has a real provisioned rate. No other WAN detector relies
    # on these keys; latency/loss are judged from rolling probe baselines instead.
    wan_plan_down_mbps: float | None = None
    wan_plan_up_mbps: float | None = None

    # Per-detector / per-classifier threshold overrides, keyed by ``detector_key``
    # (section 6) — e.g. ``{"wired.bad_cable": {"errors_per_min": 20}}``. Detectors
    # ship their own dataclass/literal defaults and read overrides through
    # ``DetectorContext.threshold(key, name, default)``; the SLE classifiers read
    # ``thresholds["sle"]`` and the scorer reads ``thresholds["sle"]["weights"]``.
    thresholds: dict[str, Any] = Field(default_factory=dict)

    @property
    def unifi(self) -> UnifiCredentials:
        """Grouped credential view for the ingest layer."""
        return UnifiCredentials(
            host=self.unifi_host,
            username=self.unifi_username,
            password=self.unifi_password,
            site=self.unifi_site,
            api_key=self.unifi_api_key,
        )

    @property
    def api_token(self) -> str | None:
        """The static API token, or ``None`` for open access (section 12).

        Whitespace-only is treated as unset so a blank line in ``secrets.env``
        does not silently lock the API behind an unusable token.
        """
        token = self.netadmin_api_token
        token = token.strip() if token else None
        return token or None

    @property
    def mcp_token(self) -> str | None:
        """The remote-MCP bearer token, or ``None`` (the feature stays absent).

        Whitespace-only is treated as unset, mirroring :attr:`api_token`, so a
        blank line in ``secrets.env`` never silently arms an unusable token.
        """
        token = self.netadmin_mcp_token
        token = token.strip() if token else None
        return token or None

    @property
    def mqtt(self) -> MqttCredentials:
        """Grouped MQTT-broker credential view for the HA publisher (section 11)."""
        return MqttCredentials(
            host=self.ha_mqtt_host,
            port=self.ha_mqtt_port,
            username=self.ha_mqtt_username,
            password=self.ha_mqtt_password,
        )

    @property
    def alert_secrets(self) -> AlertSecrets:
        """Grouped per-channel alert credential view for the dispatcher (section 20)."""
        return AlertSecrets(urls=dict(self.alert_urls), tokens=dict(self.alert_tokens))

    @model_validator(mode="after")
    def _db_path_env_override(self) -> "Settings":
        """Let ``NETADMIN_DB_PATH`` point the daemon at a different database.

        The generic (prefixless) env name for this field would collide with common
        shell variables, so we read an explicitly-namespaced one here. This is what
        makes ``NETADMIN_DB_PATH=data/netadmin-demo.db netadmin daemon`` work for the
        demo/quickstart and the restore-verify in docs/BACKUP.md, without editing
        data/config.yaml.
        """
        override = os.environ.get("NETADMIN_DB_PATH")
        if override:
            self.db_path = Path(override)
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        yaml_source = _YamlNetadminSource(settings_cls, CONFIG_YAML)
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            yaml_source,
            file_secret_settings,
        )


# --------------------------------------------------------------------------- #
# secrets.env writer (first-run setup; ARCHITECTURE.md 18)
# --------------------------------------------------------------------------- #
# Characters that force a value to be double-quoted so a dotenv parser reads it
# back intact (whitespace, comment marker, quotes, backslash).
_SECRET_QUOTE_TRIGGERS = frozenset(" \t#\"'\\")

# Line-breaking / terminating control characters that can never be represented on
# a single ``KEY=VALUE`` line: a newline or CR would split the value into a second
# (attacker-controlled) assignment when the file is parsed back, and a NUL
# truncates it. Neither the double-quoted nor the bare path can hold these safely,
# so the writer rejects them outright at the security boundary rather than emitting
# a file that a dotenv reader would parse into extra keys. This is the invariant
# "a value with a newline must not inject a second key", enforced at the writer.
_SECRET_FORBIDDEN_CHARS = ("\n", "\r", "\x00")


class SecretValueError(ValueError):
    """A secret value cannot be safely serialised into ``secrets.env``.

    Raised when a value carries a line-breaking / terminating control character
    (see :data:`_SECRET_FORBIDDEN_CHARS`). Carries no value text so the offending
    secret is never surfaced in a traceback or log.
    """


def _reject_forbidden_chars(value: str) -> None:
    """Raise :class:`SecretValueError` if ``value`` holds a forbidden control char.

    The message never includes the value itself (it may be a credential); it names
    only the class of character, so a stack trace can be logged without leaking.
    """
    for ch in _SECRET_FORBIDDEN_CHARS:
        if ch in value:
            names = {"\n": "newline", "\r": "carriage return", "\x00": "NUL"}
            raise SecretValueError(
                f"secret value contains a forbidden {names[ch]} character and cannot "
                "be written to secrets.env"
            )


def _needs_quoting(value: str) -> bool:
    return value == "" or any(ch in _SECRET_QUOTE_TRIGGERS for ch in value)


def _format_env_value(value: str) -> str:
    """Render a value for a dotenv line, double-quoting + escaping when needed.

    Plain values (the common case: a URL host, a URL-safe API key/token) are
    written bare so an existing hand-edited ``secrets.env`` keeps its style; a
    value containing whitespace / ``#`` / quotes (e.g. a password) is
    double-quoted with ``\\`` and ``"`` escaped so python-dotenv round-trips it.

    Values carrying a line-breaking control character (newline / CR / NUL) are
    **rejected** (:class:`SecretValueError`): they cannot be represented on one
    ``KEY=VALUE`` line and would otherwise split into a second assignment when the
    file is parsed back — the secrets-injection boundary this writer guards.
    """
    _reject_forbidden_chars(value)
    if not _needs_quoting(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _split_env_line(line: str) -> Any:
    """Return the KEY of a ``KEY=VALUE`` assignment line, or ``None`` otherwise.

    Comment lines, blank lines, and anything without an ``=`` are preserved
    verbatim (they return ``None`` and are copied through untouched).
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key = stripped.split("=", 1)[0].strip()
    # An ``export KEY=...`` prefix is tolerated for reads; normalise to the bare key.
    if key.startswith("export ") or key.startswith("export\t"):
        key = key.split(None, 1)[1].strip()
    return key or None


def write_secrets(updates: Mapping[str, str], *, path: Path = SECRETS_ENV) -> Path:
    """Merge ``updates`` into ``data/secrets.env`` atomically, chmod 600.

    First-run setup uses this to persist the controller credential and the minted
    UI token (ARCHITECTURE.md 18). Contract:

    * **Creates** the file (and parent dir) when absent, always ``0o600``.
    * **Preserves** every other key, plus comments and ordering; an existing key is
      updated in place, a new key is appended.
    * **Atomic**: written to a temp file in the same directory and ``os.replace``d,
      so a crash never leaves a half-written secrets file.
    * **Never logs values** — this function performs no logging at all.

    Values are written bare when safe and double-quoted when they contain
    whitespace / ``#`` / quotes, so a dotenv reader round-trips them. Returns the
    path written.
    """
    updates = {str(k): str(v) for k, v in updates.items()}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    remaining = dict(updates)
    out_lines: list[str] = []
    if path.exists():
        existing = path.read_text(encoding="utf-8").splitlines()
        for line in existing:
            key = _split_env_line(line)
            if key is not None and key in remaining:
                out_lines.append(f"{key}={_format_env_value(remaining.pop(key))}")
            else:
                out_lines.append(line)
    # Append any keys not already present, in the caller's order.
    for key, value in remaining.items():
        out_lines.append(f"{key}={_format_env_value(value)}")

    body = "\n".join(out_lines) + "\n"

    # Atomic replace via a same-dir temp file created 0600 (mkstemp default).
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".secrets-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        # Best-effort cleanup of the temp file on any failure; never mask the error.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    os.chmod(path, 0o600)
    return path


# --------------------------------------------------------------------------- #
# secrets.env reader — the writer's counterpart, for ONE key (Gitea #35)
# --------------------------------------------------------------------------- #
# A :class:`Settings` instance is a *snapshot*: pydantic-settings reads
# ``env_file`` once, at construction. A long-running daemon therefore keeps
# enforcing whatever ``secrets.env`` said at boot, even after another process
# rewrites it — ``netadmin mcp-token --regenerate``, an operator with an editor,
# a config-management tool. For a credential that is *rotated*, that is a
# security defect and not a papercut: the previous value goes on working until
# the next restart, which is the one thing rotation exists to prevent.
#
# These two helpers are what lets a running process re-evaluate ONE key without
# rebuilding Settings (which would swap live controller credentials, the database
# path and the log destination mid-flight). They deliberately reproduce the
# loader's own rules rather than inventing parallel ones:
#
#   * ``model_config`` sets ``case_sensitive=False``, so both the environment
#     source and the dotenv source lowercase keys before matching a field. Both
#     lookups below are case-insensitive for exactly that reason.
#   * The environment source outranks the dotenv source, and ``env_ignore_empty``
#     is off, so a variable exported as the empty string still beats the file.
#     :func:`env_var_is_set` therefore tests *presence*, never truthiness:
#     whoever exports the variable — a container, a systemd unit — owns that
#     value, and an edit to ``secrets.env`` must not silently override the
#     deployment's own configuration.
#   * The file is parsed with python-dotenv, the very parser
#     ``DotEnvSettingsSource`` uses (and a hard dependency of pydantic-settings,
#     so this adds nothing to the install), so quoting, escaping and ``export``
#     prefixes round-trip exactly the way :func:`write_secrets` intends.


def env_var_is_set(name: str) -> bool:
    """Whether the real process environment defines ``name`` (case-insensitively).

    "Set to the empty string" counts as set, matching the loader: an exported
    variable outranks ``secrets.env`` whatever its value.
    """
    if name in os.environ:
        return True
    wanted = name.lower()
    return any(key.lower() == wanted for key in os.environ)


def read_env_file_secret(name: str, *, path: Path = SECRETS_ENV) -> str | None:
    """Read ONE key back out of a ``secrets.env``-style file, as the loader would.

    Returns the value, or ``None`` when the file does not define the key (or
    defines it blank — whitespace-only is treated as unset, mirroring
    :attr:`Settings.api_token` / :attr:`Settings.mcp_token`, so a stray blank line
    never arms an unusable credential).

    Raises whatever the filesystem raises: ``FileNotFoundError`` when the file is
    gone, ``OSError``/``UnicodeDecodeError`` when it cannot be read or decoded.
    Absence and unreadability are genuinely different answers — one is a
    deliberate state, the other is a malfunction — so this function never
    collapses them into ``None``; the caller decides what each one means.
    """
    text = Path(path).read_text(encoding="utf-8")
    # Parsed from the text we read ourselves rather than by path, so the IO error
    # surfaces here instead of being swallowed by dotenv's own missing-file
    # handling, and so a caller can tell "no such file" from "cannot read it".
    values = dotenv_values(stream=io.StringIO(text))
    wanted = name.lower()
    for key, value in values.items():
        if key is not None and key.lower() == wanted:
            value = value.strip() if value else None
            return value or None
    return None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings singleton (cached)."""
    return Settings()


__all__ = [
    "PROJECT_ROOT",
    "DATA_DIR",
    "CONFIG_YAML",
    "SECRETS_ENV",
    "PollIntervals",
    "Retention",
    "Backfill",
    "ProbeConfig",
    "DetectConfig",
    "CorrelateConfig",
    "SleRuntimeConfig",
    "HaConfig",
    "MqttCredentials",
    "ALERT_EVENTS",
    "AlertChannelConfig",
    "AlertsConfig",
    "AlertSecrets",
    "INVESTIGATE_PROVIDERS",
    "INVESTIGATE_SEVERITIES",
    "AutoInvestigateConfig",
    "InvestigateConfig",
    "UpdatesConfig",
    "UnifiCredentials",
    "Settings",
    "get_settings",
    "write_secrets",
    "read_env_file_secret",
    "env_var_is_set",
    "SecretValueError",
]
