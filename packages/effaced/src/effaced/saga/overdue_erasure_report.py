"""The :class:`OverdueErasureReport` — every stuck local erasure at one instant."""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from effaced.saga.overdue_erasure import OverdueErasure


class OverdueErasureReport(BaseModel):
    """Every subject whose local erasure is overdue, evaluated at one instant.

    The report is the read-only counterpart of :meth:`effaced.Outbox.
    list_abandoned`: that surfaces external erasure calls that abandoned, this
    surfaces *local* erasures that were requested but never completed (the
    ``effaced_subject_erasures`` tombstone, ADR 0026). Together they answer
    "which erasures are stuck" from both halves of the saga.

    Like every effaced report it is a mechanism's output, not a determination:
    it names which erasures have been outstanding longer than the operator's
    chosen threshold, never whether any of them is a fault. A long-running
    transaction, a crashed worker mid-erasure, and a genuinely stuck subject
    all appear the same way here; distinguishing them is the operator's call.

    Attributes:
        reported_at: The single cutoff instant the whole report was evaluated
            against (UTC). Every entry's ``age`` is measured from it.
        older_than: The minimum outstanding age a request had to exceed to be
            reported — the threshold passed to
            :meth:`effaced.OverdueErasureReporter.report`.
        entries: The overdue subjects, most-overdue first (oldest
            ``requested_at``); empty when nothing is outstanding past the
            threshold.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reported_at: datetime
    older_than: timedelta = Field(ge=timedelta(0))
    entries: tuple[OverdueErasure, ...] = ()
