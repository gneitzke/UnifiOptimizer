"""Fix-engine service: the one seam CLI and API drive (ARCHITECTURE.md 9).

The planner, applier, verifier, writer, and reader are each pure/narrow; this
module is the thin orchestration that turns a *tracked issue* into a fix action:

#. reconstruct the detector :class:`Finding` from the stored issue (its
   ``detector_key`` + entity + evidence -- the same inputs the planner saw live);
#. read the target device **read-only** (:class:`DeviceReader`) so the planner can
   render an exact, whole-``radio_table`` payload;
#. :func:`plan_fix` -> a :class:`FixPlan`;
#. dry-run render (the default, and the only thing a GET ever does), or a gated
   real apply, or a revert -- through the :class:`Applier`.

Safety properties preserved end to end:

* **dry-run is inert**: :meth:`dry_run` only renders; it never constructs a writer
  and never mutates the issue.
* **apply is drift-bound**: the human's ``confirm_token`` (from the dry-run they
  read) is passed straight to the applier, which recomputes it from the freshly
  re-read device. If the device changed since review, the tokens differ and the
  apply is refused -- the human never confirms bytes that silently changed.
* **apply arms verification**: only a genuinely-applied change records the
  ``fix_applied`` event (:class:`Verifier`), starting the section-7 window.

The controller seams (:class:`DeviceReader`, :class:`ControllerWriter`) are
injected, so the entire lifecycle runs offline against fakes. A real apply needs a
:class:`~netadmin.fixes.writer.RealControllerWriter`, which only the explicit CLI
``--apply --confirm`` and the UI apply button ever build; the daemon never does.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from netadmin.domain.entities import Entity, Finding
from netadmin.domain.types import EntityType, Severity
from netadmin.fixes.applier import Applier
from netadmin.fixes.models import (
    ApplyResult,
    DryRunResult,
    FixError,
    FixPlan,
    VerificationResult,
    WriteResult,
)
from netadmin.fixes.planner import PHYSICAL_REFUSAL_KEYS, plan_fix
from netadmin.fixes.reader import DeviceReader, device_mac_of
from netadmin.fixes.verifier import Verifier
from netadmin.fixes.writer import ControllerWriter
from netadmin.issues.engine import IssueEngine
from netadmin.logging import get_logger
from netadmin.store.repository import Repository

__all__ = ["FixService", "IssueNotFound", "FixSeams", "build_fix_seams"]

_log = get_logger("fixes.service")

# The site-scoped RF-environment anchor a per-band wifi issue is fingerprinted on
# (``netadmin.detect.detectors.wifi.RF_ENV_TYPE`` / ``RF_ENV_PREFIX``). Mirrored as
# literals rather than imported: ``fixes`` does not depend on ``detect``, the same
# layering rule ``detect`` follows for ``ingest``'s entity-type literals.
_RF_ENV_TYPE = "rf_env"
_RF_ENV_PREFIX = "rf:"


def build_fix_seams(settings: Any, *, for_apply: bool) -> "FixSeams":
    """Build the real controller seams from configured credentials.

    Returns a read-only :class:`RealDeviceReader` always, and -- only when
    ``for_apply`` -- a :class:`RealControllerWriter` over the *same* client (the
    lone object in the rebuild that can send a mutation). The client connects
    lazily on first use; ``closer`` aclose()s it. Raises ``RuntimeError`` when the
    controller is unconfigured, so the caller can refuse cleanly rather than half
    build a seam. Constructing a writer here is the explicit, human-initiated
    intent to mutate; the daemon's read paths call this with ``for_apply=False``.
    """
    from netadmin.fixes.reader import RealDeviceReader
    from netadmin.fixes.writer import RealControllerWriter
    from netadmin.ingest.factory import build_endpoints

    endpoints, client = build_endpoints(settings)
    _ = endpoints  # the reader talks to the client directly (raw stat/device)
    reader = RealDeviceReader(client)
    writer = RealControllerWriter(client) if for_apply else None

    async def _close() -> None:
        for name in ("aclose", "close"):
            fn = getattr(client, name, None)
            if fn is not None:
                result = fn()
                if hasattr(result, "__await__"):
                    await result
                return

    return FixSeams(reader=reader, writer=writer, closer=_close)


class IssueNotFound(FixError):
    """The issue id does not exist (surfaces as a 404 / CLI error)."""


class IssueNotActionable(FixError):
    """The issue is no longer live, so applying its fix would act on stale state.

    An issue's evidence is refreshed only while its detector FIRES; once the
    condition stops, evidence freezes at its last-firing value while the network
    keeps moving. Nothing else in the gate chain notices for an auto-channel
    radio: config stays "auto", so the precondition passes and the confirm token
    stays valid indefinitely. Refusing resolved/resolving issues bounds the apply
    window to the life of the problem itself — a saved token for a self-resolved
    issue must not pin a radio weeks later on evidence about a channel it left.
    Dry-run stays open: previewing a stale plan is harmless and useful.
    """


@dataclass
class FixSeams:
    """The injected controller seams a :class:`FixService` needs.

    ``reader`` is the read-only device source (required for config-change plans);
    ``writer`` is the mutation seam (present only when a real apply is intended --
    a dry-run leaves it ``None``). ``closer`` tears down any connection the seams
    hold, awaited by the caller when the request is done.
    """

    reader: Optional[DeviceReader] = None
    writer: Optional[ControllerWriter] = None
    closer: Optional[Callable[[], Any]] = None


class FixService:
    """Drive dry-run / apply / revert for a tracked issue's remediation.

    ``device_reader`` is the read-only snapshot source; ``writer`` is the mutation
    seam (leave ``None`` for a preview-only service -- then :meth:`apply` refuses
    with :class:`~netadmin.fixes.models.WriterRequired`, exactly as the applier
    does). ``store`` owns the ledger; ``engine`` owns the fix-verification window.
    """

    def __init__(
        self,
        store: Repository,
        engine: IssueEngine,
        *,
        device_reader: Optional[DeviceReader] = None,
        writer: Optional[ControllerWriter] = None,
        applier: Optional[Applier] = None,
        now_fn: Optional[Callable[[], int]] = None,
    ) -> None:
        self._store = store
        self._engine = engine
        self._reader = device_reader
        self._now_fn = now_fn or (lambda: int(time.time()))
        self._applier = applier or Applier(store, writer, now_fn=self._now_fn)
        self._verifier = Verifier(engine)

    # ------------------------------------------------------------------ #
    # Plan building
    # ------------------------------------------------------------------ #
    async def build_plan(self, issue_id: int) -> FixPlan:
        """Reconstruct the finding, read the device(s) read-only, and plan the fix.

        For an advisory detector (physical fix) nothing is read: there is nothing
        to render, so we do not touch the controller at all. A site-scoped issue
        (the per-band channel plan) names its subjects in evidence rather than on
        an entity, so every distinct device among them is read once -- read-only,
        through the same ``stat/device`` seam a single-radio plan uses.
        """
        finding = self._finding_for_issue(issue_id)
        devices: dict[str, dict[str, Any]] = {}
        if finding.detector_key not in PHYSICAL_REFUSAL_KEYS and self._reader is not None:
            for mac in _plan_target_macs(finding):
                device = await self._reader.read_device(mac)
                if device is not None:
                    devices[mac] = device
        own = devices.get(device_mac_of(finding.entity.native_id).lower())
        return plan_fix(finding, device=own, devices=devices, issue_id=issue_id)

    async def dry_run(self, issue_id: int) -> DryRunResult:
        """Render the exact plan for an issue. Sends nothing, mutates nothing.

        This is what ``GET /api/issues/{id}/fix-plan`` and ``netadmin fix <id>``
        return. It never constructs a writer and never writes an issue event -- a
        preview is a pure read.
        """
        plan = await self.build_plan(issue_id)
        return self._applier.render(plan)

    # ------------------------------------------------------------------ #
    # Apply (gated) + revert
    # ------------------------------------------------------------------ #
    async def apply(self, issue_id: int, *, confirm_token: str) -> ApplyResult:
        """Apply an issue's fix, fully gated, then arm verification on success.

        Re-reads the device, rebuilds the plan, and passes the human's
        ``confirm_token`` to the applier -- which recomputes the token from the
        rebuilt plan, so a device that changed since the dry-run refuses here. The
        applier also re-checks every precondition against fresh live state and the
        min-RSSI rail before a single call is sent. Only an applied change records
        the ``fix_applied`` event that starts the section-7 verification window.
        """
        row = self._store.get_issue(issue_id)
        if row is None:
            raise IssueNotFound(f"issue {issue_id} not found")
        state = str(row["state"])
        if state in ("resolving", "resolved"):
            raise IssueNotActionable(
                f"issue {issue_id} is {state}: its evidence is no longer live, so the "
                "fix would apply against the network as it was, not as it is. If the "
                "problem returns, the next detection pass re-raises it with fresh "
                "evidence and a fresh plan."
            )
        plan = await self.build_plan(issue_id)
        current_state = await self._read_current_state(plan)
        result = await self._applier.apply(
            plan,
            dry_run=False,
            confirm_token=confirm_token,
            current_state=current_state,
        )
        # Arm on "did we change the network at all", NOT on "did every step land".
        # A multi-step plan that applies step 1 and fails step 2 reports
        # applied=False while carrying real change_ids: the controller was written
        # to and the ledger holds those rows. Keying arming off `applied` left that
        # case unverified forever, which is the worst of both worlds -- a live
        # change nothing is watching. Single-step plans are unaffected, since there
        # applied and change_ids agree.
        if result.change_ids and plan.issue_id is not None:
            self._verifier.arm(
                plan.issue_id,
                self._now_fn(),
                detail={
                    "action": plan.steps[0].action.value if plan.steps else None,
                    "change_ids": result.change_ids,
                    "partial": not result.applied,
                },
            )
        return result

    async def revert(self, change_id: int) -> WriteResult:
        """Restore a change's captured before-state, re-checked against live state.

        A revert is a controller mutation, so it is re-gated exactly like a forward
        apply: the target device is re-read (read-only) so the applier can run the
        absolute min-RSSI rail against *current* values. This is what stops a revert
        of a min-RSSI removal from silently re-enabling min-RSSI -- and, critically,
        from doing so on an AP that has since become a mesh uplink, which would
        re-create the latent outage the detector guards against. A device we cannot
        read leaves the applier with no fresh state, and it refuses rather than
        restore blind.
        """
        current_radios, is_mesh = await self._read_revert_state(change_id)
        return await self._applier.revert(
            change_id, current_radios=current_radios, is_mesh_uplink=is_mesh
        )

    async def _read_revert_state(
        self, change_id: int
    ) -> tuple[Optional[dict[str, dict[str, Any]]], bool]:
        """Fresh live radio state + mesh-uplink posture for a change's device.

        Resolves the change's device (via its ledgered entity), re-reads it through
        the read-only :class:`DeviceReader`, and returns ``({radio_code: {attr:
        value}}, is_mesh_uplink)``. Returns ``(None, False)`` whenever the device
        cannot be resolved or read -- the applier treats a radio restore with no
        fresh state as a refusal, which is the safe outcome.
        """
        if self._reader is None:
            return None, False
        row = self._store.get_change(change_id)
        if row is None:
            return None, False
        entity_id = row["entity_id"]
        if entity_id is None:
            return None, False
        entity_row = self._store.get_entity(int(entity_id))
        if entity_row is None:
            return None, False
        mac = device_mac_of(str(entity_row["native_id"]))
        device = await self._reader.read_device(mac)
        if device is None:
            return None, False
        radios = {
            str(r.get("radio")): dict(r)
            for r in (device.get("radio_table") or [])
            if r.get("radio") is not None
        }
        return radios, _device_is_mesh_uplink(device)

    def verification(self, issue_id: int) -> VerificationResult:
        """Where an applied fix sits in its verification window (section 7)."""
        return self._verifier.check(issue_id, now=self._now_fn())

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _finding_for_issue(self, issue_id: int) -> Finding:
        """Rebuild the detector :class:`Finding` from the stored issue row.

        The planner is driven by ``detector_key``, the entity, and the ``evidence``
        blob (``band`` / ``subtype`` / ``channel`` / ``poe_reboot_loop`` ...) -- all
        persisted on the issue. ``dims`` are folded into the fingerprint and not
        stored separately; the planner only reads them as an evidence fallback, and
        evidence carries the same keys, so an empty ``dims`` is faithful.

        A site-scoped issue carries a NULL ``entity_id`` (the ``rf_env``
        pseudo-entity is an anchor, not a row), so its entity is rebuilt from
        evidence instead of looked up.
        """
        row = self._store.get_issue(issue_id)
        if row is None:
            raise IssueNotFound(f"issue {issue_id} not found")
        evidence = _decode_evidence(row["evidence"])
        entity_id = row["entity_id"]
        if entity_id is None:
            entity = self._site_scoped_entity(issue_id, evidence)
        else:
            entity_row = self._store.get_entity(int(entity_id))
            if entity_row is None:
                raise FixError(f"issue {issue_id} entity {entity_id} is unknown")
            entity = _entity_from_row(entity_row)
        dims_raw = evidence.get("dims")
        dims = {str(k): str(v) for k, v in dims_raw.items()} if isinstance(dims_raw, dict) else {}
        return Finding(
            detector_key=str(row["detector_key"]),
            entity=entity,
            severity=Severity(str(row["severity"])),
            title=str(row["title"]),
            dims=dims,
            evidence=evidence,
        )

    def _site_scoped_entity(self, issue_id: int, evidence: dict[str, Any]) -> Entity:
        """Rebuild the ``rf_env`` pseudo-entity a per-band issue is anchored on.

        The issues table stores an ``entity_id``, not a native id, and a site-scoped
        issue has none -- so the anchor is reconstructed from the documented
        convention it was fingerprinted under: ``rf:<band>``, with the band on the
        issue's own evidence. Evidence without a band is not a site-scoped RF issue
        and gets the plain refusal.
        """
        band = evidence.get("band")
        if band is None:
            raise FixError(f"issue {issue_id} has no entity; nothing to plan a fix against")
        return Entity(
            entity_type=_RF_ENV_TYPE,  # type: ignore[arg-type]
            native_id=f"{_RF_ENV_PREFIX}{band}",
            site_id=str(getattr(self._store, "site_id", "default")),
            name=f"{band} GHz RF environment",
        )

    async def _read_current_state(self, plan: FixPlan) -> dict[str, dict[str, Any]]:
        """Fresh live values for every step's precondition, keyed by target.

        Reads the device once per distinct device MAC and extracts only the
        attributes the precondition expects, type-aligned to the expected value so
        a controller that stringifies a channel does not read as spurious drift.
        A device we cannot read is simply absent -- the applier treats a missing
        target as drift and refuses, which is the safe outcome.
        """
        state: dict[str, dict[str, Any]] = {}
        if self._reader is None:
            return state
        device_cache: dict[str, Optional[dict[str, Any]]] = {}
        for step in plan.steps:
            target = step.precondition.target_native_id
            expected = step.precondition.expected
            if not expected or target in state:
                continue
            mac = device_mac_of(target)
            if mac not in device_cache:
                device_cache[mac] = await self._reader.read_device(mac)
            device = device_cache[mac]
            if device is None:
                continue  # absent -> drift, refused by the applier
            state[target] = _extract_target_attrs(device, target, expected)
        return state


# --------------------------------------------------------------------------- #
# Row -> domain helpers
# --------------------------------------------------------------------------- #
def _plan_target_macs(finding: Finding) -> list[str]:
    """Every device MAC a plan for ``finding`` could need, deduped, in order.

    An entity-scoped finding needs exactly its own device, which is the whole of
    the historical behaviour. A site-scoped one (``rf:<band>``) names its subjects
    in evidence instead -- the radios in the conflict groups -- so their devices
    are what the planner must see; its own anchor carries no MAC and is skipped.
    Reads stay read-only either way.
    """
    natives = [finding.entity.native_id]
    for group in finding.evidence.get("conflict_groups") or ():
        if not isinstance(group, dict):
            continue
        for radio in group.get("radios") or ():
            native = radio.get("native_id") if isinstance(radio, dict) else None
            if native:
                natives.append(str(native))

    macs: list[str] = []
    for native in natives:
        if native.count(":") < 5:
            continue  # no device MAC in this id (an rf:<band> anchor): nothing to read
        mac = device_mac_of(native).lower()
        if mac not in macs:
            macs.append(mac)
    return macs


def _entity_from_row(row: Any) -> Entity:
    """Build a domain :class:`Entity` from an ``entities`` row."""
    meta = _decode_evidence(row["meta"]) if _has_key(row, "meta") else {}
    return Entity(
        entity_type=EntityType(str(row["entity_type"])),
        native_id=str(row["native_id"]),
        site_id=str(row["site_id"]) if _has_key(row, "site_id") else "default",
        entity_id=int(row["entity_id"]),
        parent_id=int(row["parent_id"]) if row["parent_id"] is not None else None,
        name=row["name"],
        model=row["model"] if _has_key(row, "model") else None,
        meta=meta if isinstance(meta, dict) else {},
    )


def _extract_target_attrs(
    device: dict[str, Any], native_id: str, expected: dict[str, Any]
) -> dict[str, Any]:
    """Pull each expected attr for the target radio/port from a raw device.

    The native id's trailing segment is a radio band code (``ng``/``na``/``6e``)
    or a port index; the matching ``radio_table`` / ``port_table`` entry supplies
    the live values, type-aligned to the expected value.
    """
    entry = _find_target_entry(device, native_id)
    if entry is None:
        return {}
    return {key: _coerce_like(entry.get(key), exp) for key, exp in expected.items()}


def _find_target_entry(device: dict[str, Any], native_id: str) -> Optional[dict[str, Any]]:
    suffix = native_id.rsplit(":", 1)[-1]
    for radio in device.get("radio_table") or []:
        if radio.get("radio") == suffix:
            return radio
    idx = _as_int(suffix)
    if idx is not None:
        for port in device.get("port_table") or []:
            if _as_int(port.get("port_idx")) == idx:
                return port
    return None


def _coerce_like(raw: Any, exemplar: Any) -> Any:
    """Return ``raw`` coerced to ``exemplar``'s type so equality is type-safe.

    Only the *type* is aligned, never the value: a genuinely changed channel/mode
    still compares unequal and is caught as drift. bool is checked before int
    (``bool`` is an ``int`` subclass) so ``min_rssi_enabled`` compares as truthiness.
    """
    if raw is None:
        return None
    if isinstance(exemplar, bool):
        return _truthy(raw)
    if isinstance(exemplar, int):
        coerced = _as_int(raw)
        return coerced if coerced is not None else raw
    if isinstance(exemplar, float):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return raw
    if isinstance(exemplar, str):
        return str(raw)
    return raw


def _device_is_mesh_uplink(device: dict[str, Any]) -> bool:
    """Whether a raw ``stat/device`` object is on (or configured for) a mesh uplink.

    Mirrors the wifi detector's ``_is_mesh_ap``: a truthy ``mesh_enabled`` flag, or
    an ``uplink.type`` of ``wireless`` (the live wireless-backhaul signal). Read
    from the same fresh device the applier re-checks, so the revert rail judges the
    AP's *current* posture, not the stale one captured when the fix was applied.
    """
    if _truthy(device.get("mesh_enabled")):
        return True
    uplink = device.get("uplink")
    if isinstance(uplink, dict):
        return str(uplink.get("type") or "").lower() == "wireless"
    return False


def _decode_evidence(raw: Any) -> dict[str, Any]:
    import json

    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _has_key(row: Any, key: str) -> bool:
    try:
        return key in row.keys()
    except AttributeError:
        return hasattr(row, key)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
