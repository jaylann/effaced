"""The :class:`ErasureResult` — what actually happened."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ErasureResult(BaseModel):
    """Outcome of the local phase of an erasure.

    External steps complete asynchronously; their outcomes land in the
    audit trail as the saga runner processes the outbox.

    Attributes:
        subject_id: The subject that was erased.
        completed_at: When the local transaction committed (UTC).
        deleted: Record counts deleted, by table.
        anonymized: Record counts anonymized, by table.
        retained: Record counts left in place under a retention duty, by table.
        enqueued_external: Resolver names whose erasure was enqueued.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_id: str = Field(min_length=1)
    completed_at: datetime
    deleted: dict[str, int] = Field(default_factory=dict)
    anonymized: dict[str, int] = Field(default_factory=dict)
    retained: dict[str, int] = Field(default_factory=dict)
    enqueued_external: tuple[str, ...] = ()
