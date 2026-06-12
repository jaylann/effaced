"""The :class:`RetentionReport` models — what one sweep found, per column."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RetentionReportEntry(BaseModel):
    """One annotated column's retention-expiry findings.

    The entry names the subjects whose declared retention window for this
    column has lapsed — and is honest about what it could not evaluate:
    rows without an anchor value are *counted*, never guessed. What a
    lapsed duty permits is the controller's determination; in particular,
    erasure retains ``RETAIN`` columns by construction, so acting on a
    lapsed ``RETAIN`` duty means changing the annotation first (flip the
    strategy or drop the policy) and then erasing.

    Attributes:
        table: The table holding the annotated column.
        column: The annotated column whose retention policy was evaluated.
        reason: The policy's declared legal duty, verbatim.
        anchor: The policy's anchor column, or ``None`` when the duration
            has no clock and the whole column is indeterminate.
        expired: Expired row count per subject id; empty when nothing has
            lapsed (or nothing could be evaluated).
        indeterminate_rows: Rows the sweep could not evaluate — every row
            when the policy has no anchor, otherwise the rows whose anchor
            column is NULL.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    table: str = Field(min_length=1)
    column: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    anchor: str | None = None
    expired: dict[str, int] = Field(default_factory=dict)
    indeterminate_rows: int = 0


class RetentionReport(BaseModel):
    """Everything one retention sweep found, evaluated at a single instant.

    The report is a mechanism's output, not a determination: it says which
    declared retention windows have lapsed, never whether the data may —
    or must — now be erased.

    Attributes:
        swept_at: The single cutoff instant the whole run was evaluated
            against (UTC).
        entries: One entry per column with a bounded retention duty
            (a policy carrying a ``duration``), in manifest order.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    swept_at: datetime
    entries: tuple[RetentionReportEntry, ...] = ()
