"""The :class:`VerifiedErasure` — an erasure outcome paired with its read-back."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from effaced.resolvers import ResolverErasure, ResolverVerification


class VerifiedErasure(BaseModel):
    """A successful external erasure paired with its post-erasure verification.

    Internal saga carrier (ADR 0027): the runner produces this only when an
    erase entry's resolver implements
    :class:`~effaced.VerifyingResolver` and the erase succeeded. The async
    execution phase performs both the erase and the read-back; the sync
    settlement phase audits the erasure success first, then the verification
    verdict — keeping the awaits in the gather, not in the bookkeeping.

    Attributes:
        erasure: The resolver's own erasure outcome.
        verification: The independent read-back verdict for the same subject.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    erasure: ResolverErasure
    verification: ResolverVerification
