# Home Assistant add-on image for UnifiOptimizer.
#
# BUILD_FROM is substituted per architecture by the Home Assistant builder from
# ./build.yaml. To build it by hand for a local test, pass the base explicitly:
#
#   docker build -t unifioptimizer-addon:test \
#     --build-arg BUILD_FROM=ghcr.io/home-assistant/aarch64-base-python:3.12-alpine3.19 \
#     addon/
#
# WHY THE PUBLISHED WHEEL AND NOT THE SOURCE TREE: `unifioptimizer` on PyPI
# ships the compiled dashboard inside it at netadmin/_webui/ (the release
# workflow refuses to publish a wheel without it). Building from source here
# would drag Node into the add-on image just to rebuild a UI that is already
# built, for no gain. The pin is exact, so an add-on version means one known
# wheel; bump it together with addon/config.yaml and pyproject.toml, which a
# test enforces.
ARG BUILD_FROM
FROM ${BUILD_FROM}

# The add-on's persistent directory, mounted by the Supervisor and preserved
# across add-on updates. Everything mutable lands here: secrets.env, config.yaml,
# the SQLite store, and the logs.
ENV NETADMIN_DATA_DIR=/data \
    DB_PATH=/data/netadmin.db \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

ARG NETADMIN_VERSION=0.6.0

# --no-cache keeps the wheel cache out of the layer. --only-binary=:all: is the
# guard that matters on musl: it fails loudly if any dependency lacks a
# musllinux wheel, instead of silently starting a source build that would need a
# toolchain this image does not have.
#
# [mcp] pulls in the optional MCP SDK (docs/MCP_SERVER.md) so the remote mount
# at /mcp works out of the box, same as Dockerfile.netadmin. It stays an extra
# in pyproject.toml, not a core dependency: the daemon never imports it, and
# the route stays absent (404) until the add-on's mcp_token option is set
# (netadmin/server/mcp_mount.py). The SDK is pure-Python (py3-none-any), so it
# does not weaken the --only-binary=:all: musllinux guard above.
RUN pip install --no-cache-dir --only-binary=:all: \
      "unifioptimizer[mcp]==${NETADMIN_VERSION}"

COPY run.sh /run.sh
RUN chmod a+x /run.sh

# s6-overlay is the base image's entrypoint and runs CMD as the add-on's service.
CMD [ "/run.sh" ]
