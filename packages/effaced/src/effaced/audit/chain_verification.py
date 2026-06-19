"""The :class:`ChainVerification` verdict model (ADR 0028)."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator


class ChainVerification(BaseModel):
    """The verdict of recomputing an audit trail's tamper-evidence chain.

    Produced by :class:`~effaced.AuditChainVerifier`. It reports whether the
    recomputed hash chain matched what was stored — i.e. whether any chained
    row was modified out of band since it was written (ADR 0028). It is
    *detection*: ``verified=True`` means no break was found in the chained
    rows read, never a determination that the trail — or the deployment — is
    secure or compliant.

    Legacy rows and custom-sink rows with no stored hash form an *unchained
    prefix*: they are skipped, not failed. A trail that is entirely unchained
    therefore verifies vacuously (``verified=True``,
    ``first_broken_event_id=None``) — absence of a hash is absence of
    evidence, not evidence of tampering.

    Attributes:
        verified: ``True`` if every chained row recomputed to its stored
            hash; ``False`` if any chained row's hash did not match.
        first_broken_event_id: The ``event_id`` of the earliest row whose
            recomputed hash differed from its stored hash — where the chain
            first breaks. ``None`` exactly when ``verified`` is ``True``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    verified: bool
    first_broken_event_id: UUID | None = None

    @model_validator(mode="after")
    def _verdict_and_break_agree(self) -> ChainVerification:
        """A break id is present iff the chain failed verification."""
        if self.verified and self.first_broken_event_id is not None:
            msg = "a verified chain cannot name a first broken event"
            raise ValueError(msg)
        if not self.verified and self.first_broken_event_id is None:
            msg = "an unverified chain must name its first broken event"
            raise ValueError(msg)
        return self
