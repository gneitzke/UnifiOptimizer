#!/usr/bin/env python3
"""Build the web UI and bundle it into the package as data.

Runs the Vite production build in ``web/`` and copies the result into
``netadmin/_webui/`` so the compiled dashboard ships *inside the wheel* as
package data (see ``pyproject.toml`` ``[tool.setuptools.package-data]``). End
users who ``pip install unifioptimizer`` then get a working dashboard with **no
Node.js** — the point of the whole exercise.

The RELEASE / wheel build MUST run this before ``python -m build`` (the release
workflow does; see ``.github/workflows/release.yml``). ``install.sh`` runs it too
for a source install.

Usage::

    python tools/build_web.py               # npm install (ci) + vite build + bundle
    python tools/build_web.py --skip-build   # bundle an already-built web/dist
    python tools/build_web.py --clean        # remove the bundled UI, do nothing else

The server (``netadmin/server/main.py``) prefers this bundled directory and falls
back to ``web/dist`` for local development, so a dev checkout without a bundle
still serves the UI straight from ``web/dist``.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = REPO_ROOT / "web"
DIST_DIR = WEB_DIR / "dist"
TARGET_DIR = REPO_ROOT / "netadmin" / "_webui"


def _fail(msg: str) -> "int":
    print(f"error: {msg}", file=sys.stderr)
    return 1


def _npm() -> str | None:
    """Locate an ``npm`` executable, or ``None`` when Node.js is absent."""
    return shutil.which("npm")


def _run(cmd: list[str], cwd: Path) -> None:
    print(f"$ {' '.join(cmd)}  (in {cwd})")
    subprocess.run(cmd, cwd=str(cwd), check=True)


def build_web() -> None:
    """Install web dependencies and run the production Vite build."""
    npm = _npm()
    if npm is None:
        raise RuntimeError(
            "npm (Node.js) not found. Install Node.js 18+ to build the web UI, "
            "or pass --skip-build to bundle an existing web/dist."
        )
    if not WEB_DIR.is_dir():
        raise RuntimeError(f"web/ directory not found at {WEB_DIR}")

    # `npm ci` is reproducible when a lockfile exists; fall back to `npm install`.
    if (WEB_DIR / "package-lock.json").is_file():
        _run([npm, "ci"], cwd=WEB_DIR)
    else:
        _run([npm, "install"], cwd=WEB_DIR)
    _run([npm, "run", "build"], cwd=WEB_DIR)


def bundle() -> None:
    """Copy ``web/dist`` into ``netadmin/_webui`` (replacing any prior bundle)."""
    index = DIST_DIR / "index.html"
    if not index.is_file():
        raise RuntimeError(
            f"{index} missing — the web build did not produce a dist. "
            "Run without --skip-build, or build web/ first."
        )
    if TARGET_DIR.exists():
        shutil.rmtree(TARGET_DIR)
    shutil.copytree(DIST_DIR, TARGET_DIR)
    n = sum(1 for p in TARGET_DIR.rglob("*") if p.is_file())
    print(f"bundled {n} files into {TARGET_DIR.relative_to(REPO_ROOT)}/")


def clean() -> None:
    if TARGET_DIR.exists():
        shutil.rmtree(TARGET_DIR)
        print(f"removed {TARGET_DIR.relative_to(REPO_ROOT)}/")
    else:
        print("nothing to clean")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_web.py",
        description="Build the web UI and bundle it into netadmin/_webui for packaging.",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="do not run npm; just copy an already-built web/dist into the package",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="remove the bundled netadmin/_webui directory and exit",
    )
    args = parser.parse_args(argv)

    if args.clean:
        clean()
        return 0

    try:
        if not args.skip_build:
            build_web()
        bundle()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        return _fail(str(exc))

    print("web UI bundled — the wheel will ship a working dashboard (no Node needed to run).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
