"""The :class:`~effaced.CoveredSurface` the Supabase PostgREST resolver declares.

The covered fields are built from the resolver's declared
:class:`~effaced_supabase.postgrest_table.PostgrestTable` columns, the same
list the exporter walks, so the declaration and the export cannot drift.
Unlike the auth resolver's fixed surface, this one is caller-shaped: it
covers exactly the tables and columns the application declared and nothing
else.

This is a declaration of *claimed* reach, never a compliance
determination — it cannot attest to PII the application never told the
resolver about.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from effaced import CoveredField, CoveredSurface

if TYPE_CHECKING:
    from effaced_supabase.postgrest_table import PostgrestTable

_NOTE = (
    "The surface covers exactly the tables and columns declared at "
    "construction; the resolver performs no schema discovery, so PII in "
    "an undeclared table or column is outside its claimed reach and "
    "belongs to the application's own data map."
)


def covered_surface_for(tables: Sequence[PostgrestTable]) -> CoveredSurface:
    """Build the covered surface from the resolver's declared tables.

    Args:
        tables: The resolver's declared tables; every column of every
            table becomes one covered field, keyed ``"{table}.{column}"``
            to match the exporter's :class:`~effaced.ExportRecord` fields.

    Returns:
        The :class:`~effaced.CoveredSurface` named for the PostgREST
        resolver, with one field per declared column and a note that the
        surface is exactly the caller's declaration.
    """
    fields = tuple(
        CoveredField(field=f"{table.name}.{column.name}", category=column.category)
        for table in tables
        for column in table.columns
    )
    return CoveredSurface(resolver="supabase_postgrest", fields=fields, notes=(_NOTE,))
