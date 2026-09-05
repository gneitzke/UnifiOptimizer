-- C5: incident membership is historical, not a snapshot of currently-open issues.
-- joined_ts records when correlation first attached the issue; cleared_ts records
-- when a later pass no longer considered it a current member.  Existing rows were
-- necessarily present by their incident's first observation, which is the safest
-- deterministic backfill for databases created before this migration.

ALTER TABLE incident_members ADD COLUMN joined_ts INTEGER;
ALTER TABLE incident_members ADD COLUMN cleared_ts INTEGER;

UPDATE incident_members
SET joined_ts = (
  SELECT first_seen_ts FROM incidents WHERE incidents.id = incident_members.incident_id
)
WHERE joined_ts IS NULL;

CREATE INDEX IF NOT EXISTS idx_incident_members_current_issue
ON incident_members(issue_id, cleared_ts);

PRAGMA user_version = 10;
