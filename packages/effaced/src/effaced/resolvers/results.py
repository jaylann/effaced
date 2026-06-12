"""Result models returned by resolver operations."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from effaced.export.bundle import ExportRecord


class ResolverExport(BaseModel):
    """What one external system holds on a subject.

    Attributes:
        resolver: Name of the resolver that produced this.
        records: The exported values with their metadata.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    resolver: str = Field(min_length=1)
    records: tuple[ExportRecord, ...] = ()


class ResolverErasure(BaseModel):
    """Outcome of one external erasure call.

    Idempotency contract: erasing a subject the external system no longer
    knows is **success** (``already_absent=True``), never an error — saga
    retries depend on this.

    Attributes:
        resolver: Name of the resolver that performed the erasure.
        already_absent: The subject was already gone — still success.
        detail: Short human-readable note for the audit trail (no PII).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    resolver: str = Field(min_length=1)
    already_absent: bool = False
    detail: str | None = None


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
