-- netadmin store schema, migration 0004: correlation incidents.
-- Verbatim from docs/ARCHITECTURE.md section 17. Applied by the runner in
-- netadmin/store/db.py, which gates on PRAGMA user_version (this file -> 4).
-- IF NOT EXISTS is belt-and-suspenders; the runner is the real idempotency
-- guarantee. An incident groups open issues that share one root cause: exactly
-- one member is the root (the thing to fix), the rest are symptoms that clear
-- when it clears. Issue lifecycle is untouched -- incident_id is a join onto the
-- issue read model, never a stored column on issues.

-- One incident per root. fingerprint = sha1(root issue fingerprint), so the
-- identity is stable across correlation passes as long as the same root persists.
CREATE TABLE IF NOT EXISTS incidents (
  id INTEGER PRIMARY KEY,
  fingerprint TEXT NOT NULL,            -- sha1(root issue fingerprint) -- stable identity across passes
  root_issue_id INTEGER NOT NULL REFERENCES issues(id),
  severity TEXT NOT NULL,              -- max severity across members (p1 | p2 | p3)
  state TEXT NOT NULL,                 -- open | resolved (resolved when the root is no longer produced)
  first_seen_ts INTEGER NOT NULL, last_seen_ts INTEGER NOT NULL, resolved_ts INTEGER,
  title TEXT NOT NULL,                 -- plain-language root-cause line
  summary TEXT NOT NULL DEFAULT ''     -- "Weak backhaul on Back Porch Mesh is causing 1 coverage hole + 2 client dropouts"
);
-- Mirrors idx_issues_open_fp: at most one *open* incident per root fingerprint,
-- enforced by the store so a re-run updates in place instead of duplicating.
CREATE UNIQUE INDEX IF NOT EXISTS idx_incidents_open_fp ON incidents(fingerprint) WHERE state != 'resolved';

CREATE TABLE IF NOT EXISTS incident_members (
  incident_id INTEGER NOT NULL REFERENCES incidents(id),
  issue_id INTEGER NOT NULL REFERENCES issues(id),
  role TEXT NOT NULL,                  -- root | symptom
  rule TEXT NOT NULL,                  -- the correlation rule that linked it (e.g. "mesh_uplink->coverage_hole:same_entity")
  rationale TEXT NOT NULL,             -- one human line: why this symptom is attributed to this root
  PRIMARY KEY (incident_id, issue_id)
);
-- The issue read model gains incident_id/incident_role via a join on this column,
-- so the lookup "which incident is this issue part of" is indexed.
CREATE INDEX IF NOT EXISTS idx_incident_members_issue ON incident_members(issue_id);
