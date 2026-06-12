"""The :class:`AbandonedSignal` model — what an abandonment hook receives."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from effaced.saga.outbox_operation import OutboxOperation


class AbandonedSignal(BaseModel):
    """One entry's terminal abandonment, summarised for an alerting host.

    Handed to an :class:`~effaced.AbandonedHook` after the entry has flipped
    to ``ABANDONED``. Carries exactly what a host needs to page or emit a
    metric and **no PII**: the error is the exception *class name* only,
    never its message (provider errors embed identifiers), and corrections
    never appear here.

    Attributes:
        entry_id: The abandoned entry's id — the idempotency key, and what
            ``Outbox.requeue`` takes to re-drive an erase entry.
        subject_id: The data subject whose request is now unfinished.
        resolver: Which resolver's call exhausted its retries.
        operation: Whether this was an erase or a rectify entry — a rectify
            abandonment cannot be requeued (its corrections are gone).
        attempts: How many times the call was claimed before abandonment.
        error: The terminating exception's class name (no message, no PII).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: UUID
    subject_id: str = Field(min_length=1, max_length=255)
    resolver: str = Field(min_length=1, max_length=255)
    operation: OutboxOperation
    attempts: int = Field(ge=0)
    error: str = Field(min_length=1, max_length=255)
