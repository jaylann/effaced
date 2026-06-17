"""Mapping of PostgREST table rows to :class:`~effaced.ExportRecord` rows.

The exported field set is the resolver's declared
:class:`~effaced_supabase.postgrest_table.PostgrestTable` columns, so it
is caller-authored rather than fixed — adding or recategorising a column
in a deployed configuration changes what that deployment exports. Each
record's ``source`` is the table name and its ``field`` is
``"{table}.{column}"`` so columns of the same name in different tables stay
distinct.

A cell that is absent or JSON ``null`` means "not held" and is dropped
rather than exported as an empty value. ``legal_basis``, ``purpose`` and
``retention_reason`` stay ``None`` on every record: a resolver cannot know
why the application holds the data — that metadata belongs to the
manifest-declared local data map.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from effaced import ExportRecord

if TYPE_CHECKING:
    from effaced_supabase.postgrest_table import PostgrestTable


def row_records(table: PostgrestTable, row: object) -> tuple[ExportRecord, ...]:
    """Map one PostgREST result row's declared columns to export records.

    Args:
        table: The table the row was selected from; its ``columns`` name
            the fields to export and the categories they hold.
        row: One decoded object from the ``GET /rest/v1/{table}`` JSON
            array; anything that is not a JSON object yields no records.

    Returns:
        One record per declared column the row populates; nothing for
        columns that are absent or JSON ``null``.
    """
    if not isinstance(row, Mapping):
        return ()
    return tuple(
        ExportRecord(
            source=table.name,
            field=f"{table.name}.{column.name}",
            category=column.category,
            value=value,
        )
        for column in table.columns
        if (value := row.get(column.name)) is not None
    )
