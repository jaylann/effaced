"""Resolve subject-link paths against SQLAlchemy ORM mappers.

The private walk helpers (``_find_subject``, ``_mappers_by_table``,
``_resolve_path``) are co-consumed by ``reachability_linter.py`` in this same
package: the linter probes them per table to collect findings rather than
raising on the first failure, so the two must share one walk — never fork it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Table
from sqlalchemy.orm import Mapper

from effaced.exceptions import SubjectResolutionError
from effaced.manifest.resolution import (
    JoinHop,
    SubjectGraph,
    TableAccessPlan,
    fk_safe_deletion_order,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from sqlalchemy import MetaData
    from sqlalchemy.orm import RelationshipProperty, registry

    from effaced.manifest import DataMap, TableEntry


def resolve_subject_graph(data_map: DataMap, orm_registry: registry) -> SubjectGraph:
    """Resolve every subject-link path in a data map into a subject graph.

    Each table's dotted relationship path is walked against the ORM
    mappers and flattened into foreign-key column pairs; the resulting
    accesses are ordered FK-safely for deletion (children before parents,
    subject table last). Joined-table inheritance is keyed by each
    mapper's local table only.

    Args:
        data_map: The collected manifest (see
            :func:`~effaced.adapters.sqlalchemy.collect_data_map`).
        orm_registry: The ORM registry holding the mapped classes — for
            declarative styles, ``Base.registry``.

    Returns:
        The resolved, FK-safely ordered :class:`SubjectGraph`.

    Raises:
        SubjectResolutionError: If no (or more than one) table declares
            ``subject_link("")``, a table holds personal data without a
            subject link, a table is not ORM-mapped, a path segment is not
            a relationship, a path joins through a many-to-many secondary
            table, a path does not end at the subject table, the declared
            subject id column does not exist or is declared on a
            non-subject table, or the foreign keys between resolved
            tables form a cycle.
    """
    subject = _find_subject(data_map)
    mappers = _mappers_by_table(orm_registry)
    subject_mapper = _mapper_for(subject, mappers)
    subject_id_column = subject.subject_link.subject_id_column  # type: ignore[union-attr]  # _find_subject only returns entries that carry a subject_link
    if subject_id_column not in subject_mapper.local_table.columns:
        msg = (
            f"subject table {subject.name!r} has no column "
            f"{subject_id_column!r} (declared subject_id_column)"
        )
        raise SubjectResolutionError(msg)
    plans = {
        entry.name: TableAccessPlan(
            table=entry.name,
            hops=_resolve_path(entry, mappers, subject.name),
            fully_pii_owned=_fully_pii_owned(entry, _mapper_for(entry, mappers)),
        )
        for entry in data_map.tables
    }
    order = fk_safe_deletion_order(tuple(plans), _fk_edges(orm_registry.metadata, frozenset(plans)))
    return SubjectGraph(
        subject_table=subject.name,
        subject_id_column=subject_id_column,
        accesses=tuple(plans[name] for name in order),
    )


def _find_subject(data_map: DataMap) -> TableEntry:
    """Return the single entry declaring ``subject_link("")``."""
    subjects = [
        entry
        for entry in data_map.tables
        if entry.subject_link is not None and entry.subject_link.is_subject_table
    ]
    if not subjects:
        msg = 'no subject table declared: exactly one table must carry subject_link("")'
        raise SubjectResolutionError(msg)
    if len(subjects) > 1:
        names = ", ".join(repr(entry.name) for entry in subjects)
        msg = (
            f"multiple subject tables declared ({names}): "
            'exactly one table may carry subject_link("")'
        )
        raise SubjectResolutionError(msg)
    return subjects[0]


def _mappers_by_table(orm_registry: registry) -> dict[str, Mapper[Any]]:
    """Index the registry's mappers by their local table name."""
    return {
        mapper.local_table.name: mapper
        for mapper in orm_registry.mappers
        if isinstance(mapper.local_table, Table)
    }


def _mapper_for(entry: TableEntry, mappers: Mapping[str, Mapper[Any]]) -> Mapper[Any]:
    """Return the mapper for one entry, or fail loudly."""
    mapper = mappers.get(entry.name)
    if mapper is None:
        msg = (
            f"table {entry.name!r} is in the data map but not mapped by the "
            f"given registry; subject-link paths require ORM-mapped classes"
        )
        raise SubjectResolutionError(msg)
    return mapper


