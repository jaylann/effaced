"""The :class:`ConsentRecord` — one versioned, timestamped consent event."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ConsentRecord(BaseModel):
    """One consent grant or withdrawal, as it happened.

    Records are immutable events, not mutable state: current consent status
    is derived by reading the latest record per (subject, purpose). This is
    what makes Art. 5(2) accountability answerable — *what consent was
    given, when, and against which policy version*.

    Attributes:
        subject_id: Whose consent this is.
        purpose: The processing purpose consented to (e.g. ``"newsletter"``).
        policy_version: Version of the policy text the subject saw.
        granted: ``True`` for a grant, ``False`` for a withdrawal.
        recorded_at: When the event happened (UTC).
        source: Where the event came from (``"signup_form"``, ``"api"``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_id: str = Field(min_length=1, max_length=255)
    purpose: str = Field(min_length=1, max_length=255)
    policy_version: str = Field(min_length=1, max_length=255)
    granted: bool
    recorded_at: datetime
    source: str | None = Field(default=None, max_length=255)
