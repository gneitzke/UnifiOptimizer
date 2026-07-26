"""Tech-visit mode (ARCHITECTURE.md section 3): an on-demand, one-shot analysis.

The daemon's startup path without the scheduler. :func:`run_visit` connects
read-only to a controller, backfills its retained history, runs baselines +
detectors + SLE over that window, and returns a :class:`VisitReport`.
:mod:`netadmin.visit.report` renders that report to HTML/JSON/console.
"""

from netadmin.visit.report import console_summary, render_html, render_json
from netadmin.visit.runner import STEP_ORDER, VisitReport, VisitStep, run_visit, run_visit_async

__all__ = [
    "STEP_ORDER",
    "VisitReport",
    "VisitStep",
    "run_visit",
    "run_visit_async",
    "console_summary",
    "render_html",
    "render_json",
]
