# Connecting to the remote MCP endpoint

This page is client setup: the exact commands and config files for pointing a
Claude client at `/mcp` on a running daemon. For what the endpoint is and how
it is gated, see [`MCP_SERVER.md`](MCP_SERVER.md) section 8. For the 11 tools
themselves, see [`MCP_REFERENCE.md`](MCP_REFERENCE.md).

Use this page when your Claude client is on a different machine from the
daemon. If they are the same machine, `netadmin-mcp` over stdio (also
documented in `MCP_REFERENCE.md`) needs no network setup at all, and it keeps
answering after the daemon stops.

## Before you start

On the daemon host:

```bash
pip install "unifioptimizer[mcp]"    # the daemon needs the extra too, not just the client
netadmin mcp-token --regenerate      # mints a token, writes NETADMIN_MCP_TOKEN to data/secrets.env
```

Restart the daemon so it mounts the endpoint, then note its URL:
`http://<daemon-host>:8765/mcp` (8765 is the default port; adjust if you run
the daemon on a different one). Everything below plugs a client into that URL
with the token in an `Authorization: Bearer` header.

That restart is only for the first token. Once the endpoint is mounted, a
later `netadmin mcp-token --regenerate` applies on the daemon's next `/mcp`
request: the old token starts getting 401s immediately, without a restart and
without the daemon rereading anything else it was configured with. Deleting the
`NETADMIN_MCP_TOKEN` line takes effect the same way, and turns `/mcp` back into
a 404. The one exception is a `NETADMIN_MCP_TOKEN` exported into the daemon's
environment (containers, systemd units): that wins over `data/secrets.env`, as
it does at startup, so rotate it wherever the deployment sets it.

`netadmin mcp-token` with no flag prints the current token without minting a
new one, if you need it again later.

Note the phrase "on the daemon host". Reading or rotating this token is only
open to the machine the daemon runs on. From anywhere else the two routes that
hand it out (`GET /api/system/mcp-token` and its regenerate) require the API
token, even on an install with no API token configured, where they simply refuse
every remote caller. Ordinary reads stay open on the LAN as always; this
credential does not, because it opens the whole history store, and a guest
device rotating it would silently break every client you have set up.

Or skip the CLI: the web UI's Settings page has a "Remote MCP token" section
beside the access token, with Reveal and Regenerate buttons and a ready-to-copy
`claude mcp add` command for whatever token is currently on screen. Rotating
there behaves exactly like the CLI: immediate if remote MCP is already running,
and only turning it on for the first time needs the restart. The same access
rule applies there too: a browser on the daemon host can reveal and rotate
freely, and one on another machine is asked for the API token first.

If you put the daemon behind a reverse proxy, set `NETADMIN_TRUST_PROXY=1` so
the rate limiter reads `X-Forwarded-For` instead of bucketing every client
under the proxy's own address. Leave it unset otherwise. Without a proxy in
front, that header is attacker-supplied, and honouring it lets a caller pick a
fresh bucket per attempt and guess the token without ever hitting the limit.

## Claude Code

### One command

```bash
claude mcp add --transport http unifioptimizer http://<daemon-host>:8765/mcp \
  --header "Authorization: Bearer <token>"
```

This adds the server to local scope: available in whatever project directory
you ran the command from. Since this is a network tool, not a project one,
add `--scope user` to make it available from any directory on this machine
instead.

### `.mcp.json`, without the token in the file

Project-scoped config lives in `.mcp.json` at the repo root, and unlike the
`local`/`user` scopes above, it is meant to be shared or committed. Paste the
token directly into it and it ends up in your shell history and possibly your
git log. Reference an environment variable instead. Claude Code expands
`${VAR}` (and `${VAR:-default}`) inside `.mcp.json` at connect time, so the
file itself never holds the secret:

```json
{
  "mcpServers": {
    "unifioptimizer": {
      "type": "http",
      "url": "http://<daemon-host>:8765/mcp",
      "headers": {
        "Authorization": "Bearer ${NETADMIN_MCP_TOKEN}"
      }
    }
  }
}
```

