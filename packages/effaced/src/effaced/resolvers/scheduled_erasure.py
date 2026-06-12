"""The :class:`ResolverScheduledErasure` — outcome of one scheduled external erasure."""

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class ResolverScheduledErasure(BaseModel):
    """Outcome of one external erasure that can only be *scheduled* (ADR 0018).

    Returned by
    :meth:`~effaced.RetentionOnlyResolver.schedule_erasure` for systems
    with no per-subject delete: the data is guaranteed to expire by
    ``expires_at``, but nothing was deleted now. The saga runner parks the
    entry until the horizon and then re-verifies — it never records the
    schedule as a completed erasure.

    Convergence contract: scheduling a subject the system no longer holds
    — never held, already expired, or purged early — is **success**
    (``already_absent=True``), the scheduling analogue of erasure's
    ``already_absent``. Re-scheduling an already-scheduled subject is
    success reporting the same-or-later horizon.

    Exactly one of the two facts holds: either the data is already gone
    (``already_absent=True``, no horizon) or a horizon is named
    (``expires_at`` set). A schedule without a horizon is not an honest
    fact, and a horizon for data already gone is not a fact at all.

    Attributes:
        resolver: Name of the resolver that scheduled the erasure.
        expires_at: The retention horizon — the instant by which the
            subject's data is guaranteed to be gone (timezone-aware).
            Required unless ``already_absent=True``.
        already_absent: The subject's data is already gone — verified
            expiry, still success.
        detail: Short human-readable note for the audit trail (no PII).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    resolver: str = Field(min_length=1)
    expires_at: AwareDatetime | None = None
    already_absent: bool = False
    detail: str | None = None

    @model_validator(mode="after")
    def _horizon_xor_absent(self) -> ResolverScheduledErasure:
        """Require exactly one of a named horizon or verified absence."""
        if self.expires_at is None and not self.already_absent:
            msg = "expires_at is required unless already_absent=True"
            raise ValueError(msg)
        if self.expires_at is not None and self.already_absent:
            msg = "already_absent=True must not name a horizon"
            raise ValueError(msg)
        return self
