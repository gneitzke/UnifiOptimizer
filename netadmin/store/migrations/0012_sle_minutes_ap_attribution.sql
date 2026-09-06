-- netadmin store schema, migration 0012: preserve per-sample AP attribution.
--
-- A client can roam during a bucket, leaving two otherwise-identical SLE cells
-- that differ only by the AP owning each sample. Rebuild the WITHOUT ROWID
-- table so that AP identity participates in its primary key. SQLite requires
-- every WITHOUT ROWID primary-key component to be non-null, so 0 is the stable
-- internal key for pre-existing unattributed (NULL) rows; repository readers
-- expose that sentinel as NULL.

CREATE TABLE sle_minutes_new (
  bucket_ts INTEGER NOT NULL,
  sle TEXT NOT NULL,
  classifier TEXT NOT NULL,
  entity_id INTEGER NOT NULL,
  attributed_entity_id INTEGER NOT NULL,
  minutes REAL NOT NULL,
  PRIMARY KEY (bucket_ts, sle, classifier, entity_id, attributed_entity_id)
) WITHOUT ROWID;

INSERT INTO sle_minutes_new
  (bucket_ts, sle, classifier, entity_id, attributed_entity_id, minutes)
SELECT bucket_ts, sle, classifier, entity_id, COALESCE(attributed_entity_id, 0), minutes
FROM sle_minutes;

DROP TABLE sle_minutes;
ALTER TABLE sle_minutes_new RENAME TO sle_minutes;
