"""The :class:`PostgrestTable` — one declared PII-bearing PostgREST table."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from effaced_supabase.postgrest_column import PostgrestColumn


class PostgrestTable(BaseModel):
    """One PostgREST-exposed table holding a subject's PII.

    A table declares the column that carries the subject id and the
    PII-bearing columns to export and erase. The resolver filters on
    ``subject_column = <ref value>`` and never discovers the schema, so
    the declaration is the auditable record of which rows and columns the
    resolver reaches.

    Attributes:
        name: The table (or view) name PostgREST exposes at
            ``/rest/v1/{name}``.
        subject_column: The column whose value equals the subject id —
            the resolver matches ``subject_column=eq.<ref value>``.
        columns: The PII-bearing columns to export; at least one. The
            ``subject_column`` need not appear here unless it is itself
            PII to export.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    subject_column: str = Field(min_length=1)
    columns: tuple[PostgrestColumn, ...] = Field(min_length=1)
