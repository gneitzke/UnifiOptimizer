"""Shared enums for the netadmin domain.

Dependency-free by design (stdlib only): every layer may import these without
pulling in the store, ingest, or server. String values are the canonical
serialized forms used in the SQLite schema (see ``docs/ARCHITECTURE.md``
sections 4, 6, and 7), so ``EntityType.AP.value == "ap"`` maps straight to the
``entities.entity_type`` column.
"""

from __future__ import annotations

from enum import Enum


class EntityType(str, Enum):
    """What an entity is. Mirrors ``entities.entity_type``."""

    AP = "ap"
    SWITCH = "switch"
    GATEWAY = "gateway"
    CLIENT = "client"
    PORT = "port"
    RADIO = "radio"
    WLAN = "wlan"


class Severity(str, Enum):
    """Finding / issue severity. Mirrors ``issues.severity`` (lowercase)."""

    P1 = "p1"
    P2 = "p2"
    P3 = "p3"


class Cadence(str, Enum):
    """How often a detector runs (see the ``Detector`` protocol, section 6)."""

    FAST = "fast"  # every poll cycle
    WINDOW = "window"  # ~15-minute rolling window
    DAILY = "daily"  # config audits


class IssueState(str, Enum):
    """Issue-lifecycle states. Mirrors ``issues.state`` (section 7)."""

    PENDING = "pending"
    ACTIVE = "active"
    RESOLVING = "resolving"
    RESOLVED = "resolved"


class FixState(str, Enum):
    """Fix-verification states. Mirrors ``issues.fix_state`` (sections 7 & 9)."""

    PROPOSED = "proposed"
    APPLIED = "applied"
    VERIFIED = "verified"
    FAILED = "failed"


__all__ = [
    "EntityType",
    "Severity",
    "Cadence",
    "IssueState",
    "FixState",
]
