"""Shared subject-scoping helpers for the SQLAlchemy step executors.

Extracted verbatim from the erasure executor so the rectification executor
shares the exact same hop-chain semantics — the aliasing invariants here
were learned the hard way (self-referential hops auto-correlate without
them) and must never diverge between the executors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import ColumnElement, select, tuple_

from effaced.exceptions import ManifestError, SubjectResolutionError

if TYPE_CHECKING:
    from sqlalchemy import FromClause, MetaData, Table

    from effaced.manifest import SubjectGraph


def lookup_table(metadata: MetaData, name: str) -> Table:
    """Look one manifest table up in the bound metadata.

    Args:
        metadata: The ``MetaData`` the application's tables are mounted on.
        name: The manifest table name.

    Returns:
        The mounted ``Table``.

    Raises:
        ManifestError: If the table is not in the bound metadata.
    """
    try:
        return metadata.tables[name]
    except KeyError as exc:
        msg = f"the plan references table {name!r}, which is not in the bound metadata"
        raise ManifestError(msg) from exc


def subject_scope(
    metadata: MetaData, graph: SubjectGraph, name: str, subject_id: str
) -> ColumnElement[bool]:
    """One table's rows-belong-to-this-subject predicate.

    Built from the subject outward as nested ``IN`` subqueries; every
    inner level is aliased so self-referential hops and revisited
    tables never collide, and only the outermost level is the raw
    table the surrounding DELETE/UPDATE binds to.

    Args:
        metadata: The ``MetaData`` the application's tables are mounted on.
        graph: Resolved hop chains from each table to the subject.
        name: The table whose rows the predicate scopes.
        subject_id: Identifier on the subject table, coerced to the
            subject column's python type for typed-parameter drivers.

    Returns:
        A boolean predicate matching exactly the one subject's rows.

    Raises:
        ManifestError: If a hop references a table missing from the
            bound metadata.
        SubjectResolutionError: If the id cannot carry the subject
            column's type.
    """
    hops = graph.access(name).hops
    inner: FromClause = lookup_table(metadata, graph.subject_table)
    if hops:
        inner = inner.alias()
    column = inner.c[graph.subject_id_column]
    predicate: ColumnElement[bool] = column == coerce_subject_id(column, subject_id)
    for depth, hop in enumerate(reversed(hops)):
        source: FromClause = lookup_table(metadata, hop.source_table)
        if depth < len(hops) - 1:
            source = source.alias()
        subquery = select(*(inner.c[name] for name in hop.target_columns)).where(predicate)
        predicate = grouped(source, hop.source_columns).in_(subquery)
        inner = source
    return predicate


def coerce_subject_id(column: ColumnElement[Any], subject_id: str) -> object:
    """Coerce the subject id to the column's python type.

    The published ``subject_id`` is a string, but subject columns are
    often integers; typed-parameter drivers (psycopg 3 binary mode)
    reject ``integer = text`` comparisons that quoted-literal dialects
    forgive.

    Args:
        column: The subject identifier column.
        subject_id: The published string identifier.

    Returns:
        The identifier as the column's python type.

    Raises:
        SubjectResolutionError: If the id cannot carry the column's type.
    """
    try:
        python_type = column.type.python_type
    except NotImplementedError:
        # A type effaced cannot interpret; let the dialect be the authority.
        return subject_id
    if python_type is str:
        return subject_id
    try:
        return python_type(subject_id)
    except (TypeError, ValueError) as exc:
        msg = (
            f"subject id {subject_id!r} cannot be interpreted as the subject "
            f"column's type ({python_type.__name__})"
        )
        raise SubjectResolutionError(msg) from exc


def grouped(source: FromClause, names: tuple[str, ...]) -> ColumnElement[Any]:
    """One column, or a row-value tuple for composite foreign keys.

    Args:
        source: The clause holding the columns.
        names: The foreign-key column names on it.

    Returns:
        The column itself, or a row-value tuple when composite.
    """
    if len(names) == 1:
        return source.c[names[0]]
    return tuple_(*(source.c[name] for name in names))
