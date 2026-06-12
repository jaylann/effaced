"""Build a :class:`~effaced.manifest.DataMap` from SQLAlchemy metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import DateTime

from effaced.adapters.sqlalchemy.info import INFO_KEY
from effaced.annotations import PiiSpec, SubjectLink
from effaced.exceptions import ManifestError
from effaced.manifest import ColumnEntry, DataMap, TableEntry

if TYPE_CHECKING:
    from sqlalchemy import MetaData, Table


def collect_data_map(metadata: MetaData) -> DataMap:
    """Collect every effaced annotation from SQLAlchemy metadata.

    Args:
        metadata: The ``MetaData`` holding your mapped tables (for the ORM,
            ``Base.metadata``).

    Returns:
        A :class:`DataMap` containing only tables with at least one
        annotation (a ``pii`` column or a ``subject_link``).

    Raises:
        ManifestError: If an ``info`` entry under the effaced key is not a
            recognised annotation object, or a retention policy names an
            anchor column that does not exist on the table or is not
            datetime-typed (ADR 0012 — fail loudly at assembly, before any
            sweep runs).
    """
    entries = [
        entry for table in metadata.sorted_tables if (entry := _collect_table(table)) is not None
    ]
    return DataMap(tables=tuple(entries))


def _collect_table(table: Table) -> TableEntry | None:
    """Build one table's entry, or ``None`` if it carries no annotations."""
    link = table.info.get(INFO_KEY)
    if link is not None and not isinstance(link, SubjectLink):
        msg = f"table {table.name!r}: info[{INFO_KEY!r}] is not a SubjectLink"
        raise ManifestError(msg)
    columns = tuple(
        ColumnEntry(name=column.name, spec=spec)
        for column in table.columns
        if isinstance(spec := column.info.get(INFO_KEY), PiiSpec)
    )
    for entry in columns:
        _validate_anchor(table, entry)
    if link is None and not columns:
        return None
    return TableEntry(name=table.name, subject_link=link, columns=columns)


def _validate_anchor(table: Table, entry: ColumnEntry) -> None:
    """A declared retention anchor must be a datetime column on the same table.

    ``DateTime`` covers ``TIMESTAMP`` subclasses; ``Date`` is rejected — a
    retention clock needs an instant, not a day.
    """
    retention = entry.spec.retention
    if retention is None or retention.anchor is None:
        return
    if retention.anchor not in table.c:
        msg = (
            f"table {table.name!r}: column {entry.name!r} declares retention "
            f"anchor {retention.anchor!r}, which does not exist on the table"
        )
        raise ManifestError(msg)
    anchor_type = table.c[retention.anchor].type
    if not isinstance(anchor_type, DateTime):
        msg = (
            f"table {table.name!r}: column {entry.name!r} declares retention "
            f"anchor {retention.anchor!r}, which is {anchor_type!r} — not a "
            f"datetime column"
        )
        raise ManifestError(msg)
