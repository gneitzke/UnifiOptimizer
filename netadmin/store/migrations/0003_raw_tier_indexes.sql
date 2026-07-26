-- netadmin store schema, migration 0003.
-- Two raw-tier read paths in Repository were doing unindexed scans that grow
-- without bound on a long-running install (docs/ARCHITECTURE.md section 4:
-- "a gap must be queryable, never inferred"; events are deduped per insert).
--
-- 1. poll_runs is queried by (job, ts) on every coverage/gap lookup and pruned
--    by ts; without an index each call full-scans an append-only table.
-- 2. record_event() dedupes with `SELECT 1 FROM events WHERE native_id=?` on
--    every insert. A partial index (only rows that carry a native_id -- WS
--    frames often lack one and must never be deduped) keeps that lookup O(log n)
--    without indexing the many NULLs.
CREATE INDEX IF NOT EXISTS idx_poll_runs_job_ts ON poll_runs(job, ts);
CREATE INDEX IF NOT EXISTS idx_events_native_id ON events(native_id) WHERE native_id IS NOT NULL;
