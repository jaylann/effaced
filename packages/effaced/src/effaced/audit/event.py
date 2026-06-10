"""The :class:`AuditEvent` model."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from effaced.audit.event_type import AuditEventType


class AuditEvent(BaseModel):
    """One immutable entry in the audit trail.

    Events carry *references and metadata*, never rich PII — an audit trail
    that itself hoards personal data would defeat its purpose. ``payload``
    values are intentionally restricted to short, loggable scalars.

    Attributes:
        event_id: Unique id, assigned at creation, never reused.
        event_type: What happened.
        subject_ref: Opaque subject reference (NOT an email or name).
        occurred_at: When it happened (UTC).
        payload: Small structured details (counts, table names, versions).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID
    event_type: AuditEventType
    subject_ref: str = Field(min_length=1, max_length=255)
    occurred_at: datetime
    payload: dict[str, str | int | bool] = Field(default_factory=dict)
