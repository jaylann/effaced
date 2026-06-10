"""Build a :class:`~effaced.manifest.DataMap` from SQLAlchemy metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING

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
            recognised annotation object.
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
    if link is None and not columns:
        return None
    return TableEntry(name=table.name, subject_link=link, columns=columns)
