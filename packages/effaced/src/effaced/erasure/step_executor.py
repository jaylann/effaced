"""The :class:`StepExecutor` protocol — runs one local erasure step."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from effaced.erasure.plan import ErasureStep
    from effaced.manifest import SubjectGraph


@runtime_checkable
class StepExecutor(Protocol):
    """Anything that can execute one *local* erasure step for one subject.

    The storage-specific half of :meth:`~effaced.ErasurePlanner.
    erase_subject`: it turns a step plus the subject graph's hop chains
    into subject-scoped statements in the caller's open transaction. The
    SQLAlchemy implementation is
    :class:`~effaced.adapters.sqlalchemy.ErasureExecutor`.
    """

    def execute(
        self,
        session: Session,
        graph: SubjectGraph,
        step: ErasureStep,
        subject_id: str,
    ) -> int:
        """Run one local step scoped to one subject.

        Implementations must never commit or roll back the session — the
        step is durable exactly when the caller's transaction is. ``DELETE``
        removes the matched rows, ``ANONYMIZE`` rewrites the step's columns
        with irreversible surrogates, and ``RETAIN`` touches nothing and
        only counts what stays.

        Args:
            session: The caller's open session.
            graph: Resolved hop chains from each table to the subject.
            step: The local step to run.
            subject_id: Identifier on the subject table.

        Returns:
            The number of rows the step covered (deleted, anonymized, or
            counted as retained).

        Raises:
            ConfigurationError: If the step is external — resolver calls
                never run inside the local transaction.
        """
        ...
