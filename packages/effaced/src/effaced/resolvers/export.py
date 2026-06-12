"""The :class:`ResolverExport` — what one external system holds on a subject."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from effaced.export.bundle import ExportRecord


class ResolverExport(BaseModel):
    """What one external system holds on a subject.

    Attributes:
        resolver: Name of the resolver that produced this.
        records: The exported values with their metadata.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    resolver: str = Field(min_length=1)
    records: tuple[ExportRecord, ...] = ()
