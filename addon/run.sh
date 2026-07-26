#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
#
# Add-on entrypoint. Turns the Supervisor's /data/options.json into environment
# variables and hands off to the daemon.
#
# Nothing here is written to disk. Settings reads the process environment at a
# higher priority than /data/secrets.env (netadmin/config.py), so an option set
# in the add-on UI wins for this run without ever touching the credentials file
# the first-run web setup owns. Leave the credential options empty and the
# daemon starts unconfigured, serving the setup flow, which is the intended
# path for most installs.
set -euo pipefail

export NETADMIN_DATA_DIR=/data
export DB_PATH=/data/netadmin.db

# No windowing system in a container, and nothing to open a browser onto.
export NETADMIN_NO_BROWSER=1

# Baked in, not detected: self-update (docs/ARCHITECTURE.md section 23) must
# tell an add-on user "Settings -> Add-ons -> UnifiOptimizer -> Update", never
# a pip self-upgrade (there is no writable venv here to upgrade) or a bare
# "container" host command (Home Assistant owns this container's lifecycle).
export NETADMIN_INSTALL_METHOD=addon

# Every option is read through `bashio::config.has_value` first. That guard is
# not decoration: a non-zero return inside an `if` condition is exempt from
# `set -e`, so an unset option, or a Supervisor API that is briefly unreachable,
# leaves the default in place instead of killing the add-on at startup.
LOG_LEVEL=info
if bashio::config.has_value 'log_level'; then
  LOG_LEVEL="$(bashio::config 'log_level')"
fi
export LOG_LEVEL

# Gates fix apply/revert over HTTP. Absent, reads still work and every mutating
# endpoint is refused, which is the safe default.
if bashio::config.has_value 'api_token'; then
  NETADMIN_API_TOKEN="$(bashio::config 'api_token')"
  export NETADMIN_API_TOKEN
fi

# Optional shortcut past the web setup flow for people who would rather keep
# credentials in the add-on configuration.
if bashio::config.has_value 'controller_host'; then
  UNIFI_HOST="$(bashio::config 'controller_host')"
  export UNIFI_HOST
fi
if bashio::config.has_value 'controller_api_key'; then
  UNIFI_API_KEY="$(bashio::config 'controller_api_key')"
  export UNIFI_API_KEY
fi

bashio::log.info "Starting UnifiOptimizer, data directory ${NETADMIN_DATA_DIR}"

# 0.0.0.0 binds inside the add-on's own network namespace only. Whether that is
# reachable from the LAN is decided entirely by the port mapping in the add-on
# Configuration tab, which defaults to unmapped.
exec netadmin daemon --host 0.0.0.0 --port 8765
