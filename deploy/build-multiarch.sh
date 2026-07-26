#!/usr/bin/env bash
#
# build-multiarch.sh — build the netadmin daemon image for BOTH linux/arm64 and
# linux/amd64 with `docker buildx`, then INSPECT the result to prove both
# platforms are present.
#
# It NEVER pushes to a registry and never runs anything against the live
# controller. The output is a local OCI-layout tarball plus a printed manifest
# list. This is the amd64 / x86-NAS build path; the Mac mini (arm64) uses the
# Apple `container` CLI instead. Both consume the same
# arch-neutral Dockerfile.netadmin.
#
# Usage:
#   ./deploy/build-multiarch.sh                 # build both arches, inspect, verify
#   PLATFORMS=linux/amd64 ./deploy/build-multiarch.sh   # single arch
#   SMOKE=1 ./deploy/build-multiarch.sh         # also load the native arch and import-test it
#
# Env:
#   PLATFORMS   default linux/arm64,linux/amd64
#   IMAGE       default netadmin:multiarch  (a local tag; nothing is pushed)
#   OUT         default ./dist/netadmin-oci.tar  (OCI-layout export)
#   BUILDER     default netadmin-multiarch  (a docker-container buildx builder)
#   SMOKE       set to 1 to `--load` the host-native arch and run an import test
#
# Requirements: Docker with the buildx plugin. Cross-arch layers build under
# QEMU/binfmt, which Docker Desktop and `binfmt` provide; because the image is
# pure-Python-plus-wheels there is no compilation under emulation, so the build
# is fast on both arches.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PLATFORMS="${PLATFORMS:-linux/arm64,linux/amd64}"
IMAGE="${IMAGE:-netadmin:multiarch}"
OUT="${OUT:-./dist/netadmin-oci.tar}"
BUILDER="${BUILDER:-netadmin-multiarch}"
DOCKERFILE="Dockerfile.netadmin"
SMOKE="${SMOKE:-0}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "docker not found on PATH"
docker buildx version >/dev/null 2>&1 || die "docker buildx plugin not available"
[ -f "$DOCKERFILE" ] || die "$DOCKERFILE not found (run from the repo, or check ASSIGNED paths)"

# --- Secret gate: refuse to build if a secret-bearing file would enter the
# context. .dockerignore already excludes these, but verify independently so a
# stale/edited ignore file can never leak a credential into an image layer. ---
say "Verifying the build context carries no secrets"
leaks="$(git ls-files --cached --others --exclude-standard \
  | grep -E '(^|/)(secrets\.env|deploy_hosts\.env|\.jwt_secret)$|\.env$|\.db(-wal|-shm)?$' \
  | grep -v -E '\.env\.example$' || true)"
if [ -n "$leaks" ]; then
  printf '%s\n' "$leaks" >&2
  die "secret-bearing files present in the tree; .dockerignore must exclude them (it does) — aborting out of caution"
fi
echo "context clean (no secrets/*.db/*.env)"

# --- Builder: a docker-container driver is required for multi-platform output.
# The default 'docker' driver can only build the host arch. ---
say "Ensuring buildx builder '$BUILDER' (docker-container driver)"
if ! docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
  docker buildx create --name "$BUILDER" --driver docker-container --bootstrap
else
  docker buildx inspect --bootstrap "$BUILDER" >/dev/null
fi

# --- Build both platforms to an OCI tarball. No --push, no registry. ---
mkdir -p "$(dirname "$OUT")"
say "Building $IMAGE for [$PLATFORMS] -> $OUT (no push)"
docker buildx build \
  --builder "$BUILDER" \
  --platform "$PLATFORMS" \
  --file "$DOCKERFILE" \
  --tag "$IMAGE" \
  --output "type=oci,dest=$OUT" \
  --provenance=false \
  .

# --- Inspect: read the OCI index and print every platform manifest it contains,
# then assert each requested platform is actually there. ---
say "Inspecting $OUT — platforms built"
python3 - "$OUT" "$PLATFORMS" <<'PY'
import json, sys, tarfile

out, requested = sys.argv[1], sys.argv[2].split(",")
with tarfile.open(out) as tf:
    def blob(name):
        return json.load(tf.extractfile(name))
    index = blob("index.json")
    # index.json -> a manifest that is itself an image index (the manifest list)
    top = index["manifests"][0]
    digest = top["digest"].replace("sha256:", "")
    doc = blob(f"blobs/sha256/{digest}")
    manifests = doc.get("manifests", [top]) if "manifests" in doc else [top]
    found = set()
    for m in manifests:
        p = m.get("platform", {})
        if p.get("os") and "unknown" not in p.get("architecture", ""):
            plat = f"{p['os']}/{p['architecture']}"
            found.add(plat)
            print(f"  - {plat}  {m['digest']}")
    missing = [p for p in requested if p not in found]
    if missing:
        sys.exit(f"MISSING platform manifests: {', '.join(missing)}")
    print(f"\nOK: all requested platforms present ({', '.join(sorted(found))})")
PY

# --- Optional: load the host-native arch into the local docker image store and
# run a runtime import smoke test. Multi-platform images cannot be --load'ed, so
# this rebuilds only the native arch. Still no controller contact. ---
if [ "$SMOKE" = "1" ]; then
  native="linux/$(docker version -f '{{.Server.Arch}}' 2>/dev/null || uname -m)"
  case "$native" in
    linux/x86_64) native="linux/amd64" ;;
    linux/aarch64) native="linux/arm64" ;;
  esac
  say "Smoke test: loading $native and importing the daemon package"
  docker buildx build --builder "$BUILDER" --platform "$native" \
    --file "$DOCKERFILE" --tag "$IMAGE-native" --load .
  docker run --rm "$IMAGE-native" \
    python -c "import netadmin, fastapi, apscheduler, pydantic_core; print('import OK', netadmin.__version__)"
fi

say "Done. Built and inspected; nothing was pushed."
echo "OCI tarball: $OUT"
echo "To publish later (only when you have a registry): "
echo "  docker buildx build --builder $BUILDER --platform $PLATFORMS -f $DOCKERFILE -t <registry>/netadmin:<tag> --push ."
