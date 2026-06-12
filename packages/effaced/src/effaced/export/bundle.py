"""The :class:`ExportBundle` model — one Art. 15 response payload."""

from __future__ import annotations

from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from effaced.categories import LegalBasis, PiiCategory
from effaced.manifest.migration import MANIFEST_SCHEMA_VERSION


class ExportRecord(BaseModel):
    """One exported value with its Art. 15 metadata.

    Attributes:
        source: Where the value came from — a table name or resolver name.
        field: The field within the source.
        category: PII category of the value.
        value: The value itself, JSON-encoded.
        legal_basis: Why the data is held, if declared.
        purpose: Processing purpose, if declared.
        retention_reason: The legal duty keeping the value, if any.
        expires_at: The instant by which the value is guaranteed to expire
            at its source, when on-demand erasure there is unavailable
            (retention-only resolvers, ADR 0018).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(min_length=1)
    field: str = Field(min_length=1)
    category: PiiCategory
    value: object = None
    legal_basis: LegalBasis | None = None
    purpose: str | None = None
    retention_reason: str | None = None
    expires_at: AwareDatetime | None = None


class ExportBundle(BaseModel):
    """Everything held on one subject, with the required Art. 15 metadata.

    Attributes:
        subject_id: The identifier the export was requested for.
        generated_at: When the bundle was assembled (UTC).
        schema_version: Manifest schema version the bundle was built under.
        records: Every exported value, grouped by consumers as they wish.
        incomplete_sources: Sources that failed during collection — surfaced
            loudly rather than silently omitted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_id: str = Field(min_length=1)
    generated_at: datetime
    schema_version: int = MANIFEST_SCHEMA_VERSION
    records: tuple[ExportRecord, ...] = ()
    incomplete_sources: tuple[str, ...] = ()