def _fully_pii_owned(entry: TableEntry, mapper: Mapper[Any]) -> bool:
    """Whether the row holds nothing but annotated PII and structural keys.

    True when every physical column is PII-annotated, a primary-key
    member, or a foreign-key member. Anything else (an unannotated payload
    column) means row deletion would erase more than the manifest declares,
    so the planner must fall back to column-level anonymization.

    Keys are exempt as structural plumbing (ADR 0007). A content-bearing
    *natural* primary key (an email used as PK, say) therefore counts as
    owned — annotate such columns as PII anyway; collection keeps them in
    the manifest either way.
    """
    table = mapper.local_table
    if not isinstance(table, Table):  # pragma: no cover - filtered out by _mappers_by_table
        return False
    annotated = {column.name for column in entry.columns}
    key_members = {
        column.name for constraint in table.foreign_key_constraints for column in constraint.columns
    }
    return all(
        column.name in annotated or column.primary_key or column.name in key_members
        for column in table.columns
    )


def _resolve_path(
    entry: TableEntry,
    mappers: Mapping[str, Mapper[Any]],
    subject_table: str,
) -> tuple[JoinHop, ...]:
    """Flatten one entry's dotted relationship path into join hops."""
    link = entry.subject_link
    if link is None:
        msg = (
            f"table {entry.name!r} holds personal data but declares no "
            f"subject_link; declare how its rows reach the subject"
        )
        raise SubjectResolutionError(msg)
    if not link.is_subject_table and link.subject_id_column != "id":
        msg = (
            f"table {entry.name!r}: subject_id_column "
            f"{link.subject_id_column!r} is only meaningful on the subject "
            f'table itself (subject_link("")); it would be silently ignored here'
        )
        raise SubjectResolutionError(msg)
    mapper = _mapper_for(entry, mappers)
    hops: list[JoinHop] = []
    for segment in link.path.split(".") if link.path else ():
        relationship = _relationship(entry, link.path, mapper, segment)
        hops.append(_hop(relationship))
        mapper = relationship.mapper
    if hops and hops[-1].target_table != subject_table:
        msg = (
            f"table {entry.name!r}: subject link path {link.path!r} ends at "
            f"{hops[-1].target_table!r}, not at the subject table {subject_table!r}"
        )
        raise SubjectResolutionError(msg)
    return tuple(hops)


def _relationship(
    entry: TableEntry,
    path: str,
    mapper: Mapper[Any],
    segment: str,
) -> RelationshipProperty[Any]:
    """Look up one path segment as a relationship on the current mapper."""
    try:
        relationship = mapper.relationships[segment]
    except KeyError:
        msg = (
            f"table {entry.name!r}: subject link path {path!r} — {segment!r} "
            f"is not a relationship on {mapper.local_table!r}"
        )
        raise SubjectResolutionError(msg) from None
    if relationship.secondary is not None:
        msg = (
            f"table {entry.name!r}: relationship {segment!r} joins through a "
            f"secondary (many-to-many) table; not supported in subject-link paths yet"
        )
        raise SubjectResolutionError(msg)
    return relationship


def _hop(relationship: RelationshipProperty[Any]) -> JoinHop:
    """Translate one relationship into a pure column-pair hop."""
    target = relationship.target
    source = relationship.parent.local_table
    if not isinstance(target, Table) or not isinstance(source, Table):
        msg = (
            f"relationship {relationship.key!r} joins selectables, not plain "
            f"tables; not supported in subject-link paths"
        )
        raise SubjectResolutionError(msg)
    pairs = relationship.local_remote_pairs
    if not pairs:
        msg = (
            f"relationship {relationship.key!r} has no local/remote column "
            f"pairs to join on; not supported in subject-link paths"
        )
        raise SubjectResolutionError(msg)
    return JoinHop(
        source_table=source.name,
        source_columns=tuple(local.name for local, _ in pairs),
        target_table=target.name,
        target_columns=tuple(remote.name for _, remote in pairs),
    )


def _fk_edges(metadata: MetaData, graph_tables: frozenset[str]) -> Iterable[tuple[str, str]]:
    """Yield (child, parent) FK edges between tables in the graph."""
    for table in metadata.tables.values():
        if table.name not in graph_tables:
            continue
        for constraint in table.foreign_key_constraints:
            parent = constraint.referred_table.name
            if parent in graph_tables:
                yield (table.name, parent)
