-- netadmin store schema, migration 0008: retire the per-(client, AP)
-- wifi.sticky_client issues. Applied by the runner in netadmin/store/db.py, which
-- gates on PRAGMA user_version (this file -> 8) and runs it in one
-- BEGIN IMMEDIATE transaction.
--
-- Why a data migration and not an age-out. Until this release the sticky
-- fingerprint carried the client's current AP as a dim, so a weak client that
-- bounced between two far APs minted one issue per AP it landed on. Both rows
-- stayed open -- the client kept returning to each AP inside the K=6 clear
-- window and refiring that fingerprint -- and since a client is only ever on one
-- AP, at any instant one of the two rows described an attachment the detector no
-- longer claimed. A sticky client is a property of the client ("this thing will
-- not roam"); the AP it is glued to is evidence, and evidence refreshes on every
-- refire. The detector now emits dims={} (Gitea #40), so every fingerprint that
-- carries an ap dim is obsolete: it can never be produced again, and the
-- engine's 24 h reopen window therefore cannot resurrect a retired row.
--
-- SQL cannot recompute the sha1, so the old rows cannot be rewritten in place;
-- this follows the retire-don't-age-out precedent of migration 0006 for the same
-- reasons. Letting them age out would fire ~90 minutes of spurious resolve
-- transitions (K=6 at the 15-minute WINDOW cadence) into alerts, Home Assistant
-- and the LLM auto-investigator, and would write a falsehood into the audit
-- trail: "the problem went away". It did not; the unit of issue changed.
--
-- Accepted cost, as in 0006: a client that is genuinely still sticky re-confirms
-- as a fresh row in ~45 minutes (M=3 at the WINDOW cadence), losing its
-- first-seen age and occurrence count. Any incident rooted on a retired row is
-- closed here too, so symptoms that survive under it re-mint as fresh incidents
-- on the next correlation pass rather than hanging off a resolved root.
--
-- Every non-resolved sticky row is retired, including the theoretical legacy row
-- whose dims were already empty (the detector wrote dims={} when the client's
-- current AP was unknown). Distinguishing it in SQL is impossible -- the dims
-- live inside the hash and nowhere else -- and retiring it is harmless: its
-- fingerprint is exactly what the new scheme produces, so a refire inside the
-- 24 h reopen window reopens that same row with its history intact.

-- The audit trail first, while the rows are still open, so the event is written
-- for exactly the set the UPDATE below retires.
INSERT INTO issue_events (issue_id, ts, kind, detail)
SELECT id,
       CAST(strftime('%s', 'now') AS INTEGER),
       'resolved',
       '{"reason": "superseded: sticky client re-scoped per client (migration 0008)"}'
FROM issues
WHERE detector_key = 'wifi.sticky_client'
  AND state != 'resolved';

UPDATE issues
SET state = 'resolved',
    resolved_ts = CAST(strftime('%s', 'now') AS INTEGER),
    clear_streak = 0
WHERE detector_key = 'wifi.sticky_client'
  AND state != 'resolved';

-- An incident rooted on a retired issue is stale the moment its root resolves.
-- The correlation pass only rebuilds incidents whose root is still produced, so
-- close them here rather than leave a resolved root advertised as an open
-- incident in the report and the API.
UPDATE incidents
SET state = 'resolved',
    resolved_ts = CAST(strftime('%s', 'now') AS INTEGER)
WHERE state != 'resolved'
  AND root_issue_id IN (
    SELECT id FROM issues WHERE detector_key = 'wifi.sticky_client'
  );
