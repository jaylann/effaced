"""The :class:`ResolverRectification` — outcome of one external rectification call."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ResolverRectification(BaseModel):
    """Outcome of one external rectification call.

    Idempotency contract — convergence: re-applying a correction the
    external system already reflects is **success**
    (``already_consistent=True``), never an error. It is the rectification
    analogue of erasure's ``already_absent``; saga retries depend on it.

    Attributes:
        resolver: Name of the resolver that performed the rectification.
        already_consistent: The system already held the corrected values —
            still success.
        detail: Short human-readable note for the audit trail (no PII).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    resolver: str = Field(min_length=1)
    already_consistent: bool = False
    detail: str | None = None
