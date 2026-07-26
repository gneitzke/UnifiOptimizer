-- netadmin store schema, migration 0002.
-- Index the reopen lookup. Repository.get_recent_resolved_issue filters resolved
-- issues by (fingerprint, resolved_ts); the issue engine (docs/ARCHITECTURE.md
-- section 7, reopen window) calls it on every new-fingerprint fire. Without this
-- index that lookup full-scans the whole resolved history -- which prune() never
-- touches -- so per-cycle cost grows without bound on a long-running install.
CREATE INDEX IF NOT EXISTS idx_issues_fp_resolved ON issues(fingerprint, resolved_ts);
