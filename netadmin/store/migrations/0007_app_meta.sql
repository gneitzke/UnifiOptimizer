-- netadmin store schema, migration 0007: app_meta key/value cache.
-- Applied by the runner in netadmin/store/db.py, which gates on PRAGMA
-- user_version (this file -> 7).
--
-- A generic, schemaless small cache for daemon-level facts that are cheap to
-- recompute but should survive a restart. The first tenant is the self-update
-- version check (docs/ARCHITECTURE.md section 23): the last known latest
-- published version and when it was last checked, so a restart shows the last
-- known answer immediately instead of "unknown" until the next tick. Any
-- future daemon-level fact that needs to persist without a schema change can
-- land here as a new key -- this table is deliberately untyped beyond the key.
CREATE TABLE IF NOT EXISTS app_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
