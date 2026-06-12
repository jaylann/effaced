"""The :class:`RectificationResult` — what actually happened."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RectificationResult(BaseModel):
    """Outcome of the local phase of a rectification.

    External steps complete asynchronously; their outcomes land in the
    audit trail as the saga runner processes the outbox.

    Attributes:
        subject_id: The subject whose data was rectified.
        completed_at: When the local phase finished (UTC); durable once
            the caller commits.
        rectified: Rows updated, by table. A table matched by several
            corrections counts each step's rows — the same row can be
            counted once per category that touched it.
        enqueued_external: Resolver names whose rectification was enqueued.
        skipped_resolvers: Registered resolvers that received nothing —
            no matching ref, or no ``rectify_subject`` capability.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_id: str = Field(min_length=1)
    completed_at: datetime
    rectified: dict[str, int] = Field(default_factory=dict)
    enqueued_external: tuple[str, ...] = ()
    skipped_resolvers: tuple[str, ...] = ()
