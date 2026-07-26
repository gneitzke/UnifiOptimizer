"""Output-discipline primitives for the MCP tools (``docs/MCP_SERVER.md`` s3).

A model reading a tool result pays for every token, and a tool that dumps a
thousand rows of JSON buys nothing: the model summarises it back down anyway,
badly, having spent the context to do so. So the shape of a response is a
designed thing here, not whatever the repository happened to return:

* every response leads with a ``summary`` of at most two plain sentences;
* lists are capped (20 by default, 50 hard) and say so via ``truncated`` /
  ``total`` rather than silently ending;
* series are never raw -- they are downsampled to at most
  :data:`MAX_SERIES_POINTS` ``[iso_ts, value]`` pairs plus min/avg/max;
* timestamps are ISO-8601 UTC, with a human ``ago`` only on headline fields
  (an ``ago`` on all fifty rows of a list is noise);
* ``evidence`` blobs are trimmed to their top-level scalar keys;
* the whole payload is held under :data:`MAX_RESPONSE_BYTES`.

This module is pure: it takes rows and returns dicts, touches no database, and
is where :mod:`netadmin.mcp.tools` enforces the above *centrally* rather than
each tool re-deciding. :class:`Redactor` lives here too, since redaction is the
same kind of whole-payload pass.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "MAX_SERIES_POINTS",
    "MAX_RESPONSE_BYTES",
    "Redactor",
    "ago",
    "clamp_limit",
    "downsample",
    "duration",
    "guard_size",
    "iso",
    "lead_with_summary",
    "listing",
    "series_block",
    "stamp",
    "trim_evidence",
]

# Row caps (section 3). ``DEFAULT_LIMIT`` is what a caller gets for free;
# ``MAX_LIMIT`` is the ceiling no ``limit`` argument can raise.
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
# A chart's worth of points -- enough to see a shape, small enough to read.
MAX_SERIES_POINTS = 96
# Roughly 6k tokens of JSON. Past this the model is reading data it will not use.
MAX_RESPONSE_BYTES = 24_000

_MAC_RE = re.compile(r"\b([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\b")
# Fields whose *value* is a device/client name rather than free prose.
_NAME_FIELDS = frozenset({"name", "hostname", "host", "client_name", "device_name"})


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #
def iso(ts: Optional[int]) -> Optional[str]:
    """Epoch seconds -> ``2026-07-21T14:05:00Z``. ``None`` passes through.

    UTC always, per the project-wide rule that storage and transport are UTC and
    only a human display layer localises. The model is not a display layer.
    """
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(ts: Optional[int], now: int) -> Optional[str]:
    """Compact elapsed time: ``"4d 2h"``, ``"3h 12m"``, ``"42m"``, ``"just now"``.

    Two units at most -- "4 days 2 hours 15 minutes 3 seconds" is precision no one
    asked for. A future timestamp (clock skew, or a snooze deadline) renders as
    ``"in 5m"`` rather than a negative duration.
    """
    if ts is None:
        return None
    delta = int(now) - int(ts)
    if delta < 0:
        forward = ago(now, int(ts))
        return f"in {forward}" if forward and forward != "just now" else "just now"
    if delta < 60:
        return "just now"
    minutes, seconds = divmod(delta, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"


def duration(seconds: Optional[int]) -> Optional[str]:
    """A span of seconds as ``"3h 20m"``, sharing the two-unit rule of :func:`ago`.

    Sub-minute spans keep their seconds (``"18s"``) because "just now" is a
    statement about *when*, not about *how long*, and an issue that lasted
    eighteen seconds is a meaningfully different animal from one that lasted an
    hour.
    """
    if seconds is None:
        return None
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    return ago(0, total)


def stamp(ts: Optional[int], now: int) -> Optional[dict[str, Any]]:
    """A *headline* timestamp: ``{"at": iso, "ago": "4d 2h"}``, or ``None``.

    Only for the one or two timestamps a response is *about* (an onset, a last
    poll). Row-level timestamps use bare :func:`iso` -- carrying an ``ago`` on
    every row triples the size of a list to restate arithmetic the model can do.
    """
    if ts is None:
        return None
    return {"at": iso(ts), "ago": ago(ts, now)}


# --------------------------------------------------------------------------- #
# Row caps
# --------------------------------------------------------------------------- #
def clamp_limit(limit: Optional[Any], *, default: int = DEFAULT_LIMIT) -> int:
    """Coerce a caller's ``limit`` into ``[1, MAX_LIMIT]``, defaulting on junk.

    Never raises: a model that passes ``"all"`` or ``0`` or ``9999`` gets the
    documented behaviour instead of a tool error it has to recover from.
    """
    try:
        value = int(limit)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return min(value, MAX_LIMIT)


def listing(rows: Sequence[Any], limit: int, *, total: Optional[int] = None) -> dict[str, Any]:
    """The canonical capped-list block: ``{items, total, truncated}``.

    ``total`` overrides ``len(rows)`` for the case where the SQL already applied
    a ``LIMIT`` and the true count is known separately; otherwise the block is
    honest about exactly what it saw.
    """
    count = len(rows) if total is None else int(total)
    items = list(rows[:limit])
    return {"items": items, "total": count, "truncated": count > len(items)}


# --------------------------------------------------------------------------- #
# Series
# --------------------------------------------------------------------------- #
def downsample(points: Sequence[tuple[int, Optional[float]]], max_points: int) -> list[list[Any]]:
    """Fold ``(ts, value)`` pairs into at most ``max_points`` ``[iso_ts, value]``.

    Contiguous runs are averaged, and each emitted bucket is stamped with its
    *first* timestamp, so the series keeps its shape and its left edge stays
    where the data starts. ``None`` values are skipped inside a bucket; a bucket
    that is entirely ``None`` is dropped rather than emitted as a fake zero.
    """
    usable = [(int(ts), value) for ts, value in points if value is not None]
    if not usable:
        return []
    if len(usable) <= max_points:
        return [[iso(ts), _round(value)] for ts, value in usable]

    stride = math.ceil(len(usable) / max_points)
    out: list[list[Any]] = []
    for start in range(0, len(usable), stride):
        chunk = usable[start : start + stride]
        mean = sum(float(v) for _, v in chunk) / len(chunk)
        out.append([iso(chunk[0][0]), _round(mean)])
    return out


def series_block(
    points: Sequence[tuple[int, Optional[float]]],
    *,
    tier: str,
    max_points: int = MAX_SERIES_POINTS,
) -> dict[str, Any]:
    """A downsampled series plus the stats that survive downsampling.

    ``min``/``max`` are computed over the **full** input, not the downsampled
    output: averaging buckets flattens exactly the spikes a "when did this get
    bad" question is about, so the extremes are reported from the raw rows and
    the shape from the folded ones. ``tier`` names which storage tier answered,
    so the model can say "hourly rollups" rather than implying second-level truth.
    """
    values = [float(v) for _, v in points if v is not None]
    return {
        "tier": tier,
        "points": downsample(points, max_points),
        "min": _round(min(values)) if values else None,
        "avg": _round(sum(values) / len(values)) if values else None,
        "max": _round(max(values)) if values else None,
        "sample_count": len(values),
    }


def _round(value: Optional[float]) -> Optional[float]:
    """Three decimals: enough for dBm, percent and milliseconds, no float noise."""
    if value is None:
        return None
    return round(float(value), 3)


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #
def trim_evidence(evidence: Any, *, max_keys: int = 12) -> dict[str, Any]:
    """Keep an evidence blob's top-level scalars; summarise the rest.

    Detector evidence can nest arbitrarily (per-sample arrays, confounder
    sub-objects). The scalars are the readable part -- ``"rssi_p50": -78``,
    ``"threshold": -72`` -- and the nested parts are re-derivable through the
    metric tools, so a nested value becomes a one-word placeholder
    (``"[list of 240]"``) instead of a wall of JSON.
    """
    if not isinstance(evidence, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key, value in list(evidence.items())[:max_keys]:
        if value is None or isinstance(value, (bool, int, float, str)):
            out[str(key)] = value
        elif isinstance(value, (list, tuple)):
            out[str(key)] = f"[list of {len(value)}]"
        elif isinstance(value, Mapping):
            out[str(key)] = f"[object with {len(value)} keys]"
        else:
            out[str(key)] = str(value)
    return out


# --------------------------------------------------------------------------- #
# Redaction (opt-in via NETADMIN_MCP_REDACT=1)
# --------------------------------------------------------------------------- #
class Redactor:
    """Masks MACs and names in a whole payload, keeping ``entity_id``s intact.

    Off by default and deliberately so (``docs/MCP_SERVER.md`` section 6): the
    operator is the network owner, and this is metadata they already see in their
    own controller. When a user does turn it on -- a shared screen, a
    cloud-hosted client, a demo -- the guarantee is:

    * a MAC anywhere in any string keeps its OUI and loses the device half
      (``aa:bb:cc:11:22:33`` -> ``aa:bb:cc:xx:xx:xx``), so vendor context survives;
    * a name field becomes a stable pseudonym derived from the original
      (``client-7f3a`` for clients, ``device-7f3a`` otherwise), so the same device
      reads as the same device across every tool call in a session;
    * ``entity_id`` is never touched, so every drill-down still works.

    Known entity names are also replaced *inside free prose* (issue titles, event
    messages) via the ``known_names`` map, which is why the caller builds one
    redactor per request from the entity table rather than redacting field by
    field. A name that is not in the inventory cannot be found inside prose; that
    residual is the documented limit of this feature, not a bug in it.
    """

    def __init__(self, known_names: Optional[Mapping[str, str]] = None) -> None:
        # Longest first, so "Living Room AP" is replaced before "Living Room".
        self._known: list[tuple[str, str]] = sorted(
            ((name, pseudonym) for name, pseudonym in (known_names or {}).items() if name),
            key=lambda pair: -len(pair[0]),
        )

    @staticmethod
    def pseudonym(value: str, *, kind: str = "device") -> str:
        """Stable ``<kind>-<4 hex>`` handle for a name or MAC."""
        digest = hashlib.sha1(value.strip().lower().encode("utf-8")).hexdigest()[:4]
        return f"{kind}-{digest}"

    @staticmethod
    def mask_macs(text: str) -> str:
        """Replace every MAC in a string with its OUI plus ``:xx:xx:xx``.

        The first three octets are the vendor OUI, which is public information and
        the part that carries diagnostic value ("this is an Apple device"); the
        last three identify the unit and are what redaction is for.
        """
        return _MAC_RE.sub(lambda m: m.group(0)[:8].lower() + ":xx:xx:xx", text)

    def redact(self, payload: Any, *, kind: str = "device") -> Any:
        """Recursively redact a JSON-shaped payload, returning a new object."""
        if isinstance(payload, Mapping):
            # A sibling type/entity_type tells us whether children are clients.
            child_kind = kind
            declared = payload.get("type") or payload.get("entity_type")
            if isinstance(declared, str):
                child_kind = "client" if declared == "client" else "device"
            out: dict[str, Any] = {}
            for key, value in payload.items():
                if key in _NAME_FIELDS and isinstance(value, str) and value:
                    out[key] = self.pseudonym(value, kind=child_kind)
                elif key == "native_id" and isinstance(value, str):
                    out[key] = self._redact_text(value)
                else:
                    out[key] = self.redact(value, kind=child_kind)
            return out
        if isinstance(payload, list):
            return [self.redact(item, kind=kind) for item in payload]
        if isinstance(payload, str):
            return self._redact_text(payload)
        return payload

    def _redact_text(self, text: str) -> str:
        masked = self.mask_macs(text)
        for name, pseudonym in self._known:
            if name in masked:
                masked = masked.replace(name, pseudonym)
        return masked


def build_known_names(rows: Iterable[Any]) -> dict[str, str]:
    """``{entity name: pseudonym}`` from entity rows, for :class:`Redactor`.

    Skips names shorter than four characters: replacing "AP" or "sw1" everywhere
    it appears in prose corrupts more than it protects.
    """
    known: dict[str, str] = {}
    for row in rows:
        name = row["name"]
        if not name or len(str(name)) < 4:
            continue
        kind = "client" if row["entity_type"] == "client" else "device"
        known[str(name)] = Redactor.pseudonym(str(name), kind=kind)
    return known


# --------------------------------------------------------------------------- #
# Whole-payload guards
# --------------------------------------------------------------------------- #
def lead_with_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Reorder so ``summary`` is the first key, inserting a fallback if absent.

    JSON objects are ordered on the wire, and the first thing a model reads sets
    how it reads the rest. This is cheap insurance that a tool cannot ship a
    payload whose headline is buried under a hundred rows.
    """
    summary = payload.get("summary") or "No summary available."
    ordered: dict[str, Any] = {"summary": summary}
    for key, value in payload.items():
        if key != "summary":
            ordered[key] = value
    return ordered


