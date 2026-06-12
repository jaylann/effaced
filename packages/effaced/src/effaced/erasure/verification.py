"""The :class:`ErasureVerification` — what a post-erasure read-back found."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ErasureVerification(BaseModel):
    """The verdict of reading the annotated surface back after an erasure.

    A verification re-runs the manifest's subject-scoping as a read and
    counts what is left for the subject, per table. It proves **execution
    fidelity** — that a caller trigger, an FK cascade, an ORM event, or a
    partial commit did not resurrect rows the plan deleted — and nothing
    wider. Three boundaries are load-bearing and deliberately *not* covered:

    1. It re-reads the *same annotated surface* the plan was built from, so
       PII that was never annotated is invisible by construction; this is
       not a discovery-completeness check.
    2. A row orphaned off the subject's path (reachable by no hop chain to
       the subject) is unreachable by the scoping predicate, so it is
       invisible here too.
    3. Anonymized cell *values* are not verified — surrogates are random,
       never NULL, so without a before-state a reader cannot distinguish a
       surrogate from an original. Confirming a value was rewritten needs a
       before-state and is out of scope.

    The hard assertion is therefore narrower than "everything is gone":
    ``verified`` is true iff every row-deleted table holds zero
    subject-scoped rows. ``surviving`` is informational only — anonymize
    and retain tables keep rows by design — and never flips ``verified``.

    Attributes:
        subject_id: The subject whose surface was read back.
        verified_at: When the read-back ran (UTC).
        verified: ``True`` iff every row-deleted table is empty for this
            subject (``residual`` is all zero).
        residual: Per-table leftover subject-scoped row counts for tables
            the plan whole-row deletes; ``verified`` is the claim that all
            of these are zero.
        surviving: Per-table subject-scoped row counts for tables the plan
            anonymizes in place or retains — expected to be non-zero,
            reported for the record, never a failure.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_id: str = Field(min_length=1)
    verified_at: datetime
    verified: bool
    residual: dict[str, int] = Field(default_factory=dict)
    surviving: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _verified_matches_residual(self) -> ErasureVerification:
        """``verified`` is exactly "every row-deleted table is empty"."""
        if self.verified != all(count == 0 for count in self.residual.values()):
            msg = (
                "verified must equal whether every residual count is zero; "
                f"residual={self.residual!r} contradicts verified={self.verified!r}"
            )
            raise ValueError(msg)
        return self
