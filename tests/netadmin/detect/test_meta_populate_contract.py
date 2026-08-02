"""Populate contract: every entity-``meta`` key a detector reads is set by ingest.

The second guardrail for the #59 bug class. #59b was a detector reading
``radio.meta["min_rssi"]`` that ``map_device`` never populated -- the config-audit
arm sat dead because the field it needed never arrived. The same enumeration that
motivated this test surfaced three more of exactly that shape (``tx_power``,
``tx_power_mode``, ``mesh_enabled``), one of which (``tx_power_mode``) was keeping
the whole ``wifi.tx_power_loud`` detector from ever firing.

The contract: every string key a detector reads off an entity's ``meta``
(``entity.meta["k"]`` or ``entity.meta.get("k")``) must be a key some ingest
mapper actually writes into a ``meta`` dict. A key read but never populated is a
silently dead arm -- no crash (the reads are guarded), just a check that runs on
absent data forever.

This is AST-static and covers the four shapes ingest uses to build ``meta``:
a dict literal (named ``*_meta`` or passed as a ``meta=`` kwarg), a
``*_meta[key] = ...`` subscript assign, a ``*_meta.update({...})``, and the
None-stripping dict comprehension ``{k: v for k, v in {...}.items() if v}``
(collector's rogue-BSS and WLAN meta). The check is key-level, matching the
emit-contract test's granularity: a key populated for *any* entity type counts,
because the failure this guards -- a key populated *nowhere* -- is the #59 shape.

``_EXEMPT`` is a closed, reasoned allowlist for the two reads that legitimately
have no direct populate site. A reviewer adds to it consciously, never to silence
a genuine dead arm -- the right fix for a dead arm is to emit the field.
"""

from __future__ import annotations

import ast
from pathlib import Path

import netadmin.detect.detectors as _detectors_pkg
import netadmin.ingest as _ingest_pkg

_DETECTORS_DIR = Path(_detectors_pkg.__file__).parent
_INGEST_DIR = Path(_ingest_pkg.__file__).parent

# Reads with no direct populate site, each live for a documented reason. NOT a
# place to park a dead arm -- fixing a dead arm means emitting the field.
_EXEMPT: dict[str, str] = {
    # wired.poe_budget reads meta.get("poe_budget") or meta.get("total_max_power").
    # The preferred alias is never written; the fallback total_max_power IS
    # (mapping.py dev_meta), so the arm is live. Kept as a read-side alias only.
    "poe_budget": "detector falls back to total_max_power, which is populated",
    # wifi uplink-hop corroboration. There is no flat controller field to emit --
    # hop depth must be derived by walking the uplink.uplink_mac parent chain
    # across the site's devices, tracked as its own design task (Gitea).
    "uplink_hops": "no flat source field; needs a derived parent-chain walk (tracked)",
}


def _meta_keys_read_by_detectors() -> dict[str, set[str]]:
    """Every string key read off ``*.meta`` in the detector modules -> files."""
    read: dict[str, set[str]] = {}
    for path in sorted(_DETECTORS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            key = None
            # x.meta.get("k")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "meta"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                key = node.args[0].value
            # x.meta["k"]
            elif (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "meta"
                and isinstance(node.slice, ast.Constant)
            ):
                key = node.slice.value
            if isinstance(key, str):
                read.setdefault(key, set()).add(path.name)
    return read


def _dict_literal_keys(d: ast.Dict) -> set[str]:
    return {k.value for k in d.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def _meta_keys_populated_by_ingest() -> set[str]:
    """Every string key any ingest mapper writes into a ``meta`` dict.

    Covers the four construction shapes ingest uses (see module docstring).
    """
    populated: set[str] = set()
    for path in sorted(_INGEST_DIR.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            # 1. <name>meta = {literal}  and  <name>meta = {comp}
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                target = (
                    node.target
                    if isinstance(node, ast.AnnAssign)
                    else (node.targets[0] if len(node.targets) == 1 else None)
                )
                if getattr(target, "id", "").endswith("meta"):
                    if isinstance(node.value, ast.Dict):
                        populated |= _dict_literal_keys(node.value)
                    elif isinstance(node.value, ast.DictComp):
                        populated |= _dictcomp_source_keys(node.value)
                # 3. <name>meta[key] = ...
                if isinstance(node, ast.Assign):
                    for tgt in node.targets:
                        if (
                            isinstance(tgt, ast.Subscript)
                            and getattr(tgt.value, "id", "").endswith("meta")
                            and isinstance(tgt.slice, ast.Constant)
                            and isinstance(tgt.slice.value, str)
                        ):
                            populated.add(tgt.slice.value)
            # 2. meta={literal} passed as a kwarg to Entity(...)
            if (
                isinstance(node, ast.keyword)
                and node.arg == "meta"
                and isinstance(node.value, ast.Dict)
            ):
                populated |= _dict_literal_keys(node.value)
            # 4. <name>meta.update({literal})
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "update"
                and getattr(node.func.value, "id", "").endswith("meta")
                and node.args
                and isinstance(node.args[0], ast.Dict)
            ):
                populated |= _dict_literal_keys(node.args[0])
    return populated


def _dictcomp_source_keys(comp: ast.DictComp) -> set[str]:
    """Keys of the inner ``{...}`` a ``{k: v for k, v in {...}.items()}`` iterates.

    Collector strips None values with this idiom; the real key set is the inner
    dict literal, not the comprehension's ``k``/``v`` targets.
    """
    keys: set[str] = set()
    for gen in comp.generators:
        it = gen.iter
        if (
            isinstance(it, ast.Call)
            and isinstance(it.func, ast.Attribute)
            and it.func.attr == "items"
            and isinstance(it.func.value, ast.Dict)
        ):
            keys |= _dict_literal_keys(it.func.value)
    return keys


def test_every_detector_meta_read_has_a_populate_site() -> None:
    """A meta key read by a detector but written by no ingest mapper is a dead arm."""
    read = _meta_keys_read_by_detectors()
    populated = _meta_keys_populated_by_ingest()

    dead = {k: files for k, files in read.items() if k not in populated and k not in _EXEMPT}
    assert not dead, (
        "These meta keys are read by detectors but populated by no ingest mapper "
        "-- silently dead arms of the #59 shape:\n"
        + "\n".join(f"  {k!r}  read in {sorted(files)}" for k, files in sorted(dead.items()))
        + "\nEmit the field in map_device/map_client (or, if truly unsourceable, "
        "add it to _EXEMPT with a reason)."
    )


def test_exemptions_are_still_read_and_still_unpopulated() -> None:
    """Keep _EXEMPT honest: an entry that is now populated, or no longer read,
    should be removed rather than left masking the contract."""
    read = _meta_keys_read_by_detectors()
    populated = _meta_keys_populated_by_ingest()
    for key, reason in _EXEMPT.items():
        assert (
            key in read
        ), f"_EXEMPT[{key!r}] is no longer read by any detector; drop it ({reason})"
        assert (
            key not in populated
        ), f"_EXEMPT[{key!r}] is now populated by ingest; drop the exemption ({reason})"
