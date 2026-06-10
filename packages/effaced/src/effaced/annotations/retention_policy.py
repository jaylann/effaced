"""The :class:`RetentionPolicy` model."""

from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field

from effaced.categories import LegalBasis


class RetentionPolicy(BaseModel):
    """Why and how long a value must outlive an erasure request.

    Attributes:
        reason: Human-readable legal duty (e.g. ``"§147 AO invoice retention"``).
        basis: The lawful basis that overrides erasure.
        duration: How long the duty lasts, if bounded. ``None`` means
            indefinite / determined externally.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str = Field(min_length=1)
    basis: LegalBasis = LegalBasis.LEGAL_OBLIGATION
    duration: timedelta | None = None
