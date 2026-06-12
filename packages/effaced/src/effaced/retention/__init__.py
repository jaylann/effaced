"""Art. 5(1)(e) storage limitation — a report-only retention-expiry sweep.

:class:`RetentionSweeper` evaluates every bounded retention duty in the
manifest (a :class:`~effaced.annotations.RetentionPolicy` with a
``duration``, clocked by its ``anchor`` column) against one instant and
reports lapsed windows per subject, audited. It deletes nothing, and it
determines nothing: whether a lapsed duty permits — or requires — erasure
is the controller's determination, never effaced's.
"""

from effaced.retention.report import RetentionReport, RetentionReportEntry
from effaced.retention.sweeper import RetentionSweeper

__all__ = ["RetentionReport", "RetentionReportEntry", "RetentionSweeper"]
