-- netadmin store schema, migration 0006: retire the per-radio plan-level
-- wifi.channel_plan issues. Applied by the runner in netadmin/store/db.py, which
-- gates on PRAGMA user_version (this file -> 6) and runs it in one
-- BEGIN IMMEDIATE transaction.
--
-- Why a data migration and not an age-out. Until this release wifi.channel_plan
-- reported two plan-level defects once per radio. co_channel_reuse fired from
-- every end of a conflict, so one physical clash became one issue per member;
-- and because a band with more radios than non-overlapping channels must reuse
-- some of them, an assignment that was already optimal was reported as a
-- permanent, unfixable defect. wide_channel_dense_5ghz repeated one width policy
-- decision once per 80 MHz radio. A six-radio site carried eighteen open rows
-- describing perhaps three decisions.
--
-- Both now aggregate into one site-scoped issue per band on the rf_env
-- pseudo-entity (rf:2.4 / rf:5), fired only when the reuse is actually
-- avoidable. Every legacy fingerprint (RADIO entity + one of these two subtypes)
-- is therefore obsolete: it can never be produced again, so the engine's 24 h
-- reopen window cannot resurrect a retired row, and the band-scoped issues fire
-- fresh on the next daily pass.
--
-- Letting the old rows age out would cost six more days of flood (K=6 daily
-- clears), fire one resolve transition per row into alerts, the LLM
-- auto-investigator and Home Assistant, and record a falsehood in the audit
-- trail: "the problem went away". It did not; the unit of issue changed.
--
-- channel_off_grid and wide_channel_24ghz rows are deliberately untouched. They
-- are per-radio defects with per-radio fixes, their fingerprints are still
-- produced by the detector unchanged, and their lifecycle (and any fix already
-- applied against them) continues across this migration.

-- The audit trail first, while the rows are still open, so the event is written
-- for exactly the set the UPDATE below retires.
INSERT INTO issue_events (issue_id, ts, kind, detail)
SELECT id,
       CAST(strftime('%s', 'now') AS INTEGER),
       'resolved',
       '{"reason": "superseded: plan-level channel findings aggregated per band (migration 0006)"}'
FROM issues
WHERE detector_key = 'wifi.channel_plan'
  AND state != 'resolved'
  AND json_extract(evidence, '$.subtype') IN ('co_channel_reuse', 'wide_channel_dense_5ghz');

UPDATE issues
SET state = 'resolved',
    resolved_ts = CAST(strftime('%s', 'now') AS INTEGER),
    clear_streak = 0
WHERE detector_key = 'wifi.channel_plan'
  AND state != 'resolved'
  AND json_extract(evidence, '$.subtype') IN ('co_channel_reuse', 'wide_channel_dense_5ghz');

-- An incident rooted on a retired issue is stale the moment its root resolves.
-- The correlation pass only rebuilds incidents whose root is still produced, so
-- close them here rather than leave a resolved root advertised as an open
-- incident in the report and the API.
UPDATE incidents
SET state = 'resolved',
    resolved_ts = CAST(strftime('%s', 'now') AS INTEGER)
WHERE state != 'resolved'
  AND root_issue_id IN (
    SELECT id FROM issues
    WHERE detector_key = 'wifi.channel_plan'
      AND json_extract(evidence, '$.subtype') IN ('co_channel_reuse', 'wide_channel_dense_5ghz')
  );
