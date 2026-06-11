"""The :class:`Exporter` — Art. 15 collection across database and resolvers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from effaced.audit.event import AuditEvent
from effaced.audit.event_type import AuditEventType
from effaced.exceptions import ManifestError, ResolverError, SubjectResolutionError
from effaced.export.bundle import ExportBundle, ExportRecord

if TYPE_CHECKING:
    from sqlalchemy import Column, MetaData, Select
    from sqlalchemy.orm import Session
    from sqlalchemy.sql.elements import ColumnElement
    from sqlalchemy.sql.expression import FromClause

    from effaced.annotations import SubjectRef
    from effaced.audit.sink import AuditSink
    from effaced.manifest import DataMap, SubjectGraph, TableAccessPlan, TableEntry
    from effaced.resolvers import Resolver, ResolverExport, ResolverRegistry


class Exporter:
    """Collects all of a subject's personal data into one structured bundle.

    Walks the data map for local data and fans out to registered resolvers
    for external systems. Resolver failures never silently shrink the
    bundle — they are recorded in ``incomplete_sources``.
    """

    def __init__(
        self,
        data_map: DataMap,
        graph: SubjectGraph,
        metadata: MetaData,
        audit_sink: AuditSink,
        registry: ResolverRegistry | None = None,
    ) -> None:
        """Wire the exporter to a manifest, its resolved graph, and resolvers.

        Args:
            data_map: The application's data map (column metadata).
            graph: The resolved subject graph for the same manifest (see
                :func:`~effaced.adapters.sqlalchemy.resolve_subject_graph`)
                — provides each table's path to the subject.
            metadata: The schema metadata holding the mapped tables; the
                exporter reads rows through its table handles.
            audit_sink: Receives one ``EXPORT_REQUESTED`` and one
                ``EXPORT_COMPLETED`` event per export.
            registry: Resolvers for external systems; ``None`` exports the
                local database only.

        Raises:
            ManifestError: If the data map and the graph do not describe
                the same set of tables, or a declared table is missing
                from ``metadata``.
        """
        _check_agreement(data_map, graph, metadata)
        self._data_map = data_map
        self._graph = graph
        self._metadata = metadata
        self._audit_sink = audit_sink
        self._registry = registry

    def export_subject(
        self,
        session: Session,
        subject_id: str,
        *,
        refs: tuple[SubjectRef, ...] = (),
    ) -> ExportBundle:
        """Collect everything held on one subject (Art. 15).

        Each ref is routed to the resolver whose ``name`` equals the ref's
        ``kind`` (ADR 0008). A registered resolver with no matching ref is
        skipped — "the subject has no identity in that system" is a
        complete answer, recorded in the ``EXPORT_COMPLETED`` payload's
        ``skipped_resolvers``, not in ``incomplete_sources``. A resolver
        call that fails puts the resolver's name in ``incomplete_sources``
        instead of failing the export. A local database failure propagates
        after ``EXPORT_REQUESTED`` was appended — a requested-but-never-
        completed trail is the abandonment marker. Input validation
        (subject id coercion, ref-kind matching) raises *before*
        ``EXPORT_REQUESTED`` is appended: a malformed call never became a
        data-subject request, so it deliberately leaves no audit trace.

        Blocking call; resolver fan-out runs on an internal event loop, so
        it must not be invoked on a running event-loop thread — in async
        web apps dispatch via a threadpool (e.g. FastAPI's
        ``run_in_threadpool``). See ADR 0006.

        Args:
            session: An open database session; reads only, never writes.
            subject_id: Identifier on the subject table (see
                :class:`~effaced.annotations.SubjectLink`).
            refs: External-system references for resolver fan-out.

        Returns:
            The structured bundle including Art. 15 metadata (purposes,
            legal bases, retention reasons).

        Raises:
            SubjectResolutionError: If ``subject_id`` cannot be coerced to
                the subject id column's type.
            ResolverError: If a ref's ``kind`` matches no registered
                resolver — a typo must not silently drop an external
                source from the answer.
        """
        subject_column = self._metadata.tables[self._graph.subject_table].c[
            self._graph.subject_id_column
        ]
        coerced_id = _coerce_subject_id(subject_column, subject_id)
        resolvers = self._registry.all() if self._registry is not None else ()
        jobs = _match_refs(resolvers, refs)
        self._append_event(
            AuditEventType.EXPORT_REQUESTED,
            subject_id,
            {"ref_count": len(refs), "resolver_count": len(resolvers)},
        )
        local = _local_records(session, self._data_map, self._graph, self._metadata, coerced_id)
        external, incomplete = _collect_external(jobs)
        matched = {resolver.name for resolver, _ in jobs}
        skipped = tuple(resolver.name for resolver in resolvers if resolver.name not in matched)
        bundle = ExportBundle(
            subject_id=subject_id,
            generated_at=datetime.now(UTC),
            records=local + external,
            incomplete_sources=incomplete,
        )
        self._append_event(
            AuditEventType.EXPORT_COMPLETED,
            subject_id,
            {
                "record_count": len(bundle.records),
                "incomplete_source_count": len(incomplete),
                "incomplete_sources": ",".join(incomplete),
                "skipped_resolvers": ",".join(skipped),
            },
        )
        return bundle

    def _append_event(
        self,
        event_type: AuditEventType,
        subject_id: str,
        payload: dict[str, str | int | bool],
    ) -> None:
        """Mirror one export milestone into the audit trail."""
        self._audit_sink.append(
            AuditEvent(
                event_id=uuid4(),
                event_type=event_type,
                subject_ref=subject_id,
                occurred_at=datetime.now(UTC),
                payload=payload,
            )
        )


def _check_agreement(data_map: DataMap, graph: SubjectGraph, metadata: MetaData) -> None:
    """Fail at construction when manifest, graph, and metadata disagree."""
    declared = {entry.name for entry in data_map.tables}
    resolved = set(graph.deletion_order)
    if declared != resolved:
        msg = (
            f"data map and subject graph disagree: tables only in the data "
            f"map {sorted(declared - resolved)!r}, only in the graph "
            f"{sorted(resolved - declared)!r}"
        )
        raise ManifestError(msg)
    missing = sorted(declared - set(metadata.tables))
    if missing:
        msg = f"tables {missing!r} are in the data map but not in the given metadata"
        raise ManifestError(msg)


def _coerce_subject_id(column: Column[Any], subject_id: str) -> object:
    """Coerce the string subject id to the subject id column's type.

    SQLite compares ``users.id = '42'`` happily; stricter dialects
    (Postgres) refuse to compare an integer column with a text parameter,
    so the comparison value must carry the column's Python type.
    """
    try:
        python_type = column.type.python_type
    except NotImplementedError:
        # The type is one effaced cannot interpret (e.g. a UserDefinedType);
        # pass the string through and let the dialect be the authority.
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


def _local_records(
    session: Session,
    data_map: DataMap,
    graph: SubjectGraph,
    metadata: MetaData,
    coerced_id: object,
) -> tuple[ExportRecord, ...]:
    """Collect every annotated value reachable from the subject."""
    records: list[ExportRecord] = []
    for entry in data_map.tables:
        if not entry.columns:
            continue
        statement = _statement_for(entry, graph.access(entry.name), graph, metadata, coerced_id)
        for row in session.execute(statement).mappings():
            records.extend(_row_records(entry, dict(row)))
    return tuple(records)


def _statement_for(
    entry: TableEntry,
    plan: TableAccessPlan,
    graph: SubjectGraph,
    metadata: MetaData,
    coerced_id: object,
) -> Select[Any]:
    """One SELECT of the entry's annotated columns for one subject.

    Linked tables filter through a single EXISTS whose inner select walks
    the hop chain over a fresh alias per hop target — aliasing keeps
    self-referential hops unambiguous.
    """
    table = metadata.tables[entry.name]
    selected = tuple(table.c[column.name] for column in entry.columns)
    if plan.is_subject_table:
        return (
            table.select()
            .with_only_columns(*selected)
            .where(table.c[graph.subject_id_column] == coerced_id)
        )
    aliases = tuple(metadata.tables[hop.target_table].alias() for hop in plan.hops)
    conditions: list[ColumnElement[bool]] = []
    outer: FromClause = table
    for hop, target in zip(plan.hops, aliases, strict=True):
        conditions.extend(
            outer.c[source_column] == target.c[target_column]
            for source_column, target_column in zip(
                hop.source_columns, hop.target_columns, strict=True
            )
        )
        outer = target
    conditions.append(outer.c[graph.subject_id_column] == coerced_id)
    inner = aliases[0].select().where(*conditions)
    return table.select().with_only_columns(*selected).where(inner.exists())


def _row_records(entry: TableEntry, row: dict[str, object]) -> tuple[ExportRecord, ...]:
    """One record per annotated column of one row, with its metadata."""
    return tuple(
        ExportRecord(
            source=entry.name,
            field=column.name,
            category=column.spec.category,
            value=row[column.name],
            legal_basis=column.spec.legal_basis,
            purpose=column.spec.purpose,
            retention_reason=column.spec.retention.reason if column.spec.retention else None,
        )
        for column in entry.columns
    )


def _match_refs(
    resolvers: tuple[Resolver, ...],
    refs: tuple[SubjectRef, ...],
) -> tuple[tuple[Resolver, SubjectRef], ...]:
    """Pair each ref with the resolver named after its kind (ADR 0008)."""
    names = {resolver.name for resolver in resolvers}
    unmatched = sorted({ref.kind for ref in refs} - names)
    if unmatched:
        msg = (
            f"no resolver registered for ref kind(s) {unmatched!r}; refs are "
            f"routed to the resolver whose name equals the ref's kind"
        )
        raise ResolverError(msg)
    return tuple(
        (resolver, ref) for resolver in resolvers for ref in refs if ref.kind == resolver.name
    )


def _collect_external(
    jobs: tuple[tuple[Resolver, SubjectRef], ...],
) -> tuple[tuple[ExportRecord, ...], tuple[str, ...]]:
    """Fan out to resolvers; failures become incomplete sources, never raise."""
    if not jobs:
        return (), ()
    fan_out = _fan_out(jobs)
    try:
        outcomes = asyncio.run(fan_out)
    except RuntimeError:
        fan_out.close()  # asyncio.run refused (running loop) without consuming it
        raise
    records: list[ExportRecord] = []
    incomplete: list[str] = []
    for (resolver, _), outcome in zip(jobs, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            if resolver.name not in incomplete:
                incomplete.append(resolver.name)
        else:
            records.extend(outcome.records)
    return tuple(records), tuple(incomplete)


async def _fan_out(
    jobs: tuple[tuple[Resolver, SubjectRef], ...],
) -> list[ResolverExport | BaseException]:
    """Await every resolver call concurrently; exceptions are returned, not raised.

    The only event-loop ownership in the core (ADR 0006).
    """
    return await asyncio.gather(
        *(resolver.export_subject(ref) for resolver, ref in jobs),
        return_exceptions=True,
    )
