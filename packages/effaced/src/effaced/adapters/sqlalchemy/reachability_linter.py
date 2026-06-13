"""Lint a data map for tables the erasure planner cannot reach."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from effaced.adapters.sqlalchemy.resolution import _mappers_by_table, _resolve_path
from effaced.exceptions import SubjectResolutionError
from effaced.lint import ReachabilityFinding
from effaced.manifest.resolution import fk_safe_deletion_order

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from sqlalchemy import MetaData
    from sqlalchemy.orm import Mapper, registry

    from effaced.manifest import DataMap, TableEntry


def lint_reachability(data_map: DataMap, orm_registry: registry) -> tuple[ReachabilityFinding, ...]:
    """Find every annotated table the erasure planner cannot route to a subject.

    :func:`resolve_subject_graph` raises on the *first* unreachable table, so it
    answers "can this whole map be planned?" but not "which tables are the
    problem?". This linter probes each concern independently and collects a
    finding per gap instead of raising, the way :func:`lint_completeness`
    complements :func:`collect_data_map`.

    It is the exact inverse of resolution: ``lint_reachability(...) == ()`` if
    and only if :func:`resolve_subject_graph` succeeds on the same inputs. An
    empty result is the assurance that every annotated store has a subject path
    the planner can walk; any finding names a store whose data would otherwise
    be silently never erased.

    Findings are questions, not verdicts — a table may be unreachable because
    its ``subject_link`` is wrong, because it is not ORM-mapped, or because the
    foreign keys form a cycle. effaced names the gap; the fix (and the judgement
    that a store needs no path at all) stays yours.

    Args:
        data_map: The collected manifest (see :func:`collect_data_map`).
        orm_registry: The ORM registry holding the mapped classes — for
            declarative styles, ``Base.registry``.

    Returns:
        All findings, in deterministic order: the subject-anchor findings
        first, then one per unreachable non-subject table in manifest order,
        then a graph-level foreign-key-cycle finding if one remains.

    Raises:
        ManifestError: If an ``info`` entry under the effaced key is not a
            recognised annotation object — exactly the malformed metadata
            :func:`collect_data_map` rejects. Lintable conditions (a missing or
            unreachable path) never raise; they become findings.
    """
    subjects = _subject_tables(data_map)
    if len(subjects) != 1:
        return tuple(_anchor_findings(subjects))
    subject = subjects[0]
    mappers = _mappers_by_table(orm_registry)
    findings: list[ReachabilityFinding] = list(_subject_id_findings(subject, mappers))
    reached: list[str] = [subject.name]
    for entry in data_map.tables:
        if entry.name == subject.name:
            continue
        reason = _path_failure(entry, mappers, subject.name)
        if reason is None:
            reached.append(entry.name)
        else:
            findings.append(ReachabilityFinding(table=entry.name, reason=reason))
    findings.extend(_cycle_findings(orm_registry.metadata, reached))
    return tuple(findings)


def _subject_tables(data_map: DataMap) -> list[TableEntry]:
    """Every entry declaring ``subject_link("")`` — the anchor candidates."""
    return [
        entry
        for entry in data_map.tables
        if entry.subject_link is not None and entry.subject_link.is_subject_table
    ]


def _anchor_findings(subjects: list[TableEntry]) -> Iterator[ReachabilityFinding]:
    """Findings for the wrong number of subject anchors (zero or many)."""
    if not subjects:
        yield ReachabilityFinding(
            reason='no subject table declared: exactly one table must carry subject_link("")'
        )
        return
    for entry in subjects[1:]:
        yield ReachabilityFinding(
            table=entry.name,
            reason='more than one table carries subject_link("") — exactly one may',
        )


def _subject_id_findings(
    subject: TableEntry, mappers: Mapping[str, Mapper[Any]]
) -> Iterator[ReachabilityFinding]:
    """Findings for an unmapped anchor or an absent subject-id column on it."""
    mapper = mappers.get(subject.name)
    if mapper is None:
        yield ReachabilityFinding(
            table=subject.name,
            reason=(
                f"table {subject.name!r} is in the data map but not mapped by the "
                f"given registry; subject-link paths require ORM-mapped classes"
            ),
        )
        return
    link = subject.subject_link
    if link is not None and link.subject_id_column not in mapper.local_table.columns:
        yield ReachabilityFinding(
            table=subject.name,
            reason=(
                f"subject table {subject.name!r} has no column "
                f"{link.subject_id_column!r} (declared subject_id_column)"
            ),
        )


def _path_failure(
    entry: TableEntry, mappers: Mapping[str, Mapper[Any]], subject_table: str
) -> str | None:
    """Walk one entry's subject path; return the failure message or ``None``.

    Co-consumes :func:`resolve_subject_graph`'s private ``_resolve_path`` so the
    linter probes the exact same walk the planner would — never a fork of it. A
    :class:`SubjectResolutionError` becomes the finding's reason.
    """
    try:
        _resolve_path(entry, mappers, subject_table)
    except SubjectResolutionError as exc:
        return str(exc)
    return None


def _cycle_findings(metadata: MetaData, reached: list[str]) -> Iterator[ReachabilityFinding]:
    """A graph-level finding if the reachable tables' foreign keys cycle."""
    try:
        fk_safe_deletion_order(tuple(reached), _fk_edges(metadata, frozenset(reached)))
    except SubjectResolutionError as exc:
        yield ReachabilityFinding(reason=str(exc))


def _fk_edges(metadata: MetaData, graph_tables: frozenset[str]) -> list[tuple[str, str]]:
    """The ``(child, parent)`` foreign-key edges between reachable tables."""
    edges: list[tuple[str, str]] = []
    for table in metadata.tables.values():
        if table.name not in graph_tables:
            continue
        for constraint in table.foreign_key_constraints:
            parent = constraint.referred_table.name
            if parent in graph_tables:
                edges.append((table.name, parent))
    return edges
