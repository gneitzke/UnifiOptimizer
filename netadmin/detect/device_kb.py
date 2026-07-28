"""Locating and loading the device-capability knowledge base.

The KB (``wifi_device_capabilities.json``) maps device-name patterns to device
classes. Only ``known_2.4ghz_only`` currently drives detection, at two call
sites: :class:`~netadmin.detect.detectors.client.KnownPathologyDetector` fires
``iot_pmf_11r`` when a device in that class disconnects repeatedly, and the wired
bad-cable detector reuses the same list to suppress a downshift finding against a
peer that negotiates 10/100 by design. The ``wifi7_devices`` / ``wifi6e_devices``
/ ``dual_band_devices`` sections are reserved -- no code reads them yet.

Resolution order (first hit wins):

1. ``settings.thresholds['client.known_pathology']['kb_path']`` -- an explicit
   operator override, applied by the caller rather than here, because only the
   detector holds a :class:`~netadmin.detect.context.DetectorContext` to read
   thresholds from. It therefore reaches ``client.known_pathology`` only; the
   wired detector always resolves through tiers 2-3.
2. ``<data dir>/wifi_device_capabilities.json`` -- the operator's own editable
   copy in the runtime data dir (``NETADMIN_DATA_DIR``, else ``./data``). This
   is what makes the KB user-extensible per docs/DEVICE_DATABASE.md without
   editing anything inside site-packages, and puts it where ``pip install
   --upgrade`` cannot clobber it.
3. :data:`PACKAGED_KB_PATH` -- the baseline shipped inside the wheel.

Step 3 is why this module exists. The KB used to be resolved as
``Path(__file__).parents[3] / "data" / ...``, which is the repo root from a
source checkout but ``site-packages/data/`` once installed -- a directory that
never exists -- so *every* wheel install silently ran KB-empty. That is the same
class of bug as the ``DATA_DIR`` fix in 0.1.2 (resolve runtime paths from the
runtime, shipped assets from the package), applied to the one asset that fix
missed. The one-line INFO on first successful load exists so the *next* bug of
this class is one ``grep`` away instead of invisible.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Union

from netadmin import config
from netadmin.logging import get_logger

__all__ = [
    "KB_FILENAME",
    "PACKAGED_KB_PATH",
    "default_kb_path",
    "load_kb",
    "section_patterns",
]

_log = get_logger("detect.device_kb")

KB_FILENAME = "wifi_device_capabilities.json"

# The baseline copy shipped inside the package. netadmin/detect/device_kb.py ->
# parents[1] == the netadmin package root, so this is netadmin/data/<file>, which
# pyproject's package-data glob ("data/*.json") puts in the wheel.
PACKAGED_KB_PATH = Path(__file__).resolve().parents[1] / "data" / KB_FILENAME

# Paths already announced at INFO, so a per-pass detector does not re-log a state
# that cannot change without a restart. Failures are deliberately NOT tracked
# here: callers re-announce those on their own schedule, because a load that
# fails today may succeed tomorrow and must not be silenced after one line.
_announced_paths: set[str] = set()


def default_kb_path() -> Path:
    """The KB to read when no explicit ``kb_path`` override is configured.

    Prefers the operator's editable copy in the runtime data dir and falls back
    to the packaged baseline, so a wheel install always has a real KB to read.

    ``config.DATA_DIR`` is read here rather than captured at import so tests can
    monkeypatch it (and so the value tracks the module attribute, which
    :mod:`netadmin.config` computes from ``Path.cwd()`` at *its* import).
    """
    operator_copy = config.DATA_DIR / KB_FILENAME
    return operator_copy if operator_copy.is_file() else PACKAGED_KB_PATH


def load_kb(path: Optional[Union[str, Path]] = None) -> Optional[dict[str, Any]]:
    """Load the KB mapping from ``path`` (default: :func:`default_kb_path`).

    Returns ``None`` on failure -- the file is missing, unreadable, not valid
    JSON, or not a JSON object -- so callers can tell "no KB at all" from a KB
    that parsed fine and happens to be empty, and log accordingly. Logging the
    failure is left to callers, who know whether it is expected and how often to
    repeat it; a successful load announces its path once per process.
    """
    target = Path(path) if path is not None else default_kb_path()
    try:
        with open(target, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
    except (OSError, ValueError) as exc:
        # Say so, once per path. Returning None silently is how a typo'd kb_path
        # turns into a detector that quietly finds nothing forever: the
        # known-pathology branch is driven entirely by this file, so an unreadable
        # KB is indistinguishable from a healthy network. Name the packaged
        # baseline in the same breath, because "there is a working default you are
        # not using" is the actionable half.
        key = f"failed:{target}"
        if key not in _announced_paths:
            _announced_paths.add(key)
            _log.warning(
                "device KB: cannot read %s (%s); known-pathology detection is "
                "inert until this is fixed. Remove the kb_path override to fall "
                "back to the packaged baseline at %s",
                target,
                exc,
                PACKAGED_KB_PATH,
            )
        return None
    if not isinstance(loaded, dict):
        return None

    key = str(target)
    if key not in _announced_paths:
        _announced_paths.add(key)
        _log.info(
            "device KB: reading %s (%s); %d known-2.4GHz-only patterns",
            target,
            "packaged baseline" if target == PACKAGED_KB_PATH else "operator copy",
            len(section_patterns(loaded, "known_2.4ghz_only")),
        )
    return loaded


def section_patterns(kb: Optional[dict[str, Any]], section: str) -> tuple[str, ...]:
    """Lowercased, de-duplicated patterns for ``section``; ``()`` for any bad shape.

    The KB is hand-edited JSON, so every level below "it parsed" is untrusted.
    Read sections through here rather than chasing ``kb[section]["patterns"]``
    inline: the expected shape is ``{"patterns": [str, ...]}``, and the three
    shapes an operator most plausibly writes instead are all traps.

    A bare list (``"known_2.4ghz_only": ["esp32"]``) makes the inline chase raise
    ``AttributeError`` on ``list.get``; the detection engine isolates the raising
    detector and skips it, so a small typo silently takes a whole detector
    offline. A bare string (``"patterns": "esp32"``) is worse: it is iterable, so
    substring matching walks it character by character and ``"e"`` matches nearly
    every device name, inventing findings. An empty string among the patterns is
    worse still -- ``"" in haystack`` is always true, so it matches *everything*,
    which in the wired detector suppresses every bad-cable finding on the site.
    All three collapse to a safe value here.
    """
    block = (kb or {}).get(section)
    raw = block.get("patterns") if isinstance(block, dict) else None
    if not isinstance(raw, list):
        return ()
    cleaned = (str(p).strip().lower() for p in raw)
    return tuple(dict.fromkeys(p for p in cleaned if p))  # de-dup, order-stable
