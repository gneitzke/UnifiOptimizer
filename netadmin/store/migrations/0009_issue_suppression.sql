-- netadmin store schema, migration 0009: operator issue suppression (Gitea #49).
-- Applied by the runner in netadmin/store/db.py, which gates on PRAGMA
-- user_version (this file -> 9) and runs it in one BEGIN IMMEDIATE transaction.
--
-- Adds the three columns operator suppression is DERIVED from at read time
-- (netadmin/issues/suppression.py): when it was suppressed, an optional expiry,
-- and the severity captured at that moment (so an escalation past it voids the
-- mute by derivation, with no engine write). All three are additive and
-- nullable, so every existing row reads as never-suppressed with no backfill.
--
-- Why this also carries live snoozes into suppression. Until this release
-- `snooze_until_ts` was documented as a notification mute but consulted by
-- nothing (grep-verified: the only reader was the issue detail page's caption).
-- Suppression is the mute snooze was always meant to be, and it subsumes snooze:
-- the UI's Snooze control becomes a timed Suppress. Migrating live snoozes means
-- an operator who snoozed something yesterday still has it muted after upgrade,
-- rather than having their mute silently evaporate. Note this GROWS the blast
-- radius of a migrated mute — a snooze muted nothing before; as a suppression it
-- now shrinks counts, drops HA sensors, and gates alerts, and gains
-- escalation-void — but that is the mute the operator believed they had.
--
-- `snooze_until_ts` is left in place as history; the UI simply stops rendering
-- it (the "Snoozed until" caption retires with the Snooze button). The engine
-- `snooze` method and its endpoint stay (pinned by tests) but the UI no longer
-- calls them.
--
-- First-ALTER-TABLE note: the runner's _iter_statements strips `--` line
-- comments then splits on `;`. That is correct only while no `;` or `--` sits
-- inside a string literal — this file keeps both out of the SQL strings below.

ALTER TABLE issues ADD COLUMN suppressed_ts INTEGER;
ALTER TABLE issues ADD COLUMN suppress_until_ts INTEGER;
ALTER TABLE issues ADD COLUMN suppressed_severity TEXT;

-- The audit trail first, while the WHERE still matches, so the event is written
-- for exactly the set the UPDATE below suppresses (0005/0006/0008 precedent).
-- `source: migration` distinguishes these from operator-initiated suppressions.
INSERT INTO issue_events (issue_id, ts, kind, detail)
SELECT id,
       CAST(strftime('%s', 'now') AS INTEGER),
       'suppressed',
       json_object('source', 'migration',
                   'until_ts', snooze_until_ts,
                   'severity', severity)
FROM issues
WHERE state != 'resolved'
  AND snooze_until_ts IS NOT NULL
  AND snooze_until_ts > CAST(strftime('%s', 'now') AS INTEGER);

UPDATE issues
SET suppressed_ts = CAST(strftime('%s', 'now') AS INTEGER),
    suppress_until_ts = snooze_until_ts,
    suppressed_severity = severity
WHERE state != 'resolved'
  AND snooze_until_ts IS NOT NULL
  AND snooze_until_ts > CAST(strftime('%s', 'now') AS INTEGER);
