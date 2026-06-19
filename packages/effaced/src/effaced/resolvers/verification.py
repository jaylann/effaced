"""The :class:`ResolverVerification` — outcome of one external read-back."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


def _now() -> datetime:
    """The current instant, timezone-aware — the default verification time."""
    return datetime.now(UTC)


class ResolverVerification(BaseModel):
    """Outcome of one independent post-erasure read-back (ADR 0027).

    Returned by :meth:`~effaced.VerifyingResolver.verify_absent` after the
    saga runner has erased a subject and marked the entry succeeded. It is
    an *independent* confirmation, separate from the resolver's own
    :class:`~effaced.ResolverErasure` report: the resolver re-queries the
    external system and states whether the subject's data is in fact gone.

    ``confirmed_absent=True`` is execution-fidelity evidence that the
    record is no longer present. ``confirmed_absent=False`` records a
    discrepancy — the erase reported success, but the read-back still finds
    the subject. A negative verification is audited loudly
    (``ERASURE_EXTERNAL_VERIFICATION_FAILED``); it does not by itself revert
    the erase or re-open the settled outbox entry (ADR 0027).

    This is never a determination that the external system holds no personal
    data: ``verify_absent`` re-queries the same surface the resolver reaches,
    so a confirmed absence proves the resolver's own erasure took effect, not
    that the provider is free of the subject everywhere.

    Attributes:
        resolver: Name of the resolver that performed the read-back.
        confirmed_absent: The read-back found the subject gone (``True``) or
            still present despite a successful erase (``False``).
        checked_at: When the read-back was performed (timezone-aware).
        detail: Short human-readable note for the audit trail (no PII).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    resolver: str = Field(min_length=1)
    confirmed_absent: bool
    checked_at: AwareDatetime = Field(default_factory=_now)
    detail: str | None = None
