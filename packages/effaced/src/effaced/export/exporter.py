"""The :class:`Exporter` — Art. 15 collection across database and resolvers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from effaced.adapters.sqlalchemy.scoping import (
    coerce_subject_values,
    subject_scope,
    subject_values,
)
from effaced.annotations import canonical_subject_id
from effaced.audit.event import AuditEvent
from effaced.audit.event_type import AuditEventType
from effaced.exceptions import ManifestError, ResolverError
from effaced.export.bundle import ExportBundle, ExportRecord

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import MetaData, Select
    from sqlalchemy.orm import Session

    from effaced.annotations import SubjectIdentifier, SubjectRef
    from effaced.audit.sink import AuditSink
    from effaced.manifest import DataMap, SubjectGraph, TableEntry
    from effaced.resolvers import Resolver, ResolverExport, ResolverRegistry

_ROW_STREAM_SIZE = 256
"""Rows fetched per round trip when streaming a local table's matched rows.

Bounds peak resident records to roughly one batch per table during a
streaming export — large enough to amortize round trips, small enough that
a wide subject footprint never materializes whole.
"""


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
        subject_id: SubjectIdentifier,
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
            subject_id: The subject identifier — a single-column ``str`` or
                a composite :class:`~effaced.CompositeSubjectId` aligned to
                the subject's
                :attr:`~effaced.SubjectLink.subject_id_columns`. It is
                stored in the audit trail as its canonical string and echoed
                back unchanged on the bundle.
            refs: External-system references for resolver fan-out.

        Returns:
            The structured bundle including Art. 15 metadata (purposes,
            legal bases, retention reasons).

        Raises:
            SubjectResolutionError: If ``subject_id``'s arity disagrees with
                the declared subject-id columns, or a component cannot be
                coerced to its column's type.
            ResolverError: If a ref's ``kind`` matches no registered
                resolver — a typo must not silently drop an external
                source from the answer.
        """
        ref = canonical_subject_id(subject_id)
        # Validate the identifier (arity + per-component coercion) before the
        # request is recorded: a malformed call never became a request.
        self._validate_subject_id(subject_id)
        resolvers = self._registry.all() if self._registry is not None else ()
        jobs = _match_refs(resolvers, refs)
        self._append_event(
            AuditEventType.EXPORT_REQUESTED,
            ref,
            {"ref_count": len(refs), "resolver_count": len(resolvers)},
        )
        local = _local_records(session, self._data_map, self._graph, self._metadata, subject_id)
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
            ref,
            {
                "record_count": len(bundle.records),
                "incomplete_source_count": len(incomplete),
                "incomplete_sources": ",".join(incomplete),
                "skipped_resolvers": ",".join(skipped),
            },
        )
        return bundle

    def iter_subject_records(
        self,
        session: Session,
        subject_id: SubjectIdentifier,
        *,
        refs: tuple[SubjectRef, ...] = (),
    ) -> Iterator[ExportRecord]:
        """Stream a subject's data record by record, bounding memory (Art. 15).

        The streaming companion to :meth:`export_subject`. It yields the
        **same** records, in the same order (local sources in data-map
        order, then external resolvers), but never builds the full
        :class:`~effaced.ExportBundle` tuple: local rows are fetched and
        emitted table by table off the database cursor, so peak memory is
        bounded by the widest single table's row set, not the subject's
        whole footprint. Use it to export large-footprint subjects without
        materializing every value at once; consumers that need the bundle's
        Art. 15 envelope (``generated_at``, ``schema_version``,
        ``incomplete_sources``) call :meth:`export_subject` instead.

        Memory bound: this streams the **local** side — peak resident local
        records are one table's matched rows (the cursor is drained per
        table, not accumulated). The **external** side is unchanged from
        :meth:`export_subject`: resolver fan-out runs concurrently on one
        internal event loop and each resolver's full
        :class:`~effaced.resolvers.ResolverExport` is gathered before its
        records are yielded, so a resolver returning a large export is not
        bounded here. The win is for subjects whose footprint is dominated
        by local database rows.

        Audit semantics match :meth:`export_subject` exactly:
        ``EXPORT_REQUESTED`` is appended once, eagerly, after input
        validation and before the first record is yielded;
        ``EXPORT_COMPLETED`` is appended once the iterator is fully
        consumed, carrying the same payload keys (``record_count``,
        ``incomplete_source_count``, ``incomplete_sources``,
        ``skipped_resolvers``). A consumer that abandons the iterator early
        (never exhausts it) leaves ``EXPORT_REQUESTED`` without a
        completion — the same requested-but-never-completed abandonment
        marker a failed :meth:`export_subject` leaves.

        Blocking call; resolver fan-out runs on an internal event loop, so
        it must not be invoked on a running event-loop thread — in async
        web apps dispatch via a threadpool (e.g. FastAPI's
        ``run_in_threadpool``). See ADR 0006.

        Args:
            session: An open database session; reads only, never writes.
            subject_id: The subject identifier — a single-column ``str`` or
                a composite :class:`~effaced.CompositeSubjectId` aligned to
                the subject's
                :attr:`~effaced.SubjectLink.subject_id_columns`.
            refs: External-system references for resolver fan-out.

        Yields:
            One :class:`~effaced.ExportRecord` per annotated value, local
            sources first (data-map order), then external resolvers.

        Raises:
            SubjectResolutionError: If ``subject_id``'s arity disagrees with
                the declared subject-id columns, or a component cannot be
                coerced to its column's type.
            ResolverError: If a ref's ``kind`` matches no registered
                resolver — a typo must not silently drop an external
                source from the answer.
        """
        ref = canonical_subject_id(subject_id)
        self._validate_subject_id(subject_id)
        resolvers = self._registry.all() if self._registry is not None else ()
        jobs = _match_refs(resolvers, refs)
        self._append_event(
            AuditEventType.EXPORT_REQUESTED,
            ref,
            {"ref_count": len(refs), "resolver_count": len(resolvers)},
        )
        count = 0
        for record in _iter_local_records(
            session, self._data_map, self._graph, self._metadata, subject_id
        ):
            count += 1
            yield record
        external, incomplete = _collect_external(jobs)
        for record in external:
            count += 1
            yield record
        matched = {resolver.name for resolver, _ in jobs}
        skipped = tuple(resolver.name for resolver in resolvers if resolver.name not in matched)
        self._append_event(
            AuditEventType.EXPORT_COMPLETED,
            ref,
            {
                "record_count": count,
                "incomplete_source_count": len(incomplete),
                "incomplete_sources": ",".join(incomplete),
                "skipped_resolvers": ",".join(skipped),
            },
        )

    def _validate_subject_id(self, subject_id: SubjectIdentifier) -> None:
        """Validate the identifier (arity + per-component coercion) eagerly.

        A malformed call must raise before ``EXPORT_REQUESTED`` is
        appended: it never became a data-subject request, so it leaves no
        audit trace. Shared by both the materializing and streaming paths.
        """
        subject_table = self._metadata.tables[self._graph.subject_table]
        columns = self._graph.subject_id_columns
        coerce_subject_values(
            (subject_table.c[name] for name in columns),
            subject_values(columns, subject_id),
        )

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


