"""The :class:`RectificationExecutor` — local rectification steps as scoped UPDATE statements."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import CursorResult

from effaced.adapters.sqlalchemy.scoping import lookup_table, subject_scope
from effaced.exceptions import ManifestError

if TYPE_CHECKING:
    from sqlalchemy import MetaData
    from sqlalchemy.orm import Session

    from effaced.manifest import SubjectGraph
    from effaced.rectification.step import RectificationStep


class RectificationExecutor:
    """Executes one local rectification step per call, scoped to one subject.

    The SQLAlchemy implementation of
    :class:`~effaced.rectification.RectificationStepExecutor`: one
    ``UPDATE`` per step, scoped through the same hop-chain predicate the
    erasure executor uses, writing the single corrected value into every
    one of the step's columns. Statements run in the caller's session and
    are never committed here (ADR 0006).

    One shared value per cell is correct here — the correction *is* one
    value, unlike anonymization's per-row surrogates — and category-keyed
    writes are deliberately blunt (ADR 0013): a step cannot fix one row but
    not another. A unique-constraint collision (two matched rows forced to
    the same corrected value) surfaces as the database's own error and is
    audited as a step failure by the caller.
    """

    def __init__(self, metadata: MetaData) -> None:
        """Wire the executor to the application's schema.

        Args:
            metadata: The ``MetaData`` holding the manifest's tables — the
                same one the data map was collected from.
        """
        self._metadata = metadata

    def execute(
        self,
        session: Session,
        graph: SubjectGraph,
        step: RectificationStep,
        subject_id: str,
        value: str | int | float | bool,
    ) -> int:
        """Run one local step scoped to one subject.

        Args:
            session: The caller's open session; never committed here.
            graph: Resolved hop chains from each table to the subject.
            step: The value-free local step to run.
            subject_id: Identifier on the subject table, coerced to the
                subject column's python type for typed-parameter drivers.
            value: The corrected value, written into every step column.

        Returns:
            The number of rows the UPDATE matched.

        Raises:
            ManifestError: If the step targets a table or column missing
                from the bound metadata.
        """
        table = lookup_table(self._metadata, step.target)
        missing = [name for name in step.columns if name not in table.c]
        if missing:
            msg = (
                f"the plan references column(s) {missing!r}, which are not on table {table.name!r}"
            )
            raise ManifestError(msg)
        predicate = subject_scope(self._metadata, graph, step.target, subject_id)
        statement = table.update().where(predicate).values(dict.fromkeys(step.columns, value))
        result = cast(CursorResult[Any], session.execute(statement))
        return result.rowcount
