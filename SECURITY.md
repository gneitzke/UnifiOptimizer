# Security Policy

## What this tool is

UnifiOptimizer is a local-first network administration tool. It runs on your own
machine (a Mac mini, a Pi, or a small server), talks to a UniFi controller on
your own network, and writes everything it collects to a local SQLite file
(`data/netadmin.db`). It has no cloud backend, no telemetry, and no phone-home.
Nothing leaves your network unless you configure an optional integration
(Home Assistant MQTT) or hand an issue dossier to an external LLM yourself.

## Credentials handling

- Controller credentials (API key, or username/password) and the JWT signing
  secret live **only** in `data/secrets.env` (chmod 600), or in the macOS
  Keychain where available. This file is gitignored and must never be committed.
- No credential is ever written to `data/netadmin.db`, to any tracked config
  file, or to a generated report.
- The web UI never stores controller credentials in browser storage. The
  browser holds only a short-lived JWT (7-day expiry, `SameSite` cookie).
- Controller access in the current build is **read-only**: GETs plus a small set
  of documented read-query POSTs. The fix/apply engine, when enabled, never acts
  on its own — every change requires an explicit action in the UI or CLI and
  captures a full before-state for one-click revert.

## Running safely

- Bind the daemon to `127.0.0.1` (the default). Do not expose port 8765 to an
  untrusted network without putting it behind your own authenticated proxy.
- CORS is pinned to configured origins; a wildcard is never allowed.
- Use a dedicated, revocable controller API key rather than an admin password
  where your controller supports it.
- Rotate the controller credential if it may have been reused or exposed.

## Reporting a vulnerability

If you find a security issue, please report it privately rather than opening a
public issue.

- Contact: please report privately through this repository's **Security** tab →
  **Report a vulnerability** (GitHub private security advisories). Do not open a
  public issue for a suspected vulnerability.
- Please include: affected version/commit, reproduction steps, and impact.
- This is a personal open-source project with no formal SLA. Expect a
  best-effort acknowledgement; fixes ship on a best-effort basis.

Please do not include real credentials, controller hostnames, or packet
captures in a report. Sanitize before sending.
