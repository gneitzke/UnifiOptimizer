-- B4/new-bug: the ingest-coverage ledger (completed controller history reads +
-- unrecoverable gaps) was previously created lazily inside repository methods via
-- CREATE TABLE IF NOT EXISTS.  That put DDL on a READ path: on a migrated database
-- that never wrote coverage, a read-only connection calling observed_event_coverage
-- raised OperationalError instead of returning unknown coverage.  Creating the
-- table here, at migration time, means every reader can assume it exists (and, for
-- a read-only replica, tolerate its absence without ever issuing DDL).
--
-- IF NOT EXISTS keeps this safe for databases where an earlier build already
-- created the table lazily; the schema is identical to that lazy definition.

CREATE TABLE IF NOT EXISTS ingest_coverage (
  kind TEXT NOT NULL,
  scope TEXT NOT NULL,
  interval TEXT NOT NULL,
  start_ts INTEGER NOT NULL,
  end_ts INTEGER NOT NULL,
  status TEXT NOT NULL,
  detail TEXT,
  updated_ts INTEGER NOT NULL,
  PRIMARY KEY (kind, scope, interval, start_ts, end_ts)
);

CREATE INDEX IF NOT EXISTS idx_ingest_coverage_lookup
ON ingest_coverage(kind, scope, interval, status, start_ts, end_ts);

PRAGMA user_version = 11;
