"""The :class:`OutboxEntry` model."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from effaced.annotations import SubjectRef
from effaced.saga.outbox_status import OutboxStatus


class OutboxEntry(BaseModel):
    """One durable external call awaiting (or done with) execution.

    Entries are written in the *same transaction* as the local erasure, so
    an erasure is never half-recorded: either the local delete and all its
    external follow-ups are committed together, or none are.

    Attributes:
        entry_id: Unique id; doubles as the idempotency key for the call.
        subject_id: The erased subject's identifier — groups a subject's
            entries so the runner can tell when its last one lands.
        resolver: Which resolver will perform the call.
        ref: The subject reference to pass to it.
        status: Current lifecycle state.
        attempts: How many times the call has been claimed for execution.
        enqueued_at: When the entry was committed (UTC).
        last_attempt_at: When the last try started, if any (UTC).
        next_attempt_at: Earliest instant any runner may (re)claim the
            entry (UTC). ``None`` means due immediately. Doubles as the
            crash lease while ``IN_FLIGHT`` and as the backoff schedule
            while ``FAILED``; terminal entries carry ``None``.
        last_error: Short error note from the last failed try — the
            exception class name only, never its message (no PII).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: UUID
    subject_id: str = Field(min_length=1, max_length=255)
    resolver: str = Field(min_length=1, max_length=255)
    ref: SubjectRef
    status: OutboxStatus = OutboxStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    enqueued_at: datetime
    last_attempt_at: datetime | None = None
    next_attempt_at: datetime | None = None
    last_error: str | None = None
