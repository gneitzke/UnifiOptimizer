"""The container install paths: docker-compose.yml and the Home Assistant add-on.

These files are shipped as install surface but nothing imports them, so a typo
or a well-meaning "simplification" would otherwise only be caught by a user.
Every assertion here stands for a property that was verified against a real
build and run (docs/CONTAINER.md), and that silently breaks a deployment if it
regresses:

* the API is unauthenticated for reads, so a published port that is not pinned
  to loopback hands the network inventory to the whole LAN,
* the SQLite store and ``secrets.env`` must land on a mount, or an image rebuild
  discards a user's entire history,
* ``NETADMIN_DATA_DIR`` must be pinned, or a working-directory change silently
  relocates the database,
* the add-on installs one exact published wheel, so three version strings in
  three files have to agree.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
DAEMON_DOCKERFILE = REPO_ROOT / "Dockerfile.netadmin"
ADDON_DIR = REPO_ROOT / "addon"
ADDON_CONFIG = ADDON_DIR / "config.yaml"
ADDON_BUILD = ADDON_DIR / "build.yaml"
ADDON_DOCKERFILE = ADDON_DIR / "Dockerfile"
ADDON_RUN = ADDON_DIR / "run.sh"
REPOSITORY_YAML = REPO_ROOT / "repository.yaml"

DATA_MOUNTPOINT = "/app/data"
DAEMON_PORT = 8765


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture(scope="module")
def compose() -> dict:
    return _load_yaml(COMPOSE_FILE)


@pytest.fixture(scope="module")
def service(compose: dict) -> dict:
    services = compose["services"]
    assert len(services) == 1, "one daemon process, one service (ARCHITECTURE.md 2)"
    return next(iter(services.values()))


@pytest.fixture(scope="module")
def addon() -> dict:
    return _load_yaml(ADDON_CONFIG)


# --------------------------------------------------------------------------
# docker-compose.yml
# --------------------------------------------------------------------------


def test_compose_file_exists_and_parses(compose):
    assert isinstance(compose, dict)
    assert "services" in compose


def test_compose_builds_the_existing_daemon_dockerfile(service):
    """It reuses Dockerfile.netadmin rather than forking a second image."""
    build = service["build"]
    assert build["dockerfile"] == "Dockerfile.netadmin"
    assert (REPO_ROOT / build["dockerfile"]).is_file()


def test_compose_publishes_only_to_loopback(service):
    """Reads are unauthenticated; a bare "8765:8765" would expose the LAN.

    Verified live: with this mapping the daemon answers on 127.0.0.1 and the
    host's LAN address refuses the connection.
    """
    ports = service["ports"]
    assert ports, "the daemon has to be reachable somehow"
    for entry in ports:
        assert isinstance(entry, str), "long-form port syntax hides the host_ip check"
        host_ip = entry.rsplit(":", 2)[0]
        assert host_ip in ("127.0.0.1", "localhost"), (
            f"port mapping {entry!r} does not pin the host interface to loopback; "
            "the API is unauthenticated for reads and must never default to the LAN"
        )
        assert entry.endswith(f":{DAEMON_PORT}")


def test_compose_persists_data_on_a_volume(compose, service):
    """secrets.env, config.yaml and the SQLite store outlive the container."""
    mounts = service["volumes"]
    targets = [entry.split(":")[1] for entry in mounts]
    assert DATA_MOUNTPOINT in targets, f"nothing mounted at {DATA_MOUNTPOINT}"

    source = next(e.split(":")[0] for e in mounts if e.split(":")[1] == DATA_MOUNTPOINT)
    if not source.startswith((".", "/", "~")):
        assert source in (
            compose.get("volumes") or {}
        ), f"named volume {source!r} is used but never declared under top-level `volumes:`"


def test_compose_pins_the_data_dir_to_the_mount(service):
    """NETADMIN_DATA_DIR must point at the mount, not merely default to it."""
    assert service["environment"]["NETADMIN_DATA_DIR"] == DATA_MOUNTPOINT


def test_compose_restarts_and_healthchecks(service):
    assert service["restart"] in ("unless-stopped", "always")
    probe = " ".join(service["healthcheck"]["test"])
    assert "/api/health" in probe
    # python:3.12-slim carries no curl or wget; the probe has to use the interpreter.
    assert "python" in probe


def test_compose_bakes_in_no_secrets(service):
    """Credentials belong in the volume or the process env, never in a tracked file."""
    rendered = json.dumps(service).lower()
    for marker in ("unifi_password", "unifi_api_key", "unifi_username"):
        assert marker not in rendered, f"{marker} must not appear in a tracked compose file"


def test_compose_has_a_commented_mcp_token_passthrough():
    """NETADMIN_MCP_TOKEN is a separate credential from NETADMIN_API_TOKEN
    (Gitea #29/#30) and needs the same opt-in passthrough the API token has,
    or a Compose user has no way to configure the remote MCP mount."""
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "# NETADMIN_MCP_TOKEN: ${NETADMIN_MCP_TOKEN:-}" in text
    assert (
        "NETADMIN_API_TOKEN" in text
    ), "the API token passthrough this mirrors must still be present"


# --------------------------------------------------------------------------
# Dockerfile.netadmin
# --------------------------------------------------------------------------


def test_daemon_dockerfile_installs_the_mcp_extra():
    """Both container paths ship the optional MCP SDK (docs/MCP_SERVER.md) so
    /mcp works without a rebuild once NETADMIN_MCP_TOKEN is set. It must stay
    an extra, never inflate the pinned 11-dependency core list elsewhere."""
    text = DAEMON_DOCKERFILE.read_text(encoding="utf-8")
    assert '"mcp>=1.2"' in text


# --------------------------------------------------------------------------
# Home Assistant add-on
# --------------------------------------------------------------------------


def test_addon_skeleton_is_complete():
    for path in (ADDON_CONFIG, ADDON_BUILD, ADDON_DOCKERFILE, ADDON_RUN, ADDON_DIR / "DOCS.md"):
        assert path.is_file(), f"missing add-on file: {path.relative_to(REPO_ROOT)}"


def test_addon_repository_manifest_present():
    """Without repository.yaml at the root the GitHub URL is not an add-on repo."""
    repo = _load_yaml(REPOSITORY_YAML)
    assert repo["name"]
    assert repo["url"].endswith("UnifiOptimizer")


def test_addon_config_has_required_supervisor_keys(addon):
    for key in ("name", "version", "slug", "description", "arch", "startup", "boot"):
        assert addon.get(key), f"add-on config.yaml is missing {key!r}"
    assert addon["slug"] == "unifioptimizer"


def test_addon_declares_the_port_unpublished(addon):
    """Home Assistant publishes a mapped port on every interface, with no
    loopback option. Reads are unauthenticated, so the default has to be null
    and the user has to opt in from the Configuration tab."""
    ports = addon["ports"]
    assert f"{DAEMON_PORT}/tcp" in ports
    assert ports[f"{DAEMON_PORT}/tcp"] is None, (
        "a non-null default port publishes the unauthenticated API on the LAN "
        "the moment the add-on starts"
    )
    assert addon["ports_description"][f"{DAEMON_PORT}/tcp"]


def test_addon_ingress_is_off_until_the_spa_supports_a_base_path(addon):
    """The SPA requests absolute /api and /ws paths, which do not survive the
    ingress prefix. Flipping this to true ships a blank page; flip it only
    together with a frontend base-path change."""
    assert addon["ingress"] is False


def test_addon_uses_the_supervisor_data_dir(addon):
    """/data is provided and preserved by the Supervisor, so no extra maps."""
    assert addon.get("map") in (None, [], {})
    run = ADDON_RUN.read_text(encoding="utf-8")
    assert "NETADMIN_DATA_DIR=/data" in run
    assert "DB_PATH=/data/" in run


def test_addon_run_script_is_executable_and_execs_the_daemon():
    assert ADDON_RUN.stat().st_mode & 0o111, "run.sh must be executable"
    run = ADDON_RUN.read_text(encoding="utf-8")
    assert run.startswith("#!/usr/bin/with-contenv bashio")
    assert "exec netadmin daemon" in run


def test_addon_reads_every_option_behind_a_has_value_guard():
    """`set -e` plus a bare `bashio::config` call kills the add-on when the
    Supervisor API blips. Each option read has to sit behind a has_value guard,
    whose non-zero return inside an `if` is exempt from `set -e`."""
    run = ADDON_RUN.read_text(encoding="utf-8")
    guarded = run.count("bashio::config.has_value")
    reads = run.count("$(bashio::config '")
    assert guarded >= reads, "an option is read without a has_value guard"


def test_addon_config_has_mcp_token_option(addon):
    """mcp_token is a SEPARATE credential from api_token (Gitea #29/#30) --
    without it the add-on has no way to configure the remote MCP mount."""
    assert addon["options"]["mcp_token"] == ""
    assert addon["schema"]["mcp_token"] == "password?"


def test_addon_run_maps_mcp_token_behind_a_has_value_guard():
    run = ADDON_RUN.read_text(encoding="utf-8")
    assert "bashio::config.has_value 'mcp_token'" in run
    assert "NETADMIN_MCP_TOKEN" in run
    assert "export NETADMIN_MCP_TOKEN" in run


def test_addon_dockerfile_installs_a_pinned_published_wheel():
    text = ADDON_DOCKERFILE.read_text(encoding="utf-8")
    assert "ARG BUILD_FROM" in text and "FROM ${BUILD_FROM}" in text
    # [mcp] installs the optional MCP SDK too (docs/MCP_SERVER.md) so /mcp works
    # out of the box; the version pin itself must still be exact.
    assert 'unifioptimizer[mcp]==${NETADMIN_VERSION}"' in text
    # On musl a missing wheel would otherwise start a source build in an image
    # with no toolchain. Fail the build instead.
    assert "--only-binary=:all:" in text
    assert "NETADMIN_DATA_DIR=/data" in text


def test_addon_build_covers_every_declared_arch(addon):
    build = _load_yaml(ADDON_BUILD)
    assert set(build["build_from"]) == set(addon["arch"]), (
        "config.yaml `arch` and build.yaml `build_from` disagree; the Home "
        "Assistant builder has no base image for the difference"
    )
    for base in build["build_from"].values():
        assert base.startswith("ghcr.io/home-assistant/")


def test_addon_version_matches_the_package_version(addon):
    """The add-on installs one exact wheel, so these three must not drift."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    package_version = next(
        line.split("=", 1)[1].strip().strip('"')
        for line in pyproject.splitlines()
        if line.startswith("version =")
    )
    assert addon["version"] == package_version

    dockerfile = ADDON_DOCKERFILE.read_text(encoding="utf-8")
    assert f"ARG NETADMIN_VERSION={package_version}" in dockerfile


def test_addon_config_carries_no_secret_defaults(addon):
    """Credential options ship empty; the first-run web setup writes them."""
    for key in ("api_token", "controller_api_key"):
        assert addon["options"][key] == ""
        assert addon["schema"][key] == "password?"
