"""Report export: the backend assembler for the in-app network assessment report.

:func:`netadmin.report.assembler.build_report` is the single source of truth: it
builds the whole report model from real repository queries (``docs/REPORT_SPEC.md``
+ ``docs/ARCHITECTURE.md`` 19). ``GET /api/report`` serialises the model; the UI
renders it and computes nothing.
"""

from __future__ import annotations

from netadmin.report.assembler import build_report
from netadmin.report.models import ReportModel, report_to_dict

__all__ = ["build_report", "ReportModel", "report_to_dict"]
