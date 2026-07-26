# UnifiOptimizer add-on

Runs the UnifiOptimizer daemon on your Home Assistant host. It polls your UniFi
controller read-only, keeps the history the controller discards, and turns it
into tracked issues.

## Install

1. Settings, Add-ons, Add-on store.
2. Three-dot menu, Repositories, add `https://github.com/gneitzke/UnifiOptimizer`.
3. Install UnifiOptimizer from the list that appears.

## Before it is reachable

The add-on ships with no port published. Reads on its API are unauthenticated,
and Home Assistant publishes a mapped add-on port on every interface, so opening
one is a decision you make rather than a default you inherit.

Open the Configuration tab, set the host port for `8765/tcp`, and save. Use
`8765` unless something else on the host already has it. The dashboard is then
at `http://<home-assistant-host>:8765/`.

There is no ingress. The dashboard is a single-page app that requests absolute
`/api` and `/ws` paths, which resolve against the Home Assistant root rather
than the add-on when proxied under an ingress prefix. Enabling it today would
serve a blank page. It needs a runtime base path in the frontend first.

## Options

| Option | Default | What it does |
| --- | --- | --- |
| `log_level` | `info` | Daemon log verbosity. |
| `api_token` | empty | Gates fix apply and revert over HTTP. Without it those endpoints are refused and reads still work. |
| `controller_host` | empty | Controller URL, for example `https://192.168.1.1`. Skips the web setup flow. |
| `controller_api_key` | empty | Controller API key. Use with `controller_host`. |

Leaving the two controller options empty is the normal path. Start the add-on,
open the dashboard, and the first-run setup writes credentials to
`/data/secrets.env` for you.

## Storage

Everything mutable lives in the add-on's `/data` directory: `secrets.env`,
`config.yaml`, the SQLite database, and the logs. `NETADMIN_DATA_DIR` points
there. Home Assistant preserves it across add-on updates, and it is included in
Home Assistant backups.

## Safety

The daemon talks to your controller with GETs and a small set of documented read
queries. Nothing is changed on your network unless you click apply on a fix, and
that path is refused entirely until you set `api_token`.
