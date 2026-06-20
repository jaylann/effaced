"""The :class:`ErasureExecutor` — local erasure steps as SQLAlchemy statements."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import ColumnElement, CursorResult, func, select

from effaced.adapters.sqlalchemy.anonymizer import SurrogateRegistry, default_surrogate_registry
from effaced.adapters.sqlalchemy.scoping import lookup_table, subject_scope
from effaced.categories import ErasureStrategy
from effaced.exceptions import AnonymizationError, ConfigurationError, ManifestError

if TYPE_CHECKING:
    from sqlalchemy import Column, MetaData, Table
    from sqlalchemy.orm import Session

    from effaced.annotations import SubjectIdentifier
    from effaced.erasure.plan import ErasureStep
    from effaced.manifest import SubjectGraph

_BATCH = 256
"""Primary keys fetched per round trip when anonymizing a subject's rows.

Bounds the anonymize step's peak resident keys to one batch: a subject with
a large footprint in one table no longer materializes every matched primary
key before rewriting. Each batch is fetched ordered and offset by the count
already rewritten — the anonymized columns are never the ordering key, so
the matched set stays stable and every row is rewritten exactly once.
"""


class ErasureExecutor:
    """Executes one local erasure step per call, scoped to one subject.

    The SQLAlchemy implementation of
    :class:`~effaced.erasure.StepExecutor`: each table's
    :class:`~effaced.TableAccessPlan` hop chain becomes nested ``IN``
    subqueries down to the subject identifier (shared with the
    rectification executor via the scoping module), so a step only ever
    touches the one subject's rows. Statements run in the caller's session
    and are never committed here (ADR 0006).

    Two ADR 0007 consequences surface at this layer: a foreign-key
    reference into a row-deleted table from *outside* the subject path
    (e.g. another subject's comment replying to this subject's) fails
    loudly with the database's integrity error, and ``ANONYMIZE`` rewrites
    rows one by one so every cell gets a fresh surrogate — unique
    constraints keep holding.
    """

    def __init__(self, metadata: MetaData, surrogates: SurrogateRegistry | None = None) -> None:
        """Wire the executor to the application's schema and surrogates.

        Args:
            metadata: The ``MetaData`` holding the manifest's tables — the
                same one the data map was collected from.
            surrogates: Replacement-value factories for ``ANONYMIZE``
                steps; defaults to
                :func:`~effaced.default_surrogate_registry`.
        """
        self._metadata = metadata
        self._surrogates = surrogates if surrogates is not None else default_surrogate_registry()

    def execute(
        self,
        session: Session,
        graph: SubjectGraph,
        step: ErasureStep,
        subject_id: SubjectIdentifier,
    ) -> int:
        """Run one local step scoped to one subject (see :class:`StepExecutor`).

        Args:
            session: The caller's open session; never committed here.
            graph: Resolved hop chains from each table to the subject.
            step: The local step to run.
            subject_id: The subject identifier — a single-column ``str`` or a
                composite :class:`~effaced.CompositeSubjectId`; each
                component is coerced to its subject column's python type for
                typed-parameter drivers.

        Returns:
            The number of rows deleted, anonymized, or counted as retained.

        Raises:
            ConfigurationError: If the step is external.
            ManifestError: If the step targets a table or column missing
                from the bound metadata.
            AnonymizationError: If an ``ANONYMIZE`` table has no primary
                key or a column type has no registered surrogate.
        """
        if step.external:
            msg = (
                f"step {step.target!r} is external; resolver calls go through "
                f"the outbox, never the local transaction"
            )
            raise ConfigurationError(msg)
        table = lookup_table(self._metadata, step.target)
        predicate = subject_scope(self._metadata, graph, step.target, subject_id)
        if step.strategy is ErasureStrategy.DELETE:
            return _delete(session, table, predicate)
        if step.strategy is ErasureStrategy.ANONYMIZE:
            return self._anonymize(session, table, step.columns, predicate)
        return _count(session, table, predicate)

    def _anonymize(
        self,
        session: Session,
        table: Table,
        columns: tuple[str, ...],
        predicate: ColumnElement[bool],
    ) -> int:
        """Rewrite matched rows with a fresh per-cell surrogate (ADR 0007).

        Each matched row is rewritten with its own fresh surrogate per
        anonymized cell (one scoped UPDATE sharing a surrogate would break a
        unique constraint), so the erased result is byte-identical to fetching
        every key first and rewriting row by row — this is a memory/throughput
        change, never a behaviour change.

        Memory: when the primary key is a single, non-anonymized column the
        matched keys are walked by a stable keyset cursor (``key > last``), one
        ``_BATCH`` at a time, so a large-footprint subject never materializes
        every key — and, unlike an ``OFFSET``/count walk, this stays correct
        even when anonymizing a non-key column shifts which rows match, because
        the cursor advances by the key, not by a running count. When the PK is
        composite, or is itself anonymized (its fresh surrogate would move the
        cursor mid-walk and skip rows — letting PII survive the erasure), every
        matching key is captured in one query up-front instead: correct and
        immune to the in-loop change, at the cost of the prior resident key set.
        """
        key = list(table.primary_key.columns)
        if not key:
            msg = (
                f"table {table.name!r} has no primary key; anonymization "
                f"rewrites rows individually and needs one to address them"
            )
            raise AnonymizationError(msg)
        targets = [_column(table, name) for name in columns]
        if len(key) != 1 or key[0].name in columns:
            rows = session.execute(select(*key).where(predicate)).all()
            for row in rows:
                self._rewrite(session, table, key, targets, tuple(row))
            return len(rows)
        cursor_column = key[0]
        anonymized = 0
        cursor: object | None = None
        while True:
            statement = select(*key).where(predicate)
            if cursor is not None:
                statement = statement.where(cursor_column > cursor)
            batch = session.execute(statement.order_by(cursor_column).limit(_BATCH)).all()
            if not batch:
                return anonymized
            for row in batch:
                self._rewrite(session, table, key, targets, tuple(row))
            anonymized += len(batch)
            cursor = batch[-1][0]

    def _rewrite(
        self,
        session: Session,
        table: Table,
        key: list[Column[Any]],
        targets: list[Column[Any]],
        row: tuple[Any, ...],
    ) -> None:
        """Rewrite one matched row's anonymized cells with fresh surrogates."""
        values = {column.name: self._surrogates.surrogate_for(column.type) for column in targets}
        matched = table.update().where(*(pk == value for pk, value in zip(key, row, strict=True)))
        session.execute(matched.values(**values))


def _delete(session: Session, table: Table, predicate: ColumnElement[bool]) -> int:
    """Delete the matched rows; the database reports how many."""
    result = cast(CursorResult[Any], session.execute(table.delete().where(predicate)))
    return result.rowcount


def _count(session: Session, table: Table, predicate: ColumnElement[bool]) -> int:
    """Count the matched rows without touching them (RETAIN)."""
    counted = session.execute(select(func.count()).select_from(table).where(predicate))
    return int(counted.scalar_one())


def _column(table: Table, name: str) -> Column[Any]:
    """Look one step column up on its table."""
    try:
        return table.c[name]
    except KeyError as exc:
        msg = f"the plan references column {name!r}, which is not on table {table.name!r}"
        raise ManifestError(msg) from exc
