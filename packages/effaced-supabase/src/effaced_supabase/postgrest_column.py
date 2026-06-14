"""The :class:`PostgrestColumn` — one declared PII-bearing table column."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from effaced import PiiCategory


class PostgrestColumn(BaseModel):
    """One PII-bearing column the PostgREST resolver exports and erases.

    A column declares the name PostgREST exposes it under and the
    :class:`~effaced.PiiCategory` the value holds. Declaration is explicit
    and auditable on purpose: the resolver never discovers a table's
    schema, so a column not named here is neither exported nor counted in
    the resolver's covered surface.

    Attributes:
        name: The column name as PostgREST exposes it (a ``select`` key).
        category: The :class:`~effaced.PiiCategory` the column's value is
            declared to hold; carried onto every exported record and into
            the resolver's covered surface.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    category: PiiCategory
