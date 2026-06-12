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

    from effaced.erasure.plan import ErasureStep
    from effaced.manifest import SubjectGraph


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
        subject_id: str,
    ) -> int:
        """Run one local step scoped to one subject (see :class:`StepExecutor`).

        Args:
            session: The caller's open session; never committed here.
            graph: Resolved hop chains from each table to the subject.
            step: The local step to run.
            subject_id: Identifier on the subject table, coerced to the
                subject column's python type for typed-parameter drivers.

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
        """Rewrite matched rows one by one with fresh surrogates."""
        key = list(table.primary_key.columns)
        if not key:
            msg = (
                f"table {table.name!r} has no primary key; anonymization "
                f"rewrites rows individually and needs one to address them"
            )
            raise AnonymizationError(msg)
        targets = [_column(table, name) for name in columns]
        rows = session.execute(select(*key).where(predicate)).all()
        for row in rows:
            values = {
                column.name: self._surrogates.surrogate_for(column.type) for column in targets
            }
            matched = table.update().where(
                *(pk == value for pk, value in zip(key, row, strict=True))
            )
            session.execute(matched.values(**values))
        return len(rows)


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
