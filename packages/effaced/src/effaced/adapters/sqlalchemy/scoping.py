"""Shared subject-scoping helpers for the SQLAlchemy step executors.

Extracted verbatim from the erasure executor so the rectification executor
shares the exact same hop-chain semantics — the aliasing invariants here
were learned the hard way (self-referential hops auto-correlate without
them) and must never diverge between the executors. The exporter and the
retention sweeper converge here too (ADR 0025): there is one composite-
matching subject predicate, never a fork.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import ColumnElement, select, tuple_

from effaced.annotations import CompositeSubjectId, SubjectIdentifier, parse_canonical
from effaced.exceptions import ManifestError, SubjectResolutionError

if TYPE_CHECKING:
    from collections.abc import Iterable

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
    metadata: MetaData,
    graph: SubjectGraph,
    name: str,
    subject_id: SubjectIdentifier,
) -> ColumnElement[bool]:
    """One table's rows-belong-to-this-subject predicate.

    Built from the subject outward as nested ``IN`` subqueries; every
    inner level is aliased so self-referential hops and revisited
    tables never collide, and only the outermost level is the raw
    table the surrounding DELETE/UPDATE binds to. The subject anchor is
    matched over the *whole* ordered key in
    :attr:`~effaced.SubjectGraph.subject_id_columns`: one column produces
    the same ``col == value`` predicate it always did, a composite key a
    row-value tuple equality (ADR 0025).

    Args:
        metadata: The ``MetaData`` the application's tables are mounted on.
        graph: Resolved hop chains from each table to the subject.
        name: The table whose rows the predicate scopes.
        subject_id: The subject identifier — a single-column ``str`` or a
            composite :class:`~effaced.CompositeSubjectId`. Each component
            is coerced to its subject column's python type for typed-
            parameter drivers.

    Returns:
        A boolean predicate matching exactly the one subject's rows.

    Raises:
        ManifestError: If a hop references a table missing from the
            bound metadata.
        SubjectResolutionError: If the identifier's arity disagrees with
            the declared subject-id columns, or a component cannot carry
            its column's type.
    """
    columns = graph.subject_id_columns
    values = subject_values(columns, subject_id)
    hops = graph.access(name).hops
    inner: FromClause = lookup_table(metadata, graph.subject_table)
    if hops:
        inner = inner.alias()
    coerced = coerce_subject_values((inner.c[column] for column in columns), values)
    predicate = _subject_anchor(inner, columns, coerced)
    for depth, hop in enumerate(reversed(hops)):
        source: FromClause = lookup_table(metadata, hop.source_table)
        if depth < len(hops) - 1:
            source = source.alias()
        subquery = select(*(inner.c[name] for name in hop.target_columns)).where(predicate)
        predicate = grouped(source, hop.source_columns).in_(subquery)
        inner = source
    return predicate


def _subject_anchor(
    inner: FromClause,
    columns: tuple[str, ...],
    coerced: tuple[object, ...],
) -> ColumnElement[bool]:
    """The innermost subject-id equality over the whole ordered key.

    A single column yields ``col == value`` — byte-identical to the pre-
    composite predicate. A composite key compares the row-value tuple of
    the subject columns against the row-value tuple of the coerced
    components (the existing :func:`grouped` helper, reused).
    """
    anchor = grouped(inner, columns)
    if len(columns) == 1:
        return anchor == coerced[0]
    return anchor == coerced


def subject_values(columns: tuple[str, ...], subject_id: SubjectIdentifier) -> tuple[str, ...]:
    """Decompose a subject identifier into per-column values, in column order.

    A :class:`~effaced.CompositeSubjectId`'s values align positionally to the
    declared columns. A bare ``str`` is the single-column case against a one-
    column schema; against a multi-column schema it is read as a *canonical*
    composite string (the form the audit trail and outbox store) and parsed
    back, so a replayed or requeued operation re-decomposes the same key it
    was erased under. effaced matches the whole ordered key, so the resulting
    arity must agree exactly — a mismatch is never silently coerced into a
    partial-key match (ADR 0025).

    Args:
        columns: The declared subject-id columns, in order.
        subject_id: The composite identifier, the single-column ``str``, or a
            canonical composite string from the trail.

    Returns:
        One string value per declared column, in declared order.

    Raises:
        SubjectResolutionError: If the identifier's arity disagrees with
            the number of declared columns.
    """
    if isinstance(subject_id, CompositeSubjectId):
        values: tuple[str, ...] = subject_id.values
    elif len(columns) > 1:
        # The trail stores the canonical string; parse it back to the key.
        values = parse_canonical(subject_id).values
    else:
        values = (subject_id,)
    if len(values) != len(columns):
        kind = "composite" if len(values) > 1 else "single-column"
        msg = (
            f"{kind} subject identifier has {len(values)} value(s) but the "
            f"manifest declares {len(columns)} subject-id column(s) "
            f"{list(columns)!r}; effaced matches the whole ordered key"
        )
        raise SubjectResolutionError(msg)
    return values


def coerce_subject_values(
    columns: Iterable[ColumnElement[Any]], values: tuple[str, ...]
) -> tuple[object, ...]:
    """Coerce each component to its subject column's python type.

    Per-column wrapper around :func:`coerce_subject_id`: the single-column
    case is one element and behaves exactly as it did before composite keys.

    Args:
        columns: The subject-id column expressions, in order, aligned to
            ``values``.
        values: The string components, in declared column order.

    Returns:
        Each component as its column's python type.

    Raises:
        SubjectResolutionError: If a component cannot carry its column's
            type.
    """
    return tuple(
        coerce_subject_id(column, value) for column, value in zip(columns, values, strict=True)
    )


def coerce_subject_id(column: ColumnElement[Any], subject_id: str) -> object:
    """Coerce one subject-id component to its column's python type.

    The published identifier components are strings, but subject columns are
    often integers; typed-parameter drivers (psycopg 3 binary mode) reject
    ``integer = text`` comparisons that quoted-literal dialects forgive.

    Args:
        column: The subject identifier column.
        subject_id: The published string component.

    Returns:
        The component as the column's python type.

    Raises:
        SubjectResolutionError: If the component cannot carry the column's
            type.
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
    """One column, or a row-value tuple for composite keys.

    Args:
        source: The clause holding the columns.
        names: The column names on it (a foreign key, or the subject key).

    Returns:
        The column itself, or a row-value tuple when composite.
    """
    if len(names) == 1:
        return source.c[names[0]]
    return tuple_(*(source.c[name] for name in names))
