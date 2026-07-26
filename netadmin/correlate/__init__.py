"""netadmin.correlate: the correlation / incident layer (section 17).

The "seasoned expert" layer on top of the issue engine. Detectors + the issue
engine find, track, and confirm every real fault; this package connects the dots
-- grouping open issues into **incidents** with one root cause and its symptoms,
deterministically and conservatively (rules only, a recorded rationale per link,
anything unattributable left as a standalone incident-of-one).

Import :class:`~netadmin.correlate.engine.CorrelationEngine`, hand it a
:class:`~netadmin.correlate.models.CorrelationStore` (the in-memory fake in tests,
:class:`~netadmin.correlate.store_repository.StoreCorrelationRepository` in
production) plus an injected ``now``.
"""

from netadmin.correlate.engine import CorrelationEngine, incident_fingerprint
from netadmin.correlate.models import (
    CorrelationConfig,
    CorrelationStore,
    Incident,
    IncidentMember,
    IncidentRole,
    IncidentState,
    max_severity,
)
from netadmin.correlate.rules import (
    ANY,
    ROOT_PRIORITY_ORDER,
    RULES,
    CausalRule,
    Direction,
    MatchedLink,
    TopoRelation,
    match_link,
    root_rank,
)
from netadmin.correlate.topology import TopologyIndex, TopoNode

__all__ = [
    "CorrelationEngine",
    "incident_fingerprint",
    "CorrelationConfig",
    "CorrelationStore",
    "Incident",
    "IncidentMember",
    "IncidentRole",
    "IncidentState",
    "max_severity",
    "TopoNode",
    "TopologyIndex",
    "CausalRule",
    "Direction",
    "TopoRelation",
    "MatchedLink",
    "RULES",
    "ANY",
    "ROOT_PRIORITY_ORDER",
    "match_link",
    "root_rank",
]