`NETADMIN_MCP_TOKEN` then needs to be set wherever Claude Code runs (your
shell profile, or a local `.env` you source before launching it). It is the
same variable name the daemon itself reads, so one export covers both.

## Claude Desktop

### The custom-connector UI

Settings > Connectors > Add custom connector takes a URL and, under advanced
settings, an OAuth client ID and secret. It does not take a static bearer
header, and the connector runs from Anthropic's cloud infrastructure rather
than from your Mac, so it cannot reach a private LAN address regardless. It
is the right tool if you have put `/mcp` behind a public reverse proxy with
real OAuth in front of it; it is not the path for a daemon sitting on your
home network, which is the setup this project assumes. The reverse-proxy
recipe is in `MCP_SERVER.md` section 8 if you want to go that route yourself.

### The fallback that actually works here: `npx mcp-remote`

`claude_desktop_config.json` only launches local stdio processes; it has no
native HTTP entry the way Claude Code's `.mcp.json` does. `mcp-remote` is a
small stdio-to-HTTP bridge that fills that gap: Desktop starts it as a local
subprocess, which is what lets it reach a LAN address at all, and it attaches
the header a static-token server needs.

Edit `claude_desktop_config.json` (macOS: `~/Library/Application
Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "unifioptimizer": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote@latest",
        "http://<daemon-host>:8765/mcp",
        "--header",
        "Authorization: Bearer ${NETADMIN_MCP_TOKEN}",
        "--allow-http",
        "--transport",
        "http-only"
      ],
      "env": {
        "NETADMIN_MCP_TOKEN": "<token>"
      }
    }
  }
}
```

Two flags matter beyond the header. `--allow-http` is required because this
endpoint has no TLS (see the reverse-proxy note above if you want it); by
default `mcp-remote` refuses plain `http://` to non-localhost hosts.
`--transport http-only` skips a wasted SSE fallback attempt, since `/mcp`
speaks streamable HTTP, not SSE. Restart Claude Desktop after saving.

## When something does not connect

| Symptom | Cause | Fix |
|---|---|---|
| Connection refused | The daemon is not listening there: either it is stopped, or its port is bound to loopback only (the Docker Compose default). | Check `netadmin status` or `docker compose logs` on the daemon host. If the port is loopback-bound, tunnel it: `ssh -L 8765:localhost:8765 <daemon-host>`, then point the client at `http://localhost:8765/mcp`. If the daemon really is down, only the stdio server still answers, because it opens the database file directly and does not need the daemon process running at all. |
| `401 Unauthorized` | The bearer token does not match `NETADMIN_MCP_TOKEN`, or no `Authorization` header was sent. A token that worked until a moment ago means someone rotated it; the daemon stops accepting the old one on the next request, restart or no restart. | Run `netadmin mcp-token` on the daemon host to confirm the current value, and check the header for a stray space or an old token still cached in a client config. |
| `404 Not Found` | No `NETADMIN_MCP_TOKEN` is configured, so `/mcp` is not mounted at all. | Run `netadmin mcp-token --regenerate` on the daemon host and restart it. |
| `429 Too Many Requests` | More than 10 failed auth attempts from this client in 60 seconds. | Wait out the `Retry-After` window, then fix the token before retrying. |
| `503 Service Unavailable` | The token is right, but the mount is not serving: either the daemon host does not have the `mcp` extra installed, or the token was added after the daemon started, so the endpoint was never mounted. The response body says which. | `pip install "unifioptimizer[mcp]"` on the daemon host if that is what it names, then restart it. Either way this one needs the restart. |

## Not supported: phones and claude.ai

Both the mobile apps and the claude.ai web connector dispatch from Anthropic's
own cloud infrastructure, not from a device on your network, so they cannot
reach `http://<daemon-host>:8765/mcp` no matter how the token is configured.
The same is true of Claude Desktop's custom-connector UI, which is why that
path is marked out of scope above. Reaching this endpoint from any of them
would mean putting it on the public internet with real TLS and OAuth in
front, which is deliberately not this project's v1 (`MCP_SERVER.md` section
8, "Not in v1"). Claude Code and Claude Desktop's local config file are the
supported clients, because both run the connecting process on a machine that
can actually see your LAN.
