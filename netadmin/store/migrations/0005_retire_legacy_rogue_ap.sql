-- netadmin store schema, migration 0005: retire the legacy wifi.rogue_ap issues.
-- Applied by the runner in netadmin/store/db.py, which gates on PRAGMA
-- user_version (this file -> 5) and runs it in one BEGIN IMMEDIATE transaction.
--
-- Why a data migration and not an age-out. Until this release wifi.rogue_ap
-- emitted one issue per neighbour BSS, with the channel inside the fingerprint,
-- so every neighbour that changed channel opened a second issue. A dense site
-- accumulated well over a hundred open rows describing ordinary suburban air.
-- The taxonomy now splits: neighbour noise aggregates into wifi.neighbor_density
-- (one issue per band), and wifi.rogue_ap keeps only genuine security claims,
-- fingerprinted on subtype rather than channel.
--
-- Letting the old rows age out would cost six more days of flood (K=6 daily
-- clears), fire one resolve transition per row into alerts, the LLM
-- auto-investigator and Home Assistant -- a notification storm -- and record a
-- falsehood in the audit trail: "the problem went away". It did not; the
-- taxonomy changed. So every legacy row is retired here, in one silent step,
-- with the honest reason attached. Every legacy fingerprint is obsolete
-- (channel-in-dims), including any genuine rogue: the new detector re-fires those
-- under new fingerprints on the next daily pass, confirming on the first fire.
-- Because no legacy fingerprint can ever be produced again, the engine's 24 h
-- reopen window cannot resurrect a retired row.

-- The audit trail first, while the rows are still open, so the event is written
-- for exactly the set the UPDATE below retires.
INSERT INTO issue_events (issue_id, ts, kind, detail)
SELECT id,
       CAST(strftime('%s', 'now') AS INTEGER),
       'resolved',
       '{"reason": "superseded: neighbour noise aggregated into wifi.neighbor_density (migration 0005)"}'
FROM issues
WHERE detector_key = 'wifi.rogue_ap' AND state != 'resolved';

UPDATE issues
SET state = 'resolved',
    resolved_ts = CAST(strftime('%s', 'now') AS INTEGER),
    clear_streak = 0
WHERE detector_key = 'wifi.rogue_ap' AND state != 'resolved';

-- An incident rooted on a retired issue is stale the moment its root resolves.
-- The correlation pass only rebuilds incidents whose root is still produced, so
-- close them here rather than leave a resolved root advertised as an open
-- incident in the report and the API.
UPDATE incidents
SET state = 'resolved',
    resolved_ts = CAST(strftime('%s', 'now') AS INTEGER)
WHERE state != 'resolved'
  AND root_issue_id IN (SELECT id FROM issues WHERE detector_key = 'wifi.rogue_ap');
