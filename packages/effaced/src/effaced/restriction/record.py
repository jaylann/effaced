"""The :class:`RestrictionRecord` — one timestamped restriction event."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RestrictionRecord(BaseModel):
    """One restriction placement or lift (Art. 18), as it happened.

    Records are immutable events, not mutable state: whether a subject is
    currently restricted is *derived* by reading the latest record per
    scope, never stored. There is no transition validation — lifting a
    restriction that was never placed simply appends. Events are evidence,
    not a state machine.

    A record with ``purpose=None`` is global: it restricts (or lifts the
    restriction on) *all* processing for the subject. A purpose-scoped
    record touches only that purpose.

    Attributes:
        subject_id: Whose restriction this is.
        purpose: The processing purpose restricted (e.g. ``"ads"``);
            ``None`` means all processing.
        restricted: ``True`` places a restriction, ``False`` lifts one.
        reason: Free-text grounds (e.g. the Art. 18(1) basis claimed).
            Kept in history, never mirrored into audit payloads.
        recorded_at: When the event happened (UTC).
        source: Where the event came from (``"dsar_portal"``, ``"api"``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_id: str = Field(min_length=1, max_length=255)
    purpose: str | None = Field(default=None, min_length=1, max_length=255)
    restricted: bool
    reason: str | None = Field(default=None, max_length=255)
    recorded_at: datetime
    source: str | None = Field(default=None, max_length=255)
