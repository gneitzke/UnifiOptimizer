# Backup and restore

UnifiOptimizer keeps everything in one SQLite file, `data/netadmin.db` (WAL mode).
That file is the entire history: every metric, event, issue lifecycle, incident,
SLE minute, and applied change. Backing it up is one command, and restoring is a
file copy. Nothing else needs backing up except `data/secrets.env` (your
credentials) and `data/config.yaml` (your settings), and those you already have.

## Back up while the daemon is running

Do not just `cp` the database while the daemon is writing to it — WAL mode means a
plain copy can miss committed pages. Use SQLite's online backup, which is
consistent against a live writer:

```bash
sqlite3 "file:data/netadmin.db?mode=ro" ".backup /path/to/backups/netadmin-$(date +%F).db"
```

That produces a single self-contained file with no `-wal`/`-shm` companions.

### Nightly, unattended

A cron entry (Linux) or a launchd job (macOS) that keeps two weeks of daily
snapshots:

```cron
# 03:30 daily; keep 14 days. Adjust the paths.
30 3 * * * cd /opt/UnifiOptimizer && sqlite3 "file:data/netadmin.db?mode=ro" ".backup /var/backups/netadmin/netadmin-$(date +\%F).db" && find /var/backups/netadmin -name 'netadmin-*.db' -mtime +14 -delete
```

On the Mac mini deployment the database lives in `~/netadmin-data/netadmin.db`;
point the backup there and write the snapshot outside the container's volume.

Also copy `data/secrets.env` and `data/config.yaml` somewhere safe once (they
rarely change). Keep `secrets.env` out of any backup that leaves your control — it
holds credentials.

## Restore

1. Stop the daemon (so nothing is writing).
2. Move the current database aside, then put the backup in its place:

   ```bash
   mv data/netadmin.db data/netadmin.db.corrupt   # keep it for inspection
   rm -f data/netadmin.db-wal data/netadmin.db-shm # stale WAL from the old file
   cp /path/to/backups/netadmin-2026-07-22.db data/netadmin.db
   ```

3. Start the daemon. On startup it runs any pending schema migrations against the
   restored file and backfills the gap from the controller's own retained stats
   (5-minute data for roughly the last day, hourly for a week), so a short outage
   between the backup and the restore mostly fills itself back in.

## Verify a restore before you need it

Test the restore path at least once, on a copy, so an emergency is not the first
time you run it:

```bash
cp /path/to/backups/netadmin-2026-07-22.db /tmp/restore-test.db
NETADMIN_DB_PATH=/tmp/restore-test.db python3 -m netadmin.cli status --json
```

If that prints a health payload with your entity counts, the backup is good.
