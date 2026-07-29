"""Alert decision logic (``docs/ARCHITECTURE.md`` 20). Pure: no I/O, injected clock.

Four independent decisions live here, in the order a transition meets them:

1. **Classification** (:func:`classify`) -- which of the engine's twelve
   ``EventKind`` values is worth telling a human about, and as what.
2. **Filtering** -- the channel's ``min_severity`` floor and ``events`` allow-list.
3. **Dedupe** -- the invariant that kills every re-fire path in one rule: a channel
   announces a fingerprint as open only when it is not already announced, and
   announces a resolve only for a fingerprint it announced.
4. **Flood control** -- a token bucket per channel; overflow coalesces into a
   digest instead of being dropped, so nothing is ever silently lost.

Every piece takes its clock as a parameter, so the tests are deterministic and the
50-event-storm case is exercised without sleeping.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Callable, Optional

from netadmin.config import AlertChannelConfig
from netadmin.domain.types import IssueState
from netadmin.integrations.alerts.models import (
    OPEN_EVENTS,
    OPENED,
    REOPENED,
    RESOLVED,
    AlertEvent,
    DigestSummary,
    meets_min_severity,
    severity_rank,
)
from netadmin.issues.models import EventKind, Transition

__all__ = [
    "SKIP",
    "SEND",
    "DIGEST",
    "classify",
    "TokenBucket",
    "ChannelPolicy",
]

# --- decisions ------------------------------------------------------------- #

SKIP = "skip"  # not notifiable on this channel
SEND = "send"  # deliver now as its own message
DIGEST = "digest"  # channel is flooding; fold into the next summary

# How many fingerprints a channel remembers as "currently announced". Bounded so a
# pathological detector cannot grow the map without limit; the oldest entry is
# evicted first. Losing an entry costs at most one duplicate notification.
_DEDUPE_MAX = 4096

# Titles sampled into a digest message ("top: ...") and the buffer cap behind it.
_DIGEST_TOP_TITLES = 3
_DIGEST_TITLE_CANDIDATES = 64

# The engine detail that distinguishes the *confirmation* escalation (pending ->
# active after M consecutive fires) from a flap snap-back (resolving -> active).
_REASON_M_REACHED = "m_reached"


def classify(transition: Transition) -> Optional[str]:
    """Map a lifecycle transition onto an alert class, or ``None`` for "never notify".

    Only three shapes are notifiable:

    * ``escalated`` into ACTIVE with ``reason == "m_reached"`` -- the pending ->
      active confirmation. This, not ``detected``, is the real "a problem started":
      a ``detected`` issue is unconfirmed noise that may vanish before reaching M.
    * ``reopened`` -- a resolved issue firing again inside the reopen window.
    * ``resolved`` -- it cleared.

    Everything else returns ``None``. In particular an ``escalated`` with
    ``reason == "refire_during_resolving"`` is a flap snap-back: the open was
    already announced and never resolved-announced, so re-announcing it would be a
    duplicate. Ack / snooze / investigate / all ``fix_*`` rows are bookkeeping.

    **Suppression gate (Gitea #49).** An OPENED/REOPENED on a *suppressed* issue
    returns ``None``: the operator has parked its claim on attention, so no
    channel announces it. RESOLVED deliberately passes through even when
    suppressed — the dedupe invariant drops a resolve the channel never
    announced, so a suppressed issue that was never announced open cannot leak a
    resolve, while one announced *before* it was suppressed still gets its close.
    The flag rides on the transition (populated in the engine's ``_emit``), so
    this stays pure — no store handle, no clock.
    """
    kind = transition.kind
    if kind == EventKind.REOPENED:
        return None if transition.suppressed else REOPENED
    if kind == EventKind.RESOLVED:
        return RESOLVED
    if kind == EventKind.ESCALATED:
        if (
            transition.to_state is IssueState.ACTIVE
            and (transition.detail or {}).get("reason") == _REASON_M_REACHED
        ):
            return None if transition.suppressed else OPENED
    return None


# --- flood control --------------------------------------------------------- #


class TokenBucket:
    """A per-channel rate limiter: ``rate_per_min`` burst, refilled at that rate.

    Monotonic by contract -- the caller injects ``clock`` (``time.monotonic`` in
    production, a fake in tests) so a wall-clock step never grants or withholds
    tokens.
    """

    def __init__(self, rate_per_min: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._capacity = float(max(1, int(rate_per_min)))
        self._per_second = self._capacity / 60.0
        self._tokens = self._capacity
        self._clock = clock
        self._last = clock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        self._last = now
        if elapsed:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._per_second)

    @property
    def tokens(self) -> float:
        """Currently available tokens (refilled to now)."""
        self._refill()
        return self._tokens

    def try_take(self) -> bool:
        """Spend one token if available."""
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    def delay_until_token(self) -> float:
        """Seconds until one token is available (0.0 when one already is)."""
        self._refill()
        if self._tokens >= 1.0:
            return 0.0
        return (1.0 - self._tokens) / self._per_second


class _DigestBuffer:
    """Exact counts plus a bounded title sample for one pending digest."""

    def __init__(self) -> None:
        self.count = 0
        self.by_event: Counter[str] = Counter()
        self.by_severity: Counter[str] = Counter()
        self.first_ts: Optional[int] = None
        self.last_ts: Optional[int] = None
        # (severity rank, arrival order, title) -- sorted at render time so the
        # sample leads with the worst thing in the batch.
        self._titles: list[tuple[int, int, str]] = []

    def __len__(self) -> int:
        return self.count

    def add(self, event: AlertEvent) -> None:
        self.count += 1
        self.by_event[event.event] += 1
        self.by_severity[event.severity] += 1
        ts = event.ts
        self.first_ts = ts if self.first_ts is None else min(self.first_ts, ts)
        self.last_ts = ts if self.last_ts is None else max(self.last_ts, ts)
        if len(self._titles) < _DIGEST_TITLE_CANDIDATES:
            self._titles.append((severity_rank(event.severity), self.count, event.title))

    def top_titles(self) -> list[str]:
        ordered = sorted(self._titles)
        out: list[str] = []
        for _rank, _seq, title in ordered:
            if title not in out:
                out.append(title)
            if len(out) >= _DIGEST_TOP_TITLES:
                break
        return out


# --- the per-channel policy ------------------------------------------------ #


class ChannelPolicy:
    """Filter + dedupe + flood control for one channel. Holds all its own state."""

    def __init__(
        self,
        cfg: AlertChannelConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
        dedupe_max: int = _DEDUPE_MAX,
    ) -> None:
        self.cfg = cfg
        self._events = frozenset(cfg.events)
        self._min_severity = cfg.min_severity
        self._bucket = TokenBucket(cfg.rate_limit_per_min, clock=clock)
        self._dedupe_max = max(1, int(dedupe_max))
        # fingerprint -> the open class last announced on THIS channel. Per channel,
        # not global: a P3 that only cleared a p3-floor channel must not license a
        # resolve on the p2-floor channel that never heard the open.
        self._announced: dict[str, str] = {}
        self._digest = _DigestBuffer()

    # -- decision ------------------------------------------------------ #
    def evaluate(self, event: AlertEvent) -> str:
        """Decide :data:`SKIP` / :data:`SEND` / :data:`DIGEST` for one event.

        Dedupe state is committed here, on the accept path only: an event filtered
        out by severity or the event allow-list never marks the fingerprint, so a
        later resolve for it is correctly suppressed too.
        """
        if event.event not in self._events:
            return SKIP
        if not meets_min_severity(event.severity, self._min_severity):
            return SKIP
        if not self._dedupe_admit(event):
            return SKIP
        return SEND if self._bucket.try_take() else DIGEST

    def _dedupe_admit(self, event: AlertEvent) -> bool:
        """Apply the announce-once invariant, recording the new state on accept."""
        fingerprint = event.fingerprint
        if event.event in OPEN_EVENTS:
            if fingerprint in self._announced:
                return False  # already announced open and never resolved-announced
            self._announced[fingerprint] = event.event
            while len(self._announced) > self._dedupe_max:
                self._announced.pop(next(iter(self._announced)))
            return True
        # RESOLVED: only meaningful if this channel announced the open.
        if fingerprint not in self._announced:
            return False
        del self._announced[fingerprint]
        return True

    @property
    def announced(self) -> int:
        """How many fingerprints this channel currently considers announced."""
        return len(self._announced)

    # -- digest -------------------------------------------------------- #
    def buffer(self, event: AlertEvent) -> None:
        """Fold a rate-limited event into the pending digest."""
        self._digest.add(event)

    @property
    def pending_digest(self) -> int:
        """Events waiting to be summarised."""
        return len(self._digest)

    def next_flush_delay(self) -> Optional[float]:
        """Seconds until the pending digest can be sent, or ``None`` if none pends."""
        if not self._digest.count:
            return None
        return self._bucket.delay_until_token()

    def take_digest(self, *, force: bool = False) -> Optional[DigestSummary]:
        """Drain the buffer into one summary, spending a token.

        Returns ``None`` when nothing pends, or when no token is available and
        ``force`` is not set. ``force`` is used on shutdown so a pending digest is
        flushed rather than discarded.
        """
        if not self._digest.count:
            return None
        if not self._bucket.try_take() and not force:
            return None
        buf = self._digest
        self._digest = _DigestBuffer()
        return DigestSummary(
            channel=self.cfg.name,
            count=buf.count,
            by_event=dict(buf.by_event),
            by_severity=dict(buf.by_severity),
            top_titles=buf.top_titles(),
            first_ts=int(buf.first_ts or 0),
            last_ts=int(buf.last_ts or 0),
        )
