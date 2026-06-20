"""The :class:`OverdueErasure` model — one subject's stuck local erasure."""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field


class OverdueErasure(BaseModel):
    """One subject whose local erasure was requested but never completed.

    Sourced from the ``effaced_subject_erasures`` tombstone (ADR 0026): the
    subject's erasure was requested at ``requested_at`` and its ``erased_at``
    is still ``NULL`` — the local phase never committed, or a re-erasure
    re-opened the row and stalled. The age is measured against the report's
    single cutoff instant, so every entry in one report shares one clock.

    This is a detection signal, not a determination: an overdue erasure may be
    a crashed worker mid-erasure, a transaction still in flight, or a genuinely
    stuck subject blocking completion. Whether it needs intervention is the
    operator's call.

    Attributes:
        subject_ref: The canonical subject reference whose erasure is overdue
            — the same scalar the audit trail and outbox store, so a composite
            subject key reports as its canonical string (ADR 0025).
        requested_at: When the erasure was requested (UTC), per the tombstone.
        age: How long the erasure has been outstanding at the report's cutoff
            instant (``reported_at - requested_at``). Always positive for a
            reported subject, since only requests older than the cutoff appear.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_ref: str = Field(min_length=1)
    requested_at: datetime
    age: timedelta
