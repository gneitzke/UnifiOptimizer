"""Shared dataclasses passed between netadmin layers.

Dependency-free (stdlib ``dataclasses`` + the enums in :mod:`netadmin.domain.types`).
Detectors emit :class:`Finding` objects; the issue engine owns lifecycle. A
:class:`Fix` is a proposed remediation the fix engine can render, dry-run, and
apply. Field shapes follow ``docs/ARCHITECTURE.md`` sections 4, 6, and 9.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from netadmin.domain.types import EntityType, FixState, Severity

# Epoch-second timestamps everywhere (UTC). Kept as int for SQLite affinity.
Timestamp = int

#: Entity types whose own name is only unique *within* their parent. Every AP
#: names its radios ``wifi0``/``wifi1`` and every switch has a ``Port 2``, so the
#: bare name identifies nothing on a site-wide surface.
CHILD_ENTITY_TYPES = frozenset({EntityType.RADIO.value, EntityType.PORT.value})


def entity_display_label(
    name: str, entity_type: Optional[str], parent_name: Optional[str] = None
) -> str:
    """How an entity should be *named to a human*: ``"Loft / wifi0"`` for a child.

    Four genuinely distinct saturated radios all render as ``wifi0``, and the
    owner reads four separate faults as duplicate rows (Gitea #44). Qualifying a
    radio or a port with the device it sits on is the whole disambiguation, and
    it stays short enough for a width-constrained table column -- which is why
    this is a prefix and not a parenthetical.

    Top-level entities (AP, switch, gateway, client, WLAN) already have unique
    names and come back untouched, as does a child whose parent is unresolved --
    a stale ``parent_id`` must degrade to ``wifi0``, never to ``"None / wifi0"``.
    A child an admin already named after its device ("Loft 5G" under "Loft") is
    left alone too: the prefix exists to identify, and that name identifies.

    The one rule, shared by the API serializer, the MCP tools, the report
    assembler and the dossier; the web UI mirrors it in ``entityLabel``.
    """
    if entity_type not in CHILD_ENTITY_TYPES or not parent_name:
        return name
    if parent_name.casefold() in name.casefold():
        return name
    return f"{parent_name} / {name}"


@dataclass
class Entity:
    """A tracked thing: ap | switch | gateway | client | port | radio | wlan.

    Mirrors the ``entities`` table. ``entity_id`` is ``None`` until the
    repository assigns one on insert. ``native_id`` is the stable controller
    identity (MAC for devices/clients, ``"<sw_mac>:<port_idx>"`` for ports,
    ``"<ap_mac>:<radio>"`` for radios).
    """

    entity_type: EntityType
    native_id: str
    site_id: str = "default"
    entity_id: Optional[int] = None
    parent_id: Optional[int] = None
    name: Optional[str] = None
    model: Optional[str] = None
    first_seen_ts: Optional[Timestamp] = None
    last_seen_ts: Optional[Timestamp] = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Fix:
    """A proposed remediation, produced by the fix planner (section 9).

    ``before``/``after`` capture the full state for revert; ``payload`` is the
    exact API body a dry-run would render. The daemon never self-applies in v1
    (``requires_user_action`` stays True).
    """

    action: str
    entity: Entity
    params: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    risk: str = ""
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    state: FixState = FixState.PROPOSED
    requires_user_action: bool = True


@dataclass
class Finding:
    """A detector's single observation (section 6).

    Detectors return ``list[Finding]``; ``dims`` supplies the extra dimensions
    that (with ``detector_key`` + entity + site) form the issue fingerprint.
    ``confounders_checked`` is the audit trail of false-positive traps tested.
    """

    detector_key: str
    entity: Entity
    severity: Severity
    title: str
    dims: dict[str, str] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    confounders_checked: list[str] = field(default_factory=list)
    proposed_fix: Optional[Fix] = None


__all__ = [
    "Timestamp",
    "Entity",
    "Fix",
    "Finding",
]
