"""The :class:`RetentionPolicy` model."""

from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field

from effaced.categories import LegalBasis


class RetentionPolicy(BaseModel):
    """Why and how long a value must outlive an erasure request.

    A bounded duty needs a clock: ``duration`` is measured from the instant
    stored in the ``anchor`` column. Without an anchor, a duration cannot be
    evaluated — the retention sweep reports such columns as indeterminate,
    never guessed (see :class:`effaced.retention.RetentionSweeper`).

    Attributes:
        reason: Human-readable legal duty (e.g. ``"§147 AO invoice retention"``).
        basis: The lawful basis that overrides erasure.
        duration: How long the duty lasts, if bounded. ``None`` means
            indefinite / determined externally.
        anchor: Name of a datetime column **on the same table** as the
            annotated column, holding the instant the retention clock starts
            (an ``invoiced_at``, a ``closed_at``). Cross-table anchors are
            out of scope. The SQLAlchemy adapter validates existence and
            datetime-ness at collection time (ADR 0012).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str = Field(min_length=1)
    basis: LegalBasis = LegalBasis.LEGAL_OBLIGATION
    duration: timedelta | None = None
    anchor: str | None = None
