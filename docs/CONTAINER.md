# Running in a container

Two supported paths beyond `pip install`: Docker (Compose or plain `docker run`)
and a Home Assistant add-on. Both run the same daemon as the pip install, with
the same read-only controller access.

No image is published to a registry yet, so both paths build locally. The
Compose build takes about ten seconds on a warm Docker cache.

---

## Docker Compose

From a clone of the repository:

```bash
git clone https://github.com/gneitzke/UnifiOptimizer.git
cd UnifiOptimizer
docker compose up -d
```

Then open <http://127.0.0.1:8765/> and complete the first-run setup, which
writes your controller credentials into the data volume.

`docker-compose.yml` is deliberately not the shortest file that would work.
Three of its choices matter:

**The port is published to loopback only.** `127.0.0.1:8765:8765`, not
`8765:8765`. Reads on the API are unauthenticated by design, so the short form
would publish your full network inventory to everyone on the LAN. For remote
access, put a reverse proxy that authenticates in front of it.

**Storage is a named volume.** The image runs as a non-root user, uid 501 by
default. Docker seeds a new named volume from the image with that ownership
already correct, on any host, so there is no uid bookkeeping for you to get
wrong. `secrets.env`, `config.yaml`, `netadmin.db`, and the logs all live in it
and survive `docker compose pull && docker compose up -d`.

**`NETADMIN_DATA_DIR` is set explicitly.** `/app/data` is also where the default
cwd-relative lookup lands, but pinning it means an upgrade cannot quietly start
writing somewhere else.

### Everyday commands

```bash
docker compose logs -f              # follow the daemon log
docker compose restart              # restart after a config change
docker compose down                 # stop; the named volume survives
docker compose down -v              # stop and DELETE the database
docker compose up -d --build        # rebuild after pulling new code
```

### Reaching files in the volume

```bash
docker compose exec netadmin cat /app/data/secrets.env
docker compose cp netadmin:/app/data/netadmin.db ./netadmin-backup.db
```

### Enabling fix apply over HTTP

Reads work without a token. Every mutating endpoint is refused until one is set,
which is the safe default. To enable them, uncomment `NETADMIN_API_TOKEN` in
`docker-compose.yml` and put the value in a gitignored `.env` next to it:

```bash
echo "NETADMIN_API_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" >> .env
docker compose up -d
```

### If you want the data on the host instead

A bind mount only works when the host directory is writable by the container's
uid. Build the image for your own uid and gid, then mount:

```bash
docker compose build --build-arg APP_UID="$(id -u)" --build-arg APP_GID="$(id -g)"
```

and replace the `volumes:` block with `- ./data:/app/data`. Create `./data`
yourself first, otherwise Docker creates it owned by root and the daemon cannot
write. On Docker Desktop for macOS this is unnecessary: the file sharing layer
maps ownership for you.

---

## Plain `docker run`

Same result without Compose. Build once, then run:

```bash
docker build -f Dockerfile.netadmin -t unifioptimizer:local .

docker run -d --name unifioptimizer --restart unless-stopped \
  -p 127.0.0.1:8765:8765 \
  -v netadmin-data:/app/data \
  -e NETADMIN_DATA_DIR=/app/data \
  unifioptimizer:local
```

The same three rules apply: loopback-only publish, named volume,
`NETADMIN_DATA_DIR` pinned. `docker logs -f unifioptimizer` follows the log.

For a multi-architecture build, `./deploy/build-multiarch.sh` builds arm64 and
amd64 from the same Dockerfile and verifies both manifests are present.

---

## Home Assistant add-on

The add-on lives in `addon/`. `repository.yaml` at the repository root is what
makes the GitHub URL usable as an add-on repository.

1. Settings, Add-ons, Add-on store.
2. Three-dot menu, Repositories, add `https://github.com/gneitzke/UnifiOptimizer`.
3. Install UnifiOptimizer, then open its Configuration tab and set a host port
   for `8765/tcp`.
4. Start it and open `http://<home-assistant-host>:<port>/`.

`addon/DOCS.md` is the user-facing page the add-on store renders, including the
options table.

### How it is built

`addon/Dockerfile` installs the published `unifioptimizer` wheel from PyPI at a
pinned version rather than building from this source tree. The wheel already
carries the compiled dashboard at `netadmin/_webui/`, so building from source
would only add Node to the image to rebuild something that is already built.
The pinned version, `addon/config.yaml`'s `version`, and
`pyproject.toml`'s `version` are held equal by
`tests/netadmin/test_container_packaging.py`.

`addon/build.yaml` maps each architecture to a Home Assistant Python base image.
Those are Alpine, so every wheel has to be musllinux. All eleven runtime
dependencies publish musllinux wheels for x86_64 and aarch64, including the
compiled ones (`pydantic-core`, `uvloop`, `httptools`, `watchfiles`), and the
`--only-binary=:all:` flag in the Dockerfile makes a missing wheel fail the
build loudly instead of starting a source compile the image has no toolchain
for. Re-run a real build before bumping a base image tag.

`addon/run.sh` reads `/data/options.json` through bashio and execs the daemon.
It reads every option behind a `bashio::config.has_value` guard so that an unset
option, or a Supervisor API that is briefly unreachable, falls back to the
default instead of killing the add-on at startup.

### Why no port by default

Home Assistant publishes a mapped add-on port on every interface and offers no
loopback-only option. Since reads are unauthenticated, `config.yaml` declares
`8765/tcp: null`, so nothing is reachable until you choose a port.

### Why no ingress

Ingress would be the better answer, and it does not work yet. Home Assistant
serves an add-on under `/api/hassio_ingress/<token>/` and strips that prefix
before proxying. The dashboard is a Vite SPA built with the default base of `/`,
and its API client requests absolute `/api/...` and `/ws` paths
(`web/src/api/client.ts`, `web/src/api/useWebSocket.ts`). Under an ingress prefix
the browser resolves those against the Home Assistant root instead of the
add-on, so assets, API calls, and the WebSocket all miss.

Fixing it is a frontend change, not an add-on change: the SPA needs a runtime
base path, `vite.config.ts` needs a matching `base`, and the API client and
WebSocket URL builder need to prepend it. Until that lands, the port is the
honest surface, and `ingress: false` is a statement of fact rather than a
default nobody revisited.

### Testing the add-on image locally

The Home Assistant builder normally supplies `BUILD_FROM`. To build it by hand,
pass one from `addon/build.yaml`:

```bash
docker build -t unifioptimizer-addon:test \
  --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.19 \
  addon/
```

Running it outside Home Assistant works, with bashio logging one error about not
reaching the Supervisor API before falling back to defaults:

```bash
mkdir -p /tmp/addon-data
echo '{"log_level":"info"}' > /tmp/addon-data/options.json
docker run --rm -v /tmp/addon-data:/data -p 127.0.0.1:8765:8765 unifioptimizer-addon:test
```

---

## What none of this changes

`pip install unifioptimizer` still works exactly as before, and the release
workflow that publishes the wheel is untouched. The container paths are
additions, not a replacement.
