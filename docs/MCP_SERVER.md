# MCP Server (`netadmin/mcp/`)

The authoritative design for the read-only MCP server that exposes the history
store to the user's own Claude client. This document is the architecture as
agreed; the implementation in `netadmin/mcp/` follows it.

**Component:** `netadmin/mcp/`, a read-only MCP server exposing the history store
to the user's own Claude client. Positioning: competing UniFi MCP servers are
"Claude's hands on a live controller that keeps ~1 day of stats"; this is
"Claude's memory".

## 1. Transport / topology

Separate stdio process that opens the SQLite store DIRECTLY read-only. Console
script `netadmin-mcp`, MCP over stdio. Rationale: stdio is the Claude
Desktop/Code default; the daemon may not be running (post-crash "what happened
last night" is exactly a memory question); WAL makes direct reads safe with the
daemon as sole writer.

- Extend `netadmin/store/db.py:connect` with `read_only: bool = False`: open
  `file:{path}?mode=ro` with `uri=True`, apply `PRAGMA query_only=ON`, skip the
  `journal_mode` pragma. Give `Repository.open` a matching `read_only=True` that
  forces `migrate=False`.
- Startup gate: `db.schema_version()` must equal the newest migration number.
  Older DB -> "run `netadmin` once to migrate"; newer -> "pip install -U
  unifioptimizer". Emit on stderr AND return from tool calls.
- `netadmin/mcp/tools.py` is transport-agnostic: plain functions
  `(Repository, params) -> dict`. `netadmin/mcp/server.py` binds them to the MCP
  SDK over stdio.

## 2. Tools (11), prefix `netadmin_`

Conventions: `entity` params accept entity_id, MAC or name (resolve via existing
`list_entities`/`get_entity`; ambiguity returns a candidate list, never a guess).
Windows are strings (`"24h"`, `"7d"`, `"30d"`) or explicit ISO start/end. Every
returned ID is accepted by drill-down tools.

1. `netadmin_overview` - entry point: open issues, incidents, SLE headline, trend
   vs prior window, collector health. (`list_issues`, `list_incidents`,
   `sle_scores`, `read_poll_runs`)
2. `netadmin_when_did_this_start` - FLAGSHIP: onset time, baseline vs now, what
   changed near onset, prior occurrences. (`get_issue`, `list_issue_events`,
   `get_baselines`, rollups, `list_changes`, `query_events`)
3. `netadmin_has_this_happened_before` - recurrence by fingerprint: past issues,
   durations, what fixed them. (NEW `list_issue_history(fingerprint)`,
   `list_issue_events`, `list_changes`)
4. `netadmin_issues` - list or one issue: lifecycle trail, evidence,
   investigations, fixes tried. (`list_issues`/`get_issue`, `list_issue_events`,
   `list_investigations`, `list_changes`)
5. `netadmin_incidents` - correlated incidents (root cause + symptoms), list or
   one. (`list_incidents`/`get_incident`, `list_incident_members`)
6. `netadmin_sle_trend` - "is it getting worse": per-bucket SLE score +
   fail-minutes with direction. (`query_sle_minutes` grouped by bucket,
   `sle_scores`)
7. `netadmin_what_changed` - config/state/fix timeline: firmware, channel, link
   flaps, applied/reverted fixes, admin events. (NEW
   `list_state_changes(start,end)`, `list_changes`, `query_events`)
8. `netadmin_worst_offenders` - top-N entities by attributed fail-minutes, issue
   count, event count.
9. `netadmin_metric_history` - bucketed series + baseline for one entity metric.
   (`get_series`, `read_raw`/rollups, `get_baselines`)
10. `netadmin_events_around` - "what else broke at the same time": events near a
    ts, grouped by key with exemplars. (`query_events`/`read_events`)
11. `netadmin_client_experience` - one client's story: SLE breakdown,
    roams/disconnects, AP history, RSSI trend.

Add the two new Repository read methods (`list_issue_history`,
`list_state_changes`) in the store module (keep SQL in the store per the
architecture rule). Reuse `netadmin.sle.scores.sle_scores` (pure over
`query_sle_minutes` rows). Everything else uses existing reads - VERIFY each
exists before calling.

## 3. Output discipline

Enforced centrally in `tools.py`, not per tool:

- Every response leads with `summary`: at most two plain-English sentences.
- Row caps: default 20, hard max 50. Clipped -> `truncated: true` and `total`.
- Series never raw: auto-pick raw/hourly/daily so a series is <=96 points,
  emitted as `[iso_ts, value]` plus min/avg/max.
- ISO-8601 UTC timestamps, with an `ago` companion ("4d 2h") on headline fields
  only.
- `evidence` JSON trimmed to top-level scalar keys, no nested dumps.
- Response-size guard: truncate any output over ~24KB with a note.

## 4. Naming

Server name `unifioptimizer`, tool prefix `netadmin_`. Every tool DESCRIPTION
starts with "History:" and contains "from the local UnifiOptimizer history store;
works even when the controller has forgotten or the daemon is down" - that
sentence is what steers Claude's routing when a live-controller MCP server is
also loaded. Descriptions state read-only explicitly. Ship no live-state twins
(no `list_devices`-style tools).

## 5. Packaging

`pyproject`: optional extra `mcp = ["mcp>=1.2"]`; script
`netadmin-mcp = "netadmin.mcp.server:main"`. Core install stays at 11 runtime
deps. `main()` imports the SDK lazily; if absent exit 1 with
`pip install "unifioptimizer[mcp]"` on stderr. DB discovery precedence: `--db`
flag, then `NETADMIN_DB_PATH`, then `NETADMIN_DATA_DIR/netadmin.db`, then
`./data/netadmin.db`. Resolve paths with a SMALL helper, NOT full `Settings`: it
must never load `secrets.env`, so controller credentials never enter this
process.

## 6. Safety

Read-only enforced three ways, none by convention: (a) SQLite `mode=ro`, (b)
`PRAGMA query_only=ON`, (c) the mcp package imports nothing from the fix/ingest
layers and holds no credentials. Privacy: default NO redaction (the operator is
the network owner and this is metadata they already see); opt-in
`NETADMIN_MCP_REDACT=1` masks client MACs to OUI-only and hostnames to stable
pseudonyms (`client-7f3a`) while keeping `entity_id`s so drill-downs still work.

## 7. Testing

Under `tests/netadmin/mcp/`: logic tests call `tools.py` functions directly
against a temp store seeded via `Repository` (reuse existing store fixtures;
`netadmin.demo.seed` for a populated scenario). Protocol tests (initialize,
list_tools, call flagship) use the SDK's in-memory streams guarded by
`pytest.importorskip("mcp")`. Invariant tests: read-only sweep (EVERY tool
against a `mode=ro` store, no write errors - this also catches read paths that
lazily write); caps/truncation honored; a fresh EMPTY db answers honestly ("no
data yet") on every tool; schema-version mismatch produces the guidance error;
redaction masks every MAC and hostname field.
