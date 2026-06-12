"""The :class:`OutboxEntry` model."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from effaced.annotations import Correction, SubjectRef
from effaced.saga.outbox_operation import OutboxOperation
from effaced.saga.outbox_status import OutboxStatus


class OutboxEntry(BaseModel):
    """One durable external call awaiting (or done with) execution.

    Entries are written in the *same transaction* as the local phase, so
    an operation is never half-recorded: either the local changes and all
    their external follow-ups are committed together, or none are.

    Attributes:
        entry_id: Unique id; doubles as the idempotency key for the call.
        subject_id: The subject's identifier, exactly as passed to
            ``erase_subject``/``rectify_subject`` — groups a subject's
            entries so the runner can tell when its last one of an
            operation lands (completion is per subject *and* operation).
            Must be globally unique per data subject: two subjects sharing
            a value would be treated as one completion group.
        resolver: Which resolver will perform the call.
        ref: The subject reference to pass to it.
        operation: Which external call the entry performs (erase by
            default; rectify entries also carry ``corrections``).
        corrections: The corrected values for a rectify entry — real PII,
            stored in the row's payload only while the entry is
            non-terminal (cleared on success and abandonment alike) and
            never mirrored into any audit event.
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
    operation: OutboxOperation = OutboxOperation.ERASE
    corrections: tuple[Correction, ...] = ()
    status: OutboxStatus = OutboxStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    enqueued_at: datetime
    last_attempt_at: datetime | None = None
    next_attempt_at: datetime | None = None
    last_error: str | None = None
