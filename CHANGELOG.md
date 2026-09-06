# Changelog

All notable changes to UnifiOptimizer are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.8.1] — 2026-09-06

A reliability release. Six failures found by running the daemon against a real
network for a week — not by the test suite — where a large, long-lived store
exposed scheduling and coercion edges that small fixtures never reach. All are
backward compatible.

### Fixed

- The daily detection pass (`detect_daily`) crashed every night with
  `ValueError: 'rogue_bss' is not a valid EntityType`. The rogue-AP scanner stores
  neighbour BSSes as `rogue_bss` inventory rows that are deliberately not an
  `EntityType`; the daily config audit enumerates every entity type and choked
  coercing the first such row. The detector's entity view now skips inventory-only
  rows (and logs any genuinely unknown type) instead of aborting the whole pass.
- The same crash had a second site in the issue-engine reconciliation step that
  only running the real daily pass against a live store surfaced: the
  "Foreign AP broadcasting our SSID" issues are keyed to those `rogue_bss` rows,
  so the parentage-inhibition lookup (`get_entity`) coerced the type and failed
  the pass *after* every detector had already produced findings. An issue
  legitimately references its `rogue_bss` entity, so the lookup now returns the
  row with its raw type (as the collector stores it) rather than excluding it.
- `sle_minutes` and `anomalies` silently stopped running on a busy daemon. Both
  jobs were being dropped by APScheduler's 1-second misfire window whenever a
  detect/correlate pass held the single event loop a few seconds past their fire
  time — on a large store, every run. Jobs now carry a generous misfire grace, so
  a slipped run executes once when the loop frees instead of vanishing.
- `retention_prune` reported `UNKNOWN` in `/api/health` forever: the nightly prune
  ran but never recorded a `poll_runs` row, so health could not tell a prune that
  ran from one that never fired. It now records each run.
- `/api/health` now reports *why* a job is failing (`last_error` / `last_failure_ts`
  from the most recent failed run), and the dashboard's source-health panel shows
  that reason inline. Previously the surface said a job was "failing" but the cause
  was only readable by shelling into the host and querying the database.
- The container image pinned `mcp>=1.2` with no upper bound, so a fresh build
  resolved mcp 2.0 and the `/mcp` mount died at import (`'Server' object has no
  attribute 'list_tools'`, a 503). Pinned to `mcp>=1.2,<2` to match the packaging
  extra until the server is ported to the 2.x API.

## [0.8.0] — 2026-09-06

A safety and correctness release. Every controller-facing and incident-facing
path was audited and hardened; the changes are backward compatible (the CLI, API,
and on-disk layout are unchanged, and the schema upgrades in place on first run).

### Added

- `netadmin doctor --offline` — a store-only health check that never touches the
  controller: it reports whether the database is present and migratable, the schema
  version, whether credentials are configured, the age of the last successful poll
  per collector, and any detected collection gaps, with stable exit codes for
  scripting.
- Incident UI: fix outcomes now render their real state (`applying` / `failed` /
  `unknown` / `applied` / `reverted`) with per-step results; the timeline and
  collector strip show where the collectors had no visibility (so a gap never
  reads as "nothing happened"); a resolved incident shows how long it lasted and
  preserves its whole story including cleared symptoms; the change ledger is
  filterable and its revert control opens a real approval diff.

### Fixed

Controller safety
- Data collection is strictly GET-only; report/event/session reads that used POST
  are GET or reported unavailable, and only the approved, revertible fix writer
  ever issues a non-GET.
- A mutation is never auto-retried: an uncertain outcome (lost response, connection
  reset, unparseable or timing-out reply) is reported as *unknown*, never replayed
  and never mislabeled as failed or applied.
- Authenticated controller requests no longer follow cross-origin redirects, so the
  API key cannot leak to another host; concurrent auth failures honor the
  controller's `Retry-After` cooldown.

Fixes you apply
- One-way actions (PoE power-cycle, min-RSSI removal) are advisory — surfaced as a
  recommendation, never executed — because they cannot be reverted.
- A revert restores only the fields the original change touched, rebuilt from fresh
  live state, and refuses if the device drifted; apply and revert serialize per
  device and re-check eligibility, so concurrent operators cannot clobber each other.
- Verification arms only for confirmed-successful steps; a failed apply is no longer
  recorded as applied.

History you can trust
- An incident keeps its full membership history (append-only, with joined/cleared
  timestamps) while its current severity and symptom counts reflect only what is
  still open — consistently across the dashboard, issue pages, MCP, and the LLM
  dossier.
- Event-based detectors freeze to *unknown* during an event-feed outage instead of
  falsely clearing a real issue; event catch-up and metric backfill track observed
  coverage so a gap is retried rather than silently lost.
- Four wired root-cause rules (port flapping, bad cable, STP loop, broadcast storm)
  that never fired in production now do, because feeder-port topology derived from
  GET inventory reaches the correlation graph.

Accuracy
- Historical SLE minutes attribute to the AP a client was actually on at the time,
  split correctly across roams.
- Metric history uses the real retention clock and aligns retention tiers so each
  interval is counted exactly once.
- First-run setup is serialized, so concurrent requests cannot overwrite
  configuration or disclose the owner's token.

### Changed

- Schema migrations `0010`–`0013` run automatically on first start (incident
  membership history, the event-source coverage ledger, per-AP SLE attribution, and
  event-repair fairness). Upgrades are forward-only and preserve existing data.

[0.8.0]: https://github.com/gneitzke/UnifiOptimizer/releases/tag/v0.8.0
