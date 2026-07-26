"""netadmin.store: SQLite (WAL) data layer, repository, and numbered migrations.

Public surface:

- :class:`~netadmin.store.repository.Repository` -- the only SQL-facing module.
- :func:`~netadmin.store.db.connect`, :func:`~netadmin.store.db.begin_immediate`,
  :func:`~netadmin.store.db.apply_migrations` -- low-level connection + migration.
- :class:`~netadmin.store.metrics.MetricKind`, :func:`~netadmin.store.metrics.metric_kind`
  -- the gauge/counter registry the repository consults.
"""

from netadmin.store.db import (
    MIGRATIONS_DIR,
    apply_migrations,
    begin_immediate,
    connect,
    schema_version,
)
from netadmin.store.metrics import MetricKind, is_counter, metric_kind
from netadmin.store.repository import (
    DAY_SECONDS,
    HOUR_SECONDS,
    Repository,
    SampleReading,
    WindowResult,
)

__all__ = [
    "Repository",
    "SampleReading",
    "WindowResult",
    "connect",
    "begin_immediate",
    "apply_migrations",
    "schema_version",
    "MIGRATIONS_DIR",
    "MetricKind",
    "metric_kind",
    "is_counter",
    "HOUR_SECONDS",
    "DAY_SECONDS",
]