def _local_records(
    session: Session,
    data_map: DataMap,
    graph: SubjectGraph,
    metadata: MetaData,
    subject_id: SubjectIdentifier,
) -> tuple[ExportRecord, ...]:
    """Collect every annotated value reachable from the subject."""
    return tuple(_iter_local_records(session, data_map, graph, metadata, subject_id))


def _iter_local_records(
    session: Session,
    data_map: DataMap,
    graph: SubjectGraph,
    metadata: MetaData,
    subject_id: SubjectIdentifier,
) -> Iterator[ExportRecord]:
    """Yield every annotated value reachable from the subject, table by table.

    The lazy core both paths share: :func:`_local_records` drains it into a
    tuple for the materializing :meth:`Exporter.export_subject`, while
    :meth:`Exporter.iter_subject_records` yields straight through it. Rows
    are streamed off the cursor (``yield_per``) so peak resident records are
    one table's matched rows, never the subject's whole local footprint.
    """
    for entry in data_map.tables:
        if not entry.columns:
            continue
        statement = _statement_for(entry, graph, metadata, subject_id)
        for row in session.execute(statement).yield_per(_ROW_STREAM_SIZE).mappings():
            yield from _row_records(entry, dict(row))


def _statement_for(
    entry: TableEntry,
    graph: SubjectGraph,
    metadata: MetaData,
    subject_id: SubjectIdentifier,
) -> Select[Any]:
    """One SELECT of the entry's annotated columns for one subject.

    The rows-belong-to-this-subject filter is the shared
    :func:`~effaced.adapters.sqlalchemy.scoping.subject_scope` predicate —
    the same composite-key-aware hop-chain matching the erasure executor and
    verifier use, never a fork (ADR 0025).
    """
    table = metadata.tables[entry.name]
    selected = tuple(table.c[column.name] for column in entry.columns)
    predicate = subject_scope(metadata, graph, entry.name, subject_id)
    return table.select().with_only_columns(*selected).where(predicate)


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