def guard_size(payload: dict[str, Any], *, max_bytes: int = MAX_RESPONSE_BYTES) -> dict[str, Any]:
    """Shrink a payload until it fits ``max_bytes`` of JSON, and say that it did.

    Halves the longest list in the tree, repeatedly, so the cut falls on the
    bulkiest thing rather than on whatever happens to be last. ``summary`` and
    the scalar headline fields are never touched -- an over-budget response
    degrades into a shorter *answer*, never into a truncated string that no
    longer parses. If halving lists cannot get there (a single enormous string),
    the payload is replaced by its summary plus the note.
    """
    if _encoded_size(payload) <= max_bytes:
        return payload

    working = json.loads(json.dumps(payload, default=str))
    for _ in range(64):
        longest = _longest_list(working)
        if longest is None:
            break
        container, key, items = longest
        if len(items) <= 1:
            break
        container[key] = items[: max(1, len(items) // 2)]
        if _encoded_size(working) <= max_bytes:
            working["note"] = _SIZE_NOTE
            return working

    if _encoded_size(working) > max_bytes:
        return {
            "summary": payload.get("summary", "Result too large to return."),
            "note": _SIZE_NOTE,
        }
    working["note"] = _SIZE_NOTE
    return working


_SIZE_NOTE = (
    "Response was trimmed to fit the size budget; narrow the window or "
    "drill into a specific id for the full detail."
)


def _encoded_size(payload: Any) -> int:
    return len(json.dumps(payload, default=str).encode("utf-8"))


def _longest_list(node: Any) -> Optional[tuple[Any, Any, list[Any]]]:
    """``(container, key, list)`` for the longest list anywhere in the tree."""
    best: Optional[tuple[Any, Any, list[Any]]] = None
    stack: list[Any] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            items = current.items()
        elif isinstance(current, list):
            items = enumerate(current)  # type: ignore[assignment]
        else:
            continue
        for key, value in items:
            if isinstance(value, list):
                if best is None or len(value) > len(best[2]):
                    best = (current, key, value)
            if isinstance(value, (dict, list)):
                stack.append(value)
    return best
