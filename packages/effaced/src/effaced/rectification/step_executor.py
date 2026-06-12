"""The :class:`RectificationStepExecutor` protocol — runs one local rectification step."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from effaced.manifest import SubjectGraph
    from effaced.rectification.step import RectificationStep


@runtime_checkable
class RectificationStepExecutor(Protocol):
    """Anything that can execute one *local* rectification step for one subject.

    The storage-specific half of :meth:`~effaced.Rectifier.
    rectify_subject`: it turns a step plus the subject graph's hop chains
    into one subject-scoped UPDATE in the caller's open transaction. The
    SQLAlchemy implementation is
    :class:`~effaced.adapters.sqlalchemy.RectificationExecutor`.

    The corrected value is passed separately from the step so steps stay
    value-free — a plan never carries PII.
    """

    def execute(
        self,
        session: Session,
        graph: SubjectGraph,
        step: RectificationStep,
        subject_id: str,
        value: str | int | float | bool,
    ) -> int:
        """Run one local step scoped to one subject.

        Implementations must never commit or roll back the session — the
        step is durable exactly when the caller's transaction is. Every
        column the step names receives the same corrected ``value``.

        Args:
            session: The caller's open session.
            graph: Resolved hop chains from each table to the subject.
            step: The value-free local step to run.
            subject_id: Identifier on the subject table.
            value: The corrected value to write.

        Returns:
            The number of rows the step matched.
        """
        ...
