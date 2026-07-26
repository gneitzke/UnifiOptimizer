"""netadmin.issues: the issue-lifecycle engine, its models, and inhibition rules.

The engine (``docs/ARCHITECTURE.md`` section 7) is the stateful spine that turns
a stream of detector findings into tracked issues with a full audit trail. It is
pure logic over an :class:`~netadmin.issues.models.IssueRepository`; import
:class:`~netadmin.issues.engine.IssueEngine` and hand it findings plus an
injected ``now``.
"""

from netadmin.issues.engine import IssueEngine, TransitionCallback, fingerprint
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

__all__ = [
    "IssueEngine",
    "TransitionCallback",
    "fingerprint",
    "EngineConfig",
    "EventKind",
    "Issue",
    "IssueEvent",
    "IssueRepository",
    "Transition",
    "InhibitionScope",
    "InhibitionRule",
    "InhibitionContext",
    "DEFAULT_RULES",
]
