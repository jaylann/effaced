"""Lint SQLAlchemy metadata for data the manifest does not cover."""

from __future__ import annotations

from typing import TYPE_CHECKING

from effaced.adapters.sqlalchemy.info import INFO_KEY
from effaced.annotations import PiiSpec, SubjectLink
from effaced.exceptions import ManifestError
from effaced.lint import CompletenessFinding

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import MetaData, Table

_EFFACED_TABLE_PREFIX = "effaced_"
"""Tables mounted by :func:`effaced.bind_tables` are effaced-owned — exempt."""


def lint_completeness(metadata: MetaData) -> tuple[CompletenessFinding, ...]:
    """Find every place the metadata could hold undeclared personal data.

    The exact complement of :func:`collect_data_map`: every table is either
    in the data map, returned here as a whole-table finding, or an
    effaced-owned ``effaced_*`` table — and within a mapped table, every
    column is either annotated, a primary/foreign key, or returned here as
    a column finding. Nothing falls through silently.

    Findings are questions, not verdicts — gate on them in CI with
    :func:`effaced.testing.assert_data_map_complete`, which lets you exempt
    stores and fields you have consciously judged to hold no personal data.

    Args:
        metadata: The ``MetaData`` holding your mapped tables (for the ORM,
            ``Base.metadata``).

    Returns:
        All findings, in deterministic table order, then column order.

    Raises:
        ManifestError: If an ``info`` entry under the effaced key is not a
            recognised annotation object — exactly the metadata
            :func:`collect_data_map` rejects, so the complement contract
            holds even on malformed input.
    """
    return tuple(
        finding
        for table in metadata.sorted_tables
        if not table.name.startswith(_EFFACED_TABLE_PREFIX)
        for finding in _lint_table(table)
    )


def _lint_table(table: Table) -> Iterator[CompletenessFinding]:
    """Yield one table's findings (see :func:`lint_completeness`)."""
    link = table.info.get(INFO_KEY)
    if link is not None and not isinstance(link, SubjectLink):
        msg = f"table {table.name!r}: info[{INFO_KEY!r}] is not a SubjectLink"
        raise ManifestError(msg)
    annotated = {
        column.name for column in table.columns if isinstance(column.info.get(INFO_KEY), PiiSpec)
    }
    if link is None and not annotated:
        yield CompletenessFinding(table=table.name)
        return
    for column in table.columns:
        if column.primary_key or column.foreign_keys or column.name in annotated:
            continue
        yield CompletenessFinding(table=table.name, column=column.name)
