#!/usr/bin/env python3
"""Fail the build if a secret or a real network identifier reaches a tracked file.

This is a public repository. The rule is absolute: no credentials, no real network
data. That rule was previously enforced by reading carefully, which worked until it
did not. A controller fixture shipped two real ``guest_token`` values because the
sanitiser tokenised ``x_authkey``, ``serial``, ``syslog_key`` and ``x_vwirekey`` and
simply had no entry for ``guest_token``. An allowlist of things to scrub can only
ever catch what someone thought of; this scans for the *shape* of a secret instead,
so a field nobody anticipated still trips it.

Run over the tracked files only, because untracked working files (the live
database, ``data/secrets.env``, the upgrade journal) are the operator's own and are
excluded by ``.gitignore``.

Usage::

    python tools/scan_secrets.py          # scan tracked files, exit 1 on a finding
    python tools/scan_secrets.py --staged # scan what is staged, for a pre-commit hook
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

# --------------------------------------------------------------------------- #
# What counts as a finding
# --------------------------------------------------------------------------- #

# A secret-shaped assignment: a key whose NAME implies a credential, holding a long
# opaque value. Deliberately shape-based, not a list of known field names.
SECRET_ASSIGNMENT = re.compile(
    r"""["']?[A-Za-z_][A-Za-z0-9_]*(token|secret|api[_-]?key|passwd|password|authkey|passphrase)
        ["']?\s*[:=]\s*["']([A-Za-z0-9+/=_\-]{16,})["']""",
    re.IGNORECASE | re.VERBOSE,
)

# Values that are obviously not real, so a fixture can still be readable.
PLACEHOLDER = re.compile(
    r"(^<[^>]+>$|^x{3,}|your[-_]|example|placeholder|changeme|dummy|fake|test|"
    r"secret|token|password|passwd|redacted|^none$|^null$|^\$\{|^\{\{|sample)",
    re.IGNORECASE,
)

# Personal identifiers from this project's own environment. Extend as needed; the
# point is that these must never appear in a tracked file.
PERSONAL = (
    "cudahost",
    "garys.mac",
    "garythinkpad",
    "192.168.1.119",  # the controller
    "192.168.0.182",  # the internal git host
    "192.168.1.32",  # the deploy host
)

MAC = re.compile(r"\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b", re.IGNORECASE)


def _mac_looks_synthetic(mac: str) -> bool:
    """True when a MAC is obviously hand-written test data rather than captured.

    A vendor OUI is public information, so the prefix proves nothing either way;
    what distinguishes a real capture is a random-looking device suffix. Fixtures
    reach for patterns a human types: repeated octets (11:11:11), runs
    (11:22:33, aa:bb:cc), counters (00:00:01), or the locally-administered range
    that is reserved for exactly this purpose.
    """
    octets = mac.lower().split(":")
    # Locally administered (second nibble of the first octet is 2, 6, a or e):
    # reserved for local use, so never a real vendor device.
    if len(octets[0]) == 2 and octets[0][1] in "26ae":
        return True
    if len(set(octets)) <= 2:  # 11:11:11:11:11:11, aa:bb:aa:bb:aa:bb
        return True
    suffix = octets[3:]
    if len(set(suffix)) == 1:  # ...:11:11:11
        return True
    try:
        vals = [int(o, 16) for o in suffix]
    except ValueError:
        return False
    if vals[0] < 3 and vals[1] < 3:  # ...:00:00:0e counters
        return True
    if vals[1] - vals[0] == vals[2] - vals[1] and vals[1] != vals[0]:  # runs
        return True
    return False


# Paths that legitimately discuss these strings (the ignore rules, this scanner,
# and the doc explaining the policy).
EXEMPT_PATHS = {
    ".gitignore",
    "tools/scan_secrets.py",
    "docs/SECRETS_POLICY.md",
}

BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".woff", ".woff2", ".db"}

# Paths that must never be tracked at all, whatever their contents.
FORBIDDEN_TRACKED = re.compile(
    r"(^|/)(secrets\.env|deploy_hosts\.env|journal\.json)$|\.db$|\.db-(wal|shm)$|(^|/)data/upgrade/",
)


def _is_test_path(path: str) -> bool:
    return path.startswith("tests/") or "/fixtures/" in path or path.startswith("web/e2e/")


def tracked_files(staged: bool) -> list[str]:
    cmd = (
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"]
        if staged
        else ["git", "ls-files"]
    )
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def scan_file(path: str) -> list[str]:
    if path in EXEMPT_PATHS or Path(path).suffix.lower() in BINARY_SUFFIXES:
        return []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    findings: list[str] = []
    for i, line in enumerate(text.splitlines(), 1):
        low = line.lower()

        for needle in PERSONAL:
            if needle in low:
                findings.append(f"{path}:{i}: personal identifier {needle!r}")

        for match in SECRET_ASSIGNMENT.finditer(line):
            value = match.group(2)
            if not PLACEHOLDER.search(value):
                findings.append(
                    f"{path}:{i}: secret-shaped value {value[:12]}... (use a placeholder)"
                )

        # A MAC only signals a leak outside the test corpus. Fixture MACs are
        # hand-written by construction and endlessly varied, so scanning them
        # produces noise, and a guard that cries wolf is a guard that gets turned
        # off. In docs or shipped source, a real MAC is a genuine finding.
        if not _is_test_path(path):
            for mac in MAC.findall(line):
                if not _mac_looks_synthetic(mac):
                    findings.append(f"{path}:{i}: real-looking MAC {mac}")

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="scan staged changes only")
    args = parser.parse_args()

    findings: list[str] = []
    for path in tracked_files(args.staged):
        if FORBIDDEN_TRACKED.search(path):
            findings.append(f"{path}: this file must never be tracked (see .gitignore)")
        findings.extend(scan_file(path))

    if findings:
        print("Secret scan FAILED. A public repository must carry no credentials")
        print("and no real network identifiers.\n")
        for f in findings:
            print(f"  {f}")
        print(
            "\nReplace the value with a placeholder such as <guest_token>, or add the "
            "path to EXEMPT_PATHS in tools/scan_secrets.py if it is genuinely policy text."
        )
        return 1

    print("Secret scan passed: no credentials or real network identifiers in tracked files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
