"""Emit contract: every registered METRIC has a place that emits it.

The guardrail for the #59 bug class -- a series declared in
``netadmin.ingest.mapping.METRICS`` (so the counter/gauge is registered and the
kind/unit is known) but that ``map_device``/``map_client`` never actually
``_emit``\\ s. The value is parsed onto the model and the metric "exists" from
every outside vantage point, yet no sample ever lands, so any detector arm that
weighs it runs on nothing. That is exactly how ``rx_packets`` sat dead: it was
in ``METRICS`` and on ``UnifiPort``, but the port loop never emitted it, so
``bad_cable``'s packet-fraction arm was silently inert.

The relationship is meant to be a bijection. ``_emit`` looks up
``METRICS[metric]`` (mapping.py), so the *emitted-but-unregistered* direction
already fails loudly at runtime with a ``KeyError`` -- this test pins the quieter
*registered-but-unemitted* direction, which fails at authoring time instead of in
production silence. Adding a series to ``METRICS`` without an emit site (or
deleting the last emit site of a registered series) fails here.

The check is AST-static -- it reads the two facts out of ``mapping.py`` without
importing a controller or building a payload: the ``METRICS`` dict literal, and
every string passed as the metric name (3rd positional arg) to ``_emit(...)``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import netadmin.ingest.mapping as _mapping_mod

_MAPPING_PATH = Path(_mapping_mod.__file__)

# A registered series with no emit site is a bug, not a config knob, so there is
# no open-ended exemption set here (unlike the unit-suffix test's dimensionless
# escapes). If a genuine reason to register-without-emitting ever appears, add it
# here with a comment -- deliberately, one series at a time.
_EMIT_EXEMPT: frozenset[str] = frozenset()


def _module_tree() -> ast.Module:
    return ast.parse(_MAPPING_PATH.read_text())


def _registered_metrics(tree: ast.Module) -> set[str]:
    """The string keys of the ``METRICS`` dict literal (an annotated assign)."""
    for node in ast.walk(tree):
        target = None
        value = None
        if isinstance(node, ast.AnnAssign):
            target, value = getattr(node.target, "id", None), node.value
        elif isinstance(node, ast.Assign) and node.targets:
            target, value = getattr(node.targets[0], "id", None), node.value
        if target == "METRICS" and isinstance(value, ast.Dict):
            return {k.value for k in value.keys if isinstance(k, ast.Constant)}
    raise AssertionError("METRICS dict literal not found in mapping.py")


def _emitted_metrics(tree: ast.Module) -> set[str]:
    """Every literal metric name passed as ``_emit(samples, ref, <name>, value)``."""
    emitted: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_emit"
            and len(node.args) >= 3
            and isinstance(node.args[2], ast.Constant)
            and isinstance(node.args[2].value, str)
        ):
            emitted.add(node.args[2].value)
    return emitted


def test_every_registered_metric_is_emitted() -> None:
    """No METRIC may be registered without a mapper that emits it (#59 class)."""
    tree = _module_tree()
    registered = _registered_metrics(tree)
    emitted = _emitted_metrics(tree)

    dead = registered - emitted - _EMIT_EXEMPT
    assert not dead, (
        "These series are in METRICS but no _emit() ever produces a sample for "
        f"them -- registered-but-dead, the #59 shape: {sorted(dead)}. Emit them "
        "in map_device/map_client, or drop them from METRICS."
    )


def test_every_emitted_metric_is_registered() -> None:
    """The inverse the runtime enforces with a KeyError -- pinned here explicitly."""
    tree = _module_tree()
    registered = _registered_metrics(tree)
    emitted = _emitted_metrics(tree)

    unregistered = emitted - registered
    assert not unregistered, (
        "These names are passed to _emit() but absent from METRICS, so _emit's "
        f"METRICS[metric] lookup would KeyError at runtime: {sorted(unregistered)}."
    )


def test_the_contract_is_a_bijection_today() -> None:
    """Belt-and-braces: the two sets coincide, so neither arm has silent slack."""
    tree = _module_tree()
    assert _registered_metrics(tree) == _emitted_metrics(tree)
