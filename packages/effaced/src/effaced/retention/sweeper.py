"""The :class:`RetentionSweeper` — Art. 5(1)(e) storage limitation, report-only."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from effaced.audit.event import AuditEvent
from effaced.audit.event_type import AuditEventType
from effaced.exceptions import ManifestError
from effaced.retention.report import RetentionReport, RetentionReportEntry

if TYPE_CHECKING:
    from sqlalchemy import MetaData, Select, Table
    from sqlalchemy.orm import Session
    from sqlalchemy.sql.elements import ColumnElement
    from sqlalchemy.sql.expression import FromClause

    from effaced.audit.sink import AuditSink
    from effaced.manifest import ColumnEntry, DataMap, SubjectGraph, TableAccessPlan


class RetentionSweeper:
    """Finds data whose declared retention window has lapsed — and reports it.

    The sweep is read-only by construction: it builds nothing but SELECT statements,
    writes no rows, and the erasure planner stays time-free — a lapsed
    window changes the report, never any plan. Whether a lapsed duty
    permits erasure is the controller's determination (ADR 0012); acting on
    a lapsed ``RETAIN`` duty means changing the annotation first, because
    erasure retains ``RETAIN`` columns by construction.

    A column participates iff its policy declares a ``duration``; it is
    *sweepable* iff the policy also names an ``anchor``. Durations without
    an anchor — and rows whose anchor is NULL — are reported as
    indeterminate, never guessed.
    """

    def __init__(
        self,
        data_map: DataMap,
        graph: SubjectGraph,
        metadata: MetaData,
        audit_sink: AuditSink,
    ) -> None:
        """Wire the sweeper to a manifest, its resolved graph, and a sink.

        Args:
            data_map: The application's data map (retention policies).
            graph: The resolved subject graph for the same manifest (see
                :func:`~effaced.adapters.sqlalchemy.resolve_subject_graph`)
                — provides each table's path to the subject.
            metadata: The schema metadata holding the mapped tables; the
                sweeper reads rows through its table handles.
            audit_sink: Receives one ``RETENTION_EXPIRED`` event per
                subject with expired rows, per swept column.

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

    def sweep(self, session: Session, *, now: datetime | None = None) -> RetentionReport:
        """Evaluate every bounded retention duty against one instant.

        Per sweepable column, ``cutoff = now - duration`` is computed in
        Python so the database sees a portable ``anchor <= :cutoff``
        comparison; rows are attributed to subjects through the manifest's
        hop chains. One ``RETENTION_EXPIRED`` event is appended per subject
        with expired rows — table/column names and counts only, never
        values or anchor timestamps. Repeated sweeps re-emit for
        still-expired data: each run is evidence.

        Counting fetches the matched rows and counts in Python — O(rows);
        a COUNT pushdown is a future adapter optimization.

        Args:
            session: An open database session; reads only, never writes.
            now: The cutoff instant for the whole run; defaults to the
                current UTC time. Pass it explicitly for determinism.

        Returns:
            The report, one entry per column with a declared ``duration``.
        """
        moment = now if now is not None else datetime.now(UTC)
        entries: list[RetentionReportEntry] = []
        for table_entry in self._data_map.tables:
            table = self._metadata.tables[table_entry.name]
            for column in table_entry.columns:
                entry = self._column_entry(session, table, table_entry.name, column, moment)
                if entry is None:
                    continue
                entries.append(entry)
                self._append_events(entry)
        return RetentionReport(swept_at=moment, entries=tuple(entries))

    def _column_entry(
        self,
        session: Session,
        table: Table,
        table_name: str,
        column: ColumnEntry,
        moment: datetime,
    ) -> RetentionReportEntry | None:
        """One column's findings, or ``None`` when it carries no bounded duty."""
        retention = column.spec.retention
        if retention is None or retention.duration is None:
            return None
        if retention.anchor is None:
            return RetentionReportEntry(
                table=table_name,
                column=column.name,
                reason=retention.reason,
                anchor=None,
                indeterminate_rows=_count(session, _all_rows_statement(table)),
            )
        return self._swept_entry(
            session,
            table,
            column.name,
            reason=retention.reason,
            anchor=retention.anchor,
            cutoff=moment - retention.duration,
        )

    def _swept_entry(
        self,
        session: Session,
        table: Table,
        column_name: str,
        *,
        reason: str,
        anchor: str,
        cutoff: datetime,
    ) -> RetentionReportEntry:
        """Match a sweepable column's rows against the cutoff, per subject."""
        anchor_column = table.c[anchor]
        if not getattr(anchor_column.type, "timezone", False):
            # Naive column: strip the offset so the comparison is portable.
            cutoff = cutoff.replace(tzinfo=None)
        statement = self._attribution_statement(table, anchor_column <= cutoff)
        expired = Counter(str(row[0]) for row in session.execute(statement).all())
        null_statement = (
            table.select().with_only_columns(anchor_column).where(anchor_column.is_(None))
        )
        return RetentionReportEntry(
            table=table.name,
            column=column_name,
            reason=reason,
            anchor=anchor,
            expired=dict(expired),
            indeterminate_rows=_count(session, null_statement),
        )

    def _attribution_statement(
        self,
        table: Table,
        predicate: ColumnElement[bool],
    ) -> Select[Any]:
        """SELECT the owning subject id of every row matching ``predicate``.

        Linked tables walk the hop chain over a fresh alias per hop target
        (the exporter's technique) and select the final alias's subject-id
        column — only subject ids ever leave the database, never values.
        """
        plan: TableAccessPlan = self._graph.access(table.name)
        if plan.is_subject_table:
            return (
                table.select()
                .with_only_columns(table.c[self._graph.subject_id_column])
                .where(predicate)
            )
        aliases = tuple(self._metadata.tables[hop.target_table].alias() for hop in plan.hops)
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
        return (
            table.select()
            .with_only_columns(outer.c[self._graph.subject_id_column])
            .where(*conditions, predicate)
        )

    def _append_events(self, entry: RetentionReportEntry) -> None:
        """One ``RETENTION_EXPIRED`` event per subject with expired rows."""
        for subject_id in sorted(entry.expired):
            self._audit_sink.append(
                AuditEvent(
                    event_id=uuid4(),
                    event_type=AuditEventType.RETENTION_EXPIRED,
                    subject_ref=subject_id,
                    occurred_at=datetime.now(UTC),
                    payload={
                        "table": entry.table,
                        "column": entry.column,
                        "rows": entry.expired[subject_id],
                    },
                )
            )


def _all_rows_statement(table: Table) -> Select[Any]:
    """Primary-key columns of every row — a count probe that fetches no PII."""
    return table.select().with_only_columns(*table.primary_key.columns)


def _count(session: Session, statement: Select[Any]) -> int:
    """Row count by fetching — O(rows); COUNT pushdown is a future optimization."""
    return len(session.execute(statement).all())


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
