"""The :class:`RestrictionRequest` body of the restriction endpoint."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RestrictionRequest(BaseModel):
    """What the caller decides about a restriction of processing (Art. 18).

    The server fills in the subject id (from the authenticated
    :class:`~effaced_fastapi.Subject`), the timestamp, and the ``"api"``
    source. Recording is flag-keeping, never enforcement — nothing in
    effaced consults the flag before processing (ADR 0014).

    Attributes:
        restricted: ``True`` places a restriction, ``False`` lifts one.
        purpose: The processing purpose restricted; ``None`` means all
            processing for the subject.
        reason: Free-text grounds (e.g. the Art. 18(1) basis claimed).
            Kept in the ledger's history, never mirrored into audit
            payloads.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    restricted: bool
    purpose: str | None = Field(default=None, min_length=1, max_length=255)
    reason: str | None = Field(default=None, max_length=255)
