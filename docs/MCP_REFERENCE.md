# MCP tool reference

The complete reference for `netadmin-mcp`, the read-only MCP server over
UnifiOptimizer's history store. For why it exists and how it is built, see
[`MCP_SERVER.md`](MCP_SERVER.md); this page is what to actually call.

Every tool is read-only. The server opens the SQLite file with `mode=ro` and
`PRAGMA query_only`, imports nothing from the fix, ingest or config layers, and
holds no controller credentials. It cannot change your network or your database.

There are two ways to reach it, with the same 11 tools behind both.

| | `netadmin-mcp` (stdio) | `/mcp` on the daemon (HTTP) |
|---|---|---|
| Runs where | the machine your Claude client is on | the machine the daemon is on |
| Needs the daemon running | no, it reads the file directly | yes |
| Setup | install the package and point it at the database | one token, one URL, nothing installed locally |

Use stdio when you want an answer after a crash. Use the HTTP mount when Claude
is on a different machine from the daemon.

## Install: stdio

```bash
pip install "unifioptimizer[mcp]"

# Claude Code
claude mcp add unifioptimizer -- netadmin-mcp --db /path/to/data/netadmin.db
```

Claude Desktop, in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "unifioptimizer": {
      "command": "netadmin-mcp",
      "args": ["--db", "/path/to/data/netadmin.db"]
    }
  }
}
```

The database path resolves from `--db`, then `NETADMIN_DB_PATH`, then
`NETADMIN_DATA_DIR/netadmin.db`, then `./data/netadmin.db`. Claude Desktop launches
processes with `/` as the working directory, so pass `--db` or set the environment
variable rather than relying on the relative default.

## Install: the daemon's `/mcp` endpoint

On the machine running the daemon, mint a token and restart:

```bash
pip install "unifioptimizer[mcp]"    # the daemon needs the extra too
netadmin mcp-token --regenerate      # writes NETADMIN_MCP_TOKEN to data/secrets.env
```

Then, on any machine that can reach it:

```bash
claude mcp add --transport http unifioptimizer http://<daemon-host>:8765/mcp \
  --header "Authorization: Bearer <token>"
```

`NETADMIN_MCP_TOKEN` is a separate credential from `NETADMIN_API_TOKEN` and there
is no fallback between them. The API token can apply fixes to your network; this
one can only read history, and you can rotate it without touching anything else.
Keep it off the public internet: there is no TLS here, so put a reverse proxy in
front of the daemon if the link is not one you already trust.

Without a token configured, `/mcp` returns 404. A wrong token returns 401, and
repeated failures from one client are rate limited.

Minting and reading that token is a local operation. The routes behind the CLI
and the Settings buttons serve the daemon's own host unauthenticated and ask
every other machine for the API token, so a device on the LAN cannot read the
credential or rotate it out from under your clients.

For the `.mcp.json` form that keeps the token out of the file, the Claude
Desktop setup, and a full table of what each of those error codes means, see
[`MCP_REMOTE.md`](MCP_REMOTE.md).

## Shared parameters

Most tools accept a time window and a row cap. They are optional everywhere.

| Parameter | Meaning |
|---|---|
| `window` | Relative window such as `24h`, `7d`, `30d`. Defaults per tool, usually `24h`. |
| `start` / `end` | Explicit ISO-8601 or epoch bounds. Override `window` when given. |
| `limit` | Row cap. Default 20, hard maximum 50. A clipped response sets `truncated` and reports the true `total`. |

Schemas are strict (`additionalProperties: false`), so a misspelled parameter is
rejected rather than silently ignored.

Every response leads with a `summary`: one or two plain sentences that usually
answer the question on their own. Series are downsampled to at most 96 points and
timestamps are ISO-8601 UTC, so a reply stays cheap in context.

## The tools

### `netadmin_overview`
**Start here.** Open issues and incidents, the health score, and how it moved
against the previous window.
*Parameters:* `window`, `start`, `end`, `limit`.
*Reach for it when:* "How is the network doing?"

### `netadmin_when_did_this_start`
The flagship. Onset time, what was normal before it, what changed just before
onset, and whether it has happened before.
*Parameters:* `issue` (id, or an entity plus detector), `window`.
*Reach for it when:* "When did the Loft AP start dropping clients?" No live-query
tool can answer this, because the controller has already discarded the evidence.

### `netadmin_has_this_happened_before`
Every past occurrence of an issue's fingerprint, how long each lasted, and which
fixes were applied.
*Parameters:* `issue` or `fingerprint`.
*Reach for it when:* "Is this the same problem as last month, and what fixed it?"

### `netadmin_issues`
Tracked issues: a filtered list, or one issue in full with its lifecycle trail,
evidence, investigations and fixes tried.
*Parameters:* `issue`, `state`, `severity`, `open_only`, `limit`.
*Reach for it when:* "What is open right now, worst first?"

### `netadmin_incidents`
Correlated incidents, each grouping one root cause with the symptom issues it
explains.
*Parameters:* `incident`, `open_only`, `limit`.
*Reach for it when:* "Are these six complaints actually one fault?"

### `netadmin_sle_trend`
Per-bucket service-level score and failed client-minutes across a window, with the
direction of travel.
*Parameters:* `sle` (coverage, capacity, connect, roaming, wan, infra), `bucket`,
`window`.
*Reach for it when:* "Is roaming getting worse or is today just bad?"

### `netadmin_what_changed`
One merged timeline of firmware, channel and link-state changes, applied or
reverted fixes, and controller events.
*Parameters:* `window`, `start`, `end`, `limit`.
*Reach for it when:* "What changed just before things broke?"

### `netadmin_worst_offenders`
Devices or clients ranked by attributed failed client-minutes, open issues and
event churn.
*Parameters:* `surface` (`devices` or `clients`), `window`.
*Reach for it when:* "Which AP is costing my users the most?"

### `netadmin_metric_history`
One entity's metric over time, downsampled, next to that series' learned baseline.
*Parameters:* `metric`, entity reference, `window`.
*Reach for it when:* "Show me the Barn AP's uplink RSSI for the last week."

### `netadmin_events_around`
Events near a timestamp or an issue's onset, grouped by kind with exemplars.
*Parameters:* `at` (timestamp), `issue`, `radius`.
*Reach for it when:* "What else broke at 02:14?"

### `netadmin_client_experience`
One client's story: service-level breakdown, disconnects and roams, which APs it
moved between, and its RSSI trend.
*Parameters:* entity reference, `window`.
*Reach for it when:* "Why is my laptop bad in the kitchen?"

## Entity references

Tools that take an entity accept an entity id, a MAC, or a name. An ambiguous name
returns the candidate list rather than a guess, so you can disambiguate in a second
call.

## Privacy

The store holds client MACs and hostnames, and tool output goes to whichever model
you have configured. That is metadata you already see in your own controller, so
nothing is redacted by default. Set `NETADMIN_MCP_REDACT=1` to mask client MACs to
their OUI and replace hostnames with stable pseudonyms; entity ids survive, so
drill-down still works.

## Running alongside a controller MCP server

This server deliberately ships no live-controller tools. It pairs with one: a
controller server gives an assistant hands on the present, and this gives it memory
of the past. Load both and the assistant routes by itself, because every tool
description here begins with "History:" and says it reads the local store.
