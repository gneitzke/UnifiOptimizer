# Connecting UnifiOptimizer to your controller

UnifiOptimizer needs two things from your UniFi controller: its address, and a
credential that can read device, client, and health stats. On a modern console
that credential is an API key you create in a few clicks. On an older or
self-hosted controller it is a local admin account. This page covers both, plus
the one command that tells you which case you are in.

## Start here: let the tool find the path for you

Ubiquiti moves the API-key screen between firmware versions, and the exact menu
wording differs across UniFi OS 8.x, 9.x, and 10.x. Rather than guess from a
screenshot that may already be stale, ask your own console:

```bash
netadmin detect --host YOUR-CONTROLLER
```

`detect` is read-only and signs in to nothing. It opens one connection to the
address you give it, reads back the console model and, when the console exposes
it without a login, its Network application version, then prints the exact menu
path to the API-key screen for *that* device. Many UniFi OS consoles (a CloudKey
Gen2+, for instance) do not reveal their Network version until you sign in; when
`detect` cannot read it, it still recommends the API-key path — the right route
for any current console — and says so, rather than guessing you are too old for
keys. Only a version it actually reads as older than 9.0 sends you to the
admin-account route below. Point it at whatever you would type into a browser:

```bash
netadmin detect --host https://192.168.1.1        # a gateway (UDM, UCG, ...)
netadmin detect --host https://192.168.1.20        # a CloudKey or UniFi OS Server
netadmin detect --host https://unifi.example.lan:8443   # legacy software controller
```

If `detect` prints "Create the API key" steps — whether it read your version as
9.0+ or could not read it at all — follow that printed path and skip to [What
goes in secrets.env](#what-goes-in-secretsenv). Only if it prints "Set up
authentication" with an admin-account path (an older or legacy controller) go to
[Older and self-hosted controllers](#older-and-self-hosted-controllers).

## Creating an API key (UniFi OS consoles)

Every UniFi OS console creates keys the same way, because they all run the same
UniFi OS underneath. The device model changes where the console sits on your
network, not how you make the key.

1. Sign in to the console's local web UI as an admin (the address you gave
   `detect`, not `unifi.ui.com`).
2. Open **Settings → Control Plane → Integrations**. On some builds the section
   sits under **Settings → System → Integrations** instead; if you do not see it
   at all, the console is running a Network version too old for API keys (see
   below).
3. Click **Create API Key**, give it a name you will recognize later
   (`unifioptimizer`), and confirm.
4. Copy the key immediately. UniFi shows it once and never again. If you lose it,
   delete that key and make a new one.

The key inherits the permissions of the admin who created it, so create it while
signed in as a full admin. A read-only admin's key cannot see the stats and
events UnifiOptimizer collects.

### Which console you have

The steps above are identical across the UniFi OS lineup. This table is only to
confirm your device runs UniFi OS and can make keys at all.

| Console | API key support | Notes |
|---|---|---|
| CloudKey Gen2 / Gen2+ (UCK-G2, UCK-G2-Plus) | Yes | Update the CloudKey firmware if Integrations is missing. |
| Dream Machine (UDM, UDM-Pro, UDM-SE) | Yes | Gateway address is usually `https://192.168.1.1`. |
| Dream Router (UDR, UDR7) | Yes | Same path; the Network app runs on the router itself. |
| Dream Wall (UDW) | Yes | Same path. |
| Cloud Gateway (UCG-Ultra, UCG-Max, Cloud Gateway Fiber) | Yes | Same path. |
| Express (UX, Express 7) | Yes | Same path. |
| UniFi OS Server (software UniFi OS on your own host/VM) | Yes | Reach it at its host address, port 443. |
| Legacy self-hosted UniFi Network Application | No | Docker/Linux/Windows install with no UniFi OS wrapper. Use an admin account. |

### Minimum version, and how to check yours

API keys arrived with UniFi OS 4.x and the Network application 9.0 release; the
Integrations screen appears once both are recent enough. To read your version,
open **Settings → Control Plane → Updates** and note the Network application
number. UnifiOptimizer needs Network 9.x regardless, because several of the
stats endpoints it reads only exist there, so if `detect` reports an older
version, update the console before going further.

One point of confusion worth naming: `unifi.ui.com` also offers an API key,
under **Settings → API Keys** in the Site Manager cloud portal. That is a
different, cloud-proxied key for Ubiquiti's hosted API, not the local one
UnifiOptimizer uses. Create the key on the console's own web UI, and pair it with
that console's local address.

## Older and self-hosted controllers

Two kinds of controller have no API key: consoles on a Network version older
than 9.0, and the legacy self-hosted UniFi Network Application (the package you
install yourself on Linux, Windows, or Docker, without UniFi OS around it). Both
authenticate with a local admin account instead.

Create a dedicated account rather than reusing your own:

1. Open **Settings → Admins** (self-hosted) or **Settings → Admins & Users**
   (UniFi OS), and add an admin.
2. Give it the **Admin** role. A view-only role cannot read the stats and events
   UnifiOptimizer needs.
3. Leave two-factor authentication (2FA/MFA) **off** for this account.
   UnifiOptimizer signs in non-interactively and cannot answer a 2FA prompt, so a
   2FA-protected admin fails at login.
4. Leave remote/cloud access unchecked, so the credential stays on your LAN.

Put that account's username and password in `secrets.env` (below).
UnifiOptimizer signs in with it and keeps the session cookie for the life of the
process; it logs in sparingly, because some consoles rate-limit repeated logins.

## What goes in secrets.env

Credentials live in `data/secrets.env`. The file is gitignored and should be
`chmod 600`; nothing in it is ever committed.

With an API key (preferred):

```ini
UNIFI_HOST=https://192.168.1.1
UNIFI_API_KEY=your-api-key
UNIFI_SITE=default
```

With an admin account (fallback):

```ini
UNIFI_HOST=https://unifi.example.lan:8443
UNIFI_USERNAME=unifioptimizer
UNIFI_PASSWORD=...
UNIFI_SITE=default
```

`UNIFI_HOST` is the same address you gave `detect`, scheme and all. UniFi OS
consoles answer on port 443, so the port is usually implicit
(`https://192.168.1.1`). A legacy software controller answers on 8443, so include
it (`https://host:8443`). `UNIFI_SITE` is `default` unless you renamed your site;
its real identifier is the short slug in the controller URL
(`.../manage/site/<slug>/...`), not the display name.

Set the key or the account, not both. Once `secrets.env` is in place, confirm the
connection with a read-only visit before running the daemon:

```bash
netadmin visit --lookback-days 1
```

If that pulls back your devices and clients, the daemon will too.
