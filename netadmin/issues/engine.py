"""The issue-lifecycle engine (``docs/ARCHITECTURE.md`` section 7).

Pure logic. The engine takes findings plus an injected ``now`` (never
``datetime.now()`` inside logic) and drives the state machine through an
:class:`~netadmin.issues.models.IssueRepository`. It performs no I/O of its own.

State machine, per fingerprint::

    (fire, no open issue)        -> create pending  (occurrences = 1)
    pending + M consecutive fires -> active
    pending + clear               -> discarded (never a real issue)
    active + clear                -> resolving (clear_streak = 1)
    resolving + clear (streak==K) -> resolved
    resolving + fire              -> snap back to active, streak reset
    resolved + fire within 24 h   -> reopen the same row (parentage preserved)

Cross-cutting behaviours: inhibition freezes both directions while a cause is in
effect; UNKNOWN verdicts advance nothing; a ``fix_applied`` event arms a 48 h
verification window (resolve inside it -> ``fix_verified``, refire ->
``fix_failed``); snooze/ack only set mute flags; every state change and fix event
writes an ``issue_events`` row and is broadcast to the ``on_transition``
callbacks (fire-and-forget — a raising callback never affects the engine).
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Iterable, Optional

from netadmin.domain.entities import Entity, Finding, Timestamp
from netadmin.domain.types import FixState, IssueState
from netadmin.issues.inhibition import (
    DEFAULT_RULES,
    InhibitionContext,
    InhibitionRule,
    InhibitionScope,
)
from netadmin.issues.models import (
    EngineConfig,
    EventKind,
    Issue,
    IssueEvent,
    IssueRepository,
    Transition,
)
from netadmin.logging import get_logger

_log = get_logger("issues.engine")

TransitionCallback = Callable[[Transition], None]


def fingerprint(finding: Finding) -> str:
    """``sha1(detector_key | site_id | entity native_id | sorted(dims))`` (section 7).

    The dims are appended in sorted-key order as ``key=value`` segments, so the
    hash is stable regardless of dict insertion order. One open issue exists per
    fingerprint (the partial unique index enforces it in the store).
    """
    return _fingerprint(
        finding.detector_key,
        finding.entity.site_id,
        finding.entity.native_id,
        finding.dims,
    )


def _fingerprint(detector_key: str, site_id: str, native_id: str, dims: dict[str, str]) -> str:
    parts: list[str] = [detector_key, site_id, native_id]
    for key in sorted(dims):
        parts.append(f"{key}={dims[key]}")
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class IssueEngine:
    """Owns the issue state machine. Construct once, call :meth:`process_cycle`
    each evaluation cycle.
    """

    def __init__(
        self,
        repository: IssueRepository,
        *,
        config: Optional[EngineConfig] = None,
        rules: Iterable[InhibitionRule] = DEFAULT_RULES,
        on_transition: Optional[Iterable[TransitionCallback]] = None,
    ) -> None:
        self.repo = repository
        self.cfg = config or EngineConfig()
        self._rules = tuple(rules)
        self._callbacks: list[TransitionCallback] = list(on_transition or [])
        self._global_cause_keys = {
            r.cause_key for r in self._rules if r.suppressed_scope is InhibitionScope.GLOBAL
        }
        self._children_cause_keys = {
            r.cause_key for r in self._rules if r.suppressed_scope is InhibitionScope.CHILDREN
        }

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def add_callback(self, callback: TransitionCallback) -> None:
        """Register an ``on_transition`` callback (fire-and-forget).

        Appends unconditionally, so a subscriber that can be restarted inside one
        process must pair every registration with :meth:`remove_callback` on the
        way out; otherwise a stop/start cycle leaves two copies registered and
        every transition is delivered twice.
        """
        self._callbacks.append(callback)

    def remove_callback(self, callback: TransitionCallback) -> bool:
        """Unregister a callback. Returns True when something was removed.

        Idempotent: removing a callback that was never registered (a subscriber
        stopped before it ever started, or stopped twice) is a silent no-op. Every
        equal registration is dropped, so a subscriber that double-registered
        before this existed still ends up fully detached. The list is rebuilt
        rather than mutated, so a callback that unregisters itself mid-notify
        cannot perturb the delivery in flight.
        """
        remaining = [cb for cb in self._callbacks if cb != callback]
        removed = len(remaining) != len(self._callbacks)
        self._callbacks = remaining
        return removed

    def process_cycle(
        self,
        now: Timestamp,
        findings: Iterable[Finding] = (),
        *,
        cleared: Iterable[str] = (),
        unknown: Iterable[str] = (),
    ) -> list[Transition]:
        """Apply one evaluation cycle and return the transitions it produced.

        ``findings`` are FIRE verdicts. ``cleared`` are fingerprints the detector
        evaluated with the problem absent (advance the clear streak). ``unknown``
        are fingerprints the detector could not evaluate (a gap / < 50 % samples)
        — they advance nothing, exactly like a fingerprint left unmentioned.
        FIRE wins over CLEAR for the same fingerprint in one cycle.
        """
        findings = list(findings)
        transitions: list[Transition] = []
        inhibition = self._build_inhibition(now, findings)

        fired_fingerprints: set[str] = set()
        for finding in findings:
            fp = fingerprint(finding)
            fired_fingerprints.add(fp)
            self._process_fire(finding, fp, now, inhibition, transitions)

        for fp in cleared:
            if fp in fired_fingerprints:
                continue  # a fire this cycle overrides a stale clear
            self._process_clear(fp, now, inhibition, transitions)

        # ``unknown`` is intentionally a no-op: a gap advances nothing in either
        # direction. It is accepted explicitly so callers can be unambiguous.
        _ = list(unknown)
        return transitions

    def fire(self, finding: Finding, now: Timestamp) -> list[Transition]:
        """Convenience: a single-finding cycle."""
        return self.process_cycle(now, findings=[finding])

    def clear(self, fingerprint_value: str, now: Timestamp) -> list[Transition]:
        """Convenience: a single-clear cycle."""
        return self.process_cycle(now, cleared=[fingerprint_value])

    # --- operator / fix-engine hooks (mute flags + fix trail, never evaluation) ---
    def ack(
        self, issue_id: int, now: Timestamp, detail: Optional[dict[str, Any]] = None
    ) -> Optional[Transition]:
        """Acknowledge an issue: set ``ack_ts`` (mutes notifications only)."""
        issue = self.repo.get_issue(issue_id)
        if issue is None:
            return None
        issue.ack_ts = now
        self.repo.update_issue(issue)
        return self._emit([], issue, EventKind.ACKED, now, None, None, detail or {})

    def snooze(
        self,
        issue_id: int,
        until_ts: Timestamp,
        now: Timestamp,
        detail: Optional[dict[str, Any]] = None,
    ) -> Optional[Transition]:
        """Snooze notifications until ``until_ts`` (evaluation is untouched)."""
        issue = self.repo.get_issue(issue_id)
        if issue is None:
            return None
        issue.snooze_until_ts = until_ts
        self.repo.update_issue(issue)
        payload = {"until_ts": until_ts, **(detail or {})}
        return self._emit([], issue, EventKind.SNOOZED, now, None, None, payload)

    def propose_fix(
        self, issue_id: int, now: Timestamp, detail: Optional[dict[str, Any]] = None
    ) -> Optional[Transition]:
        """Record that a fix was proposed for this issue."""
        return self._set_fix_state(issue_id, FixState.PROPOSED, EventKind.FIX_PROPOSED, now, detail)

    def apply_fix(
        self, issue_id: int, now: Timestamp, detail: Optional[dict[str, Any]] = None
    ) -> Optional[Transition]:
        """Record that a fix was applied. This arms the verification window: the
        ``fix_applied`` event's timestamp is what :meth:`_fix_armed` reads back.
        """
        return self._set_fix_state(issue_id, FixState.APPLIED, EventKind.FIX_APPLIED, now, detail)

    def investigated(
        self, issue_id: int, now: Timestamp, detail: Optional[dict[str, Any]] = None
    ) -> Optional[Transition]:
        """Record that an LLM/manual investigation was attached to the issue."""
        issue = self.repo.get_issue(issue_id)
        if issue is None:
            return None
        return self._emit([], issue, EventKind.INVESTIGATED, now, None, None, detail or {})

    # ------------------------------------------------------------------ #
    # FIRE handling
    # ------------------------------------------------------------------ #
    def _process_fire(
        self,
        finding: Finding,
        fp: str,
        now: Timestamp,
        inhibition: InhibitionContext,
        transitions: list[Transition],
    ) -> None:
        if inhibition.is_inhibited(finding.detector_key, finding.entity, finding.entity.entity_id):
            return  # frozen: no creation, no escalation, no bookkeeping

        open_issue = self.repo.get_open_issue_by_fingerprint(fp)
        if open_issue is not None:
            self._apply_fire_to_open(open_issue, finding, now, transitions)
            return

        reopened = self.repo.get_recent_resolved_issue(fp, now - self.cfg.reopen_window_s)
        if reopened is not None:
            self._reopen(reopened, finding, now, transitions)
        else:
            self._create_pending(finding, fp, now, transitions)

    def _apply_fire_to_open(
        self,
        issue: Issue,
        finding: Finding,
        now: Timestamp,
        transitions: list[Transition],
    ) -> None:
        prev_state = issue.state
        # Upsert bookkeeping (section 7): bump occurrences/last_seen, refresh
        # evidence, reset the clear streak.
        issue.occurrences += 1
        issue.last_seen_ts = now
        issue.evidence = dict(finding.evidence)
        issue.severity = finding.severity
        issue.title = finding.title
        issue.clear_streak = 0

        if prev_state is IssueState.PENDING:
            m = self.cfg.m_for(issue.detector_key)
            if issue.occurrences >= m:
                issue.state = IssueState.ACTIVE
                self.repo.update_issue(issue)
                self._emit(
                    transitions,
                    issue,
                    EventKind.ESCALATED,
                    now,
                    IssueState.PENDING,
                    IssueState.ACTIVE,
                    {"reason": "m_reached", "m": m, "occurrences": issue.occurrences},
                )
            else:
                self.repo.update_issue(issue)  # still pending, no transition event
            return

        if prev_state is IssueState.ACTIVE:
            # A fix applied while ACTIVE arms the verification window. If the issue
            # keeps firing (the condition never clears) past that window, the fix
            # did not hold: flip APPLIED -> FAILED so the loop the doc "closes"
            # actually reports the most common failure mode (section 7). Marking
            # only after the window expires -- not on the first refire -- gives the
            # fix its full window to take effect.
            if self._fix_window_expired(issue, now):
                self._mark_fix_failed(issue, now, transitions, reason="still_firing_after_window")
            else:
                self.repo.update_issue(issue)  # continued fire, no state change
            return

        # RESOLVING: a fire snaps back to active and resets the clear streak.
        armed = self._fix_armed(issue, now)
        issue.state = IssueState.ACTIVE
        self.repo.update_issue(issue)
        self._emit(
            transitions,
            issue,
            EventKind.ESCALATED,
            now,
            IssueState.RESOLVING,
            IssueState.ACTIVE,
            {"reason": "refire_during_resolving"},
        )
        if armed:
            self._mark_fix_failed(issue, now, transitions, reason="refire_during_resolving")

    def _reopen(
        self,
        issue: Issue,
        finding: Finding,
        now: Timestamp,
        transitions: list[Transition],
    ) -> None:
        # A reopen inside the fix window means the fix did not hold. This covers
        # a premature VERIFIED too: an issue that resolved quickly (-> verified),
        # then refired while still inside the window, is a fix failure, not a
        # held fix -- so we downgrade it (section 7). Checked against the applied
        # event regardless of the intervening fix_state.
        failed_fix = issue.fix_state in (
            FixState.APPLIED,
            FixState.VERIFIED,
        ) and self._within_fix_window(issue, now)
        # Reuse the original row: parentage (first_seen_ts) is preserved so the
        # age keeps counting; this is not a fresh issue.
        issue.state = IssueState.ACTIVE
        issue.resolved_ts = None
        issue.clear_streak = 0
        issue.occurrences += 1
        issue.last_seen_ts = now
        issue.evidence = dict(finding.evidence)
        issue.severity = finding.severity
        issue.title = finding.title
        issue.reopened_from = issue.id
        self.repo.update_issue(issue)
        self._emit(
            transitions,
            issue,
            EventKind.REOPENED,
            now,
            IssueState.RESOLVED,
            IssueState.ACTIVE,
            {"reopened_from": issue.id},
        )
        if failed_fix:
            self._mark_fix_failed(issue, now, transitions, reason="refire_after_apply")

    def _create_pending(
        self,
        finding: Finding,
        fp: str,
        now: Timestamp,
        transitions: list[Transition],
    ) -> None:
        issue = Issue(
            fingerprint=fp,
            detector_key=finding.detector_key,
            severity=finding.severity,
            state=IssueState.PENDING,
            first_seen_ts=now,
            last_seen_ts=now,
            title=finding.title,
            entity_id=finding.entity.entity_id,
            evidence=dict(finding.evidence),
            occurrences=1,
            clear_streak=0,
        )
        issue = self.repo.insert_issue(issue)
        self._emit(
            transitions,
            issue,
            EventKind.DETECTED,
            now,
            None,
            IssueState.PENDING,
            {"severity": issue.severity.value},
        )
        # M == 1 detectors (controller_down / device_down) activate immediately.
        m = self.cfg.m_for(finding.detector_key)
        if issue.occurrences >= m:
            issue.state = IssueState.ACTIVE
            self.repo.update_issue(issue)
            self._emit(
                transitions,
                issue,
                EventKind.ESCALATED,
                now,
                IssueState.PENDING,
                IssueState.ACTIVE,
                {"reason": "m_reached", "m": m, "occurrences": issue.occurrences},
            )

    # ------------------------------------------------------------------ #
    # CLEAR handling
    # ------------------------------------------------------------------ #
    def _process_clear(
        self,
        fp: str,
        now: Timestamp,
        inhibition: InhibitionContext,
        transitions: list[Transition],
    ) -> None:
        issue = self.repo.get_open_issue_by_fingerprint(fp)
        if issue is None:
            return

        if inhibition.is_inhibited(issue.detector_key, None, issue.entity_id):
            return  # frozen: clear-streak advancement suppressed

        if issue.state is IssueState.PENDING:
            # Never confirmed -> discard entirely (Prometheus ``for:`` semantics).
            # Deleting the row (rather than resolving it) keeps a later refire
            # from reopening straight to active and skipping the M gate.
            self.repo.delete_issue(issue.id)
            return

        k = self.cfg.k_for(issue.detector_key)
        prev_state = issue.state
        issue.clear_streak += 1
        if issue.clear_streak >= k:
            self._resolve(issue, now, transitions)
        elif prev_state is IssueState.ACTIVE:
            # First clean check after firing: enters RESOLVING. This is the one
            # event in the streak worth a trail entry -- it is what explains the
            # Resolving pill (section 7/12: every state a card can show needs a
            # trail entry a human can read). Every subsequent clean check while
            # still RESOLVING advances clear_streak with no new event; the issue
            # detail combines this event's `k` with the issue's own live
            # clear_streak to show clearing progress without an event per tick.
            issue.state = IssueState.RESOLVING
            self.repo.update_issue(issue)
            self._emit(
                transitions,
                issue,
                EventKind.RESOLVING,
                now,
                IssueState.ACTIVE,
                IssueState.RESOLVING,
                {"clear_streak": issue.clear_streak, "k": k},
            )
        else:
            self.repo.update_issue(issue)  # still resolving, no new transition event

    def _resolve(self, issue: Issue, now: Timestamp, transitions: list[Transition]) -> None:
        prev_state = issue.state
        armed = self._fix_armed(issue, now)
        issue.state = IssueState.RESOLVED
        issue.resolved_ts = now
        self.repo.update_issue(issue)
        self._emit(
            transitions,
            issue,
            EventKind.RESOLVED,
            now,
            prev_state,
            IssueState.RESOLVED,
            {"clear_streak": issue.clear_streak},
        )
        if armed:
            issue.fix_state = FixState.VERIFIED
            self.repo.update_issue(issue)
            self._emit(
                transitions,
                issue,
                EventKind.FIX_VERIFIED,
                now,
                IssueState.RESOLVED,
                IssueState.RESOLVED,
                {},
            )
        # else: window expired -> resolved-but-unverified; fix_state stays APPLIED.

    # ------------------------------------------------------------------ #
    # Fix verification helpers
    # ------------------------------------------------------------------ #
    def _fix_armed(self, issue: Issue, now: Timestamp) -> bool:
        """True while a fix is applied and its verification window is still open."""
        if issue.fix_state is not FixState.APPLIED or issue.id is None:
            return False
        return self._within_fix_window(issue, now)

    def _within_fix_window(self, issue: Issue, now: Timestamp) -> bool:
        """True when a ``fix_applied`` event exists and its window has not lapsed,
        independent of the current ``fix_state`` (used to catch a refire that
        breaks an already-verified fix)."""
        if issue.id is None:
            return False
        applied_ts = self.repo.last_event_ts(issue.id, EventKind.FIX_APPLIED)
        if applied_ts is None:
            return False
        return (now - applied_ts) <= self.cfg.fix_window_s

    def _fix_window_expired(self, issue: Issue, now: Timestamp) -> bool:
        """True when a fix is still APPLIED but its verification window has lapsed
        (an issue that never cleared after the fix -> the fix failed)."""
        if issue.fix_state is not FixState.APPLIED or issue.id is None:
            return False
        applied_ts = self.repo.last_event_ts(issue.id, EventKind.FIX_APPLIED)
        if applied_ts is None:
            return False
        return (now - applied_ts) > self.cfg.fix_window_s

    def _mark_fix_failed(
        self, issue: Issue, now: Timestamp, transitions: list[Transition], *, reason: str
    ) -> None:
        issue.fix_state = FixState.FAILED
        self.repo.update_issue(issue)
        self._emit(
            transitions,
            issue,
            EventKind.FIX_FAILED,
            now,
            issue.state,
            issue.state,
            {"reason": reason},
        )

    def _set_fix_state(
        self,
        issue_id: int,
        fix_state: FixState,
        kind: str,
        now: Timestamp,
        detail: Optional[dict[str, Any]],
    ) -> Optional[Transition]:
        issue = self.repo.get_issue(issue_id)
        if issue is None:
            return None
        issue.fix_state = fix_state
        self.repo.update_issue(issue)
        return self._emit([], issue, kind, now, None, None, detail or {})

    # ------------------------------------------------------------------ #
    # Inhibition
    # ------------------------------------------------------------------ #
    def _build_inhibition(self, now: Timestamp, findings: list[Finding]) -> InhibitionContext:
        global_active = False
        global_cause_keys: set[str] = set()
        down_device_ids: set[int] = set()

        # (a) causes firing this cycle take effect immediately.
        for finding in findings:
            if finding.detector_key in self._global_cause_keys:
                global_active = True
                global_cause_keys.add(finding.detector_key)
            if (
                finding.detector_key in self._children_cause_keys
                and finding.entity.entity_id is not None
            ):
                down_device_ids.add(finding.entity.entity_id)

        # (b) causes already open and confirmed (active or resolving) stay in
        # effect until they themselves resolve.
        for issue in self.repo.list_open_issues():
            if issue.state not in (IssueState.ACTIVE, IssueState.RESOLVING):
                continue
            if issue.detector_key in self._global_cause_keys:
                global_active = True
                global_cause_keys.add(issue.detector_key)
            if issue.detector_key in self._children_cause_keys and issue.entity_id is not None:
                down_device_ids.add(issue.entity_id)

        return InhibitionContext(
            global_active=global_active,
            global_cause_keys=global_cause_keys,
            down_device_ids=down_device_ids,
            ancestry=self._ancestors_of,
        )

    def _ancestors_of(self, entity: Optional[Entity], entity_id: Optional[int]) -> set[int]:
        """Walk the ``parent_id`` chain, returning every ancestor entity_id.

        The entity itself is never included, so a downed device is not treated as
        its own descendant.
        """
        parent = self._first_parent(entity, entity_id)
        ancestors: set[int] = set()
        seen: set[int] = set()
        while parent is not None and parent not in seen:
            seen.add(parent)
            ancestors.add(parent)
            resolved = self.repo.get_entity(parent)
            parent = resolved.parent_id if resolved is not None else None
        return ancestors

    def _first_parent(self, entity: Optional[Entity], entity_id: Optional[int]) -> Optional[int]:
        if entity is not None:
            if entity.parent_id is not None:
                return entity.parent_id
            if entity.entity_id is not None:
                resolved = self.repo.get_entity(entity.entity_id)
                return resolved.parent_id if resolved is not None else None
            return None
        if entity_id is not None:
            resolved = self.repo.get_entity(entity_id)
            return resolved.parent_id if resolved is not None else None
        return None

    # ------------------------------------------------------------------ #
    # Event emission + callbacks
    # ------------------------------------------------------------------ #
    def _emit(
        self,
        transitions: list[Transition],
        issue: Issue,
        kind: str,
        now: Timestamp,
        from_state: Optional[IssueState],
        to_state: Optional[IssueState],
        detail: dict[str, Any],
    ) -> Transition:
        assert issue.id is not None, "cannot emit an event for an unsaved issue"
        self.repo.add_issue_event(
            IssueEvent(issue_id=issue.id, ts=now, kind=kind, detail=dict(detail))
        )
        transition = Transition(
            issue_id=issue.id,
            fingerprint=issue.fingerprint,
            detector_key=issue.detector_key,
            severity=issue.severity,
            title=issue.title,
            kind=kind,
            ts=now,
            from_state=from_state,
            to_state=to_state,
            detail=dict(detail),
        )
        transitions.append(transition)
        self._notify(transition)
        return transition

    def _notify(self, transition: Transition) -> None:
        """Fire callbacks. Fire-and-forget: a raising callback is logged and
        swallowed so it can never corrupt engine state or block later callbacks.
        """
        for callback in tuple(self._callbacks):
            try:
                callback(transition)
            except Exception:  # noqa: BLE001 - isolation is the whole point
                _log.warning(
                    "on_transition callback %r raised for issue %s (%s); ignored",
                    getattr(callback, "__name__", callback),
                    transition.issue_id,
                    transition.kind,
                    exc_info=True,
                )


__all__ = [
    "IssueEngine",
    "TransitionCallback",
    "fingerprint",
]
