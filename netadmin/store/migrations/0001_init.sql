-- netadmin store schema, migration 0001.
-- Verbatim from docs/ARCHITECTURE.md section 4. Applied once by the runner in
-- netadmin/store/db.py, which gates on PRAGMA user_version (this file -> 1).
-- IF NOT EXISTS is belt-and-suspenders so re-running the raw file is harmless;
-- the runner is the real idempotency guarantee.

-- Inventory. entity_type: ap | switch | gateway | client | port | radio | wlan
CREATE TABLE IF NOT EXISTS entities (
  entity_id   INTEGER PRIMARY KEY,
  site_id     TEXT NOT NULL DEFAULT 'default',
  entity_type TEXT NOT NULL,
  native_id   TEXT NOT NULL,          -- MAC for devices/clients, "<sw_mac>:<port_idx>" for ports, "<ap_mac>:<radio>" for radios
  parent_id   INTEGER REFERENCES entities(entity_id),   -- port -> switch, radio -> ap, client -> current ap/switch
  name        TEXT, model TEXT,
  first_seen_ts INTEGER NOT NULL, last_seen_ts INTEGER NOT NULL,
  meta        TEXT NOT NULL DEFAULT '{}',               -- JSON: oui, fingerprint, capabilities, is_wired...
  UNIQUE (site_id, entity_type, native_id)
);

-- Discrete state history: firmware version, link speed, up/down, channel, ip, uplink type...
CREATE TABLE IF NOT EXISTS state_changes (
  id INTEGER PRIMARY KEY, entity_id INTEGER NOT NULL REFERENCES entities(entity_id),
  attr TEXT NOT NULL, old_value TEXT, new_value TEXT, ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_state_entity_ts ON state_changes(entity_id, ts);

-- Interned series dimension: one row per (entity, metric)
CREATE TABLE IF NOT EXISTS series (
  series_id INTEGER PRIMARY KEY,
  entity_id INTEGER NOT NULL REFERENCES entities(entity_id),
  metric TEXT NOT NULL, unit TEXT,
  UNIQUE (entity_id, metric)
);

-- Raw samples. Counters stored as computed deltas (rate), not cumulative values.
CREATE TABLE IF NOT EXISTS samples (
  series_id INTEGER NOT NULL, ts INTEGER NOT NULL, value REAL NOT NULL,
  PRIMARY KEY (series_id, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS samples_hourly (
  series_id INTEGER NOT NULL, bucket_ts INTEGER NOT NULL,
  n INTEGER NOT NULL, min REAL, max REAL, avg REAL, sum REAL, last REAL,
  PRIMARY KEY (series_id, bucket_ts)
) WITHOUT ROWID;

-- samples_daily: identical shape to samples_hourly, bucket = UTC day
CREATE TABLE IF NOT EXISTS samples_daily (
  series_id INTEGER NOT NULL, bucket_ts INTEGER NOT NULL,
  n INTEGER NOT NULL, min REAL, max REAL, avg REAL, sum REAL, last REAL,
  PRIMARY KEY (series_id, bucket_ts)
) WITHOUT ROWID;

-- Normalized event log (WS + stat/event catch-up, deduped by controller event _id when present)
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, ts INTEGER NOT NULL,
  key TEXT NOT NULL,                   -- EVT_WU_Roam, EVT_SW_PoeOverload, ...
  entity_id INTEGER REFERENCES entities(entity_id),
  related_entity_id INTEGER REFERENCES entities(entity_id),   -- roam: from-AP; port event: client...
  native_id TEXT, msg TEXT, data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_entity_ts ON events(entity_id, ts);

-- Collector accounting: gaps must be queryable, never inferred
CREATE TABLE IF NOT EXISTS poll_runs (
  ts INTEGER NOT NULL, job TEXT NOT NULL, ok INTEGER NOT NULL,
  duration_ms INTEGER, error TEXT, source TEXT NOT NULL DEFAULT 'live'  -- live | backfill
);

-- Issue lifecycle (see section 7)
CREATE TABLE IF NOT EXISTS issues (
  id INTEGER PRIMARY KEY,
  fingerprint TEXT NOT NULL,           -- sha1(detector_key + entity native_id + salient dims)
  detector_key TEXT NOT NULL, entity_id INTEGER REFERENCES entities(entity_id),
  severity TEXT NOT NULL,              -- p1 | p2 | p3
  state TEXT NOT NULL,                 -- pending | active | resolving | resolved
  first_seen_ts INTEGER NOT NULL, last_seen_ts INTEGER NOT NULL, resolved_ts INTEGER,
  clear_streak INTEGER NOT NULL DEFAULT 0, occurrences INTEGER NOT NULL DEFAULT 1,
  ack_ts INTEGER, snooze_until_ts INTEGER,
  title TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '{}',   -- JSON: latest supporting metrics
  fix_state TEXT,                      -- proposed | applied | verified | failed
  reopened_from INTEGER REFERENCES issues(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_issues_open_fp ON issues(fingerprint) WHERE state != 'resolved';

CREATE TABLE IF NOT EXISTS issue_events (
  id INTEGER PRIMARY KEY, issue_id INTEGER NOT NULL REFERENCES issues(id),
  ts INTEGER NOT NULL, kind TEXT NOT NULL,   -- detected | escalated | acked | snoozed | fix_proposed | fix_applied | fix_verified | fix_failed | resolved | reopened | investigated
  detail TEXT NOT NULL DEFAULT '{}'
);

-- SLE accounting (see section 8)
CREATE TABLE IF NOT EXISTS sle_minutes (
  bucket_ts INTEGER NOT NULL,          -- 5-minute bucket
  sle TEXT NOT NULL,                   -- coverage | roaming | capacity | connect | wan | infra
  classifier TEXT NOT NULL,            -- 'ok' or the failure classifier
  entity_id INTEGER NOT NULL,          -- the client (or device for infra)
  attributed_entity_id INTEGER,        -- the AP/port/cable the failure is pinned on
  minutes REAL NOT NULL,
  PRIMARY KEY (bucket_ts, sle, classifier, entity_id)
) WITHOUT ROWID;

-- Applied config changes (replaces data/change_history.json; keeps revert)
CREATE TABLE IF NOT EXISTS changes (
  id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, issue_id INTEGER REFERENCES issues(id),
  entity_id INTEGER, action TEXT NOT NULL,
  before_json TEXT NOT NULL, after_json TEXT NOT NULL,
  status TEXT NOT NULL,                -- applied | reverted | failed
  reverted_ts INTEGER
);

-- EWMA / rolling-quantile state per series (and per hour-bucket where seasonal)
CREATE TABLE IF NOT EXISTS baselines (
  series_id INTEGER NOT NULL, bucket TEXT NOT NULL,   -- 'all' or 'h00'..'h23' (+ 'we'/'wd' suffix if needed)
  stat TEXT NOT NULL,                  -- ewma_mean | ewma_var | p05 | p50 | p95
  value REAL NOT NULL, updated_ts INTEGER NOT NULL,
  PRIMARY KEY (series_id, bucket, stat)
) WITHOUT ROWID;

-- LLM investigations (section 10)
CREATE TABLE IF NOT EXISTS investigations (
  id INTEGER PRIMARY KEY, issue_id INTEGER NOT NULL REFERENCES issues(id),
  ts INTEGER NOT NULL, provider TEXT NOT NULL,        -- manual | copilot | anthropic
  dossier_md TEXT NOT NULL, response_md TEXT, status TEXT NOT NULL  -- pending | answered
);
