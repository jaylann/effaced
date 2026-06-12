"""The :class:`ErasureVerifier` — a read-only post-erasure read-back."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import func, select

from effaced.adapters.sqlalchemy.scoping import lookup_table, subject_scope
from effaced.audit.event import AuditEvent
from effaced.audit.event_type import AuditEventType
from effaced.categories import ErasureStrategy
from effaced.erasure.planner import ErasurePlanner
from effaced.erasure.verification import ErasureVerification

if TYPE_CHECKING:
    from sqlalchemy import MetaData
    from sqlalchemy.orm import Session

    from effaced.audit.sink import AuditSink
    from effaced.erasure.plan import ErasureStep
    from effaced.manifest import DataMap, SubjectGraph


class ErasureVerifier:
    """Reads the annotated surface back after an erasure and records the verdict.

    The verifier re-derives the plan's table classification — the ADR 0007
    row-delete versus anonymize/retain split — by calling
    :class:`~effaced.ErasurePlanner` internally and reading its local steps,
    never re-implementing the classification. It then counts, per table, the
    rows still scoped to the subject through the same hop-chain predicate the
    executor used (the shared ``scoping.subject_scope`` helper), issuing
    nothing but ``SELECT COUNT`` statements — it is strictly
    read-only and mutates no row.

    The verdict proves **execution fidelity**, not erasure completeness, and
    the boundaries are stated on :class:`~effaced.ErasureVerification`:

    1. it re-reads the *same annotated surface* the plan was built from, so
       un-annotated PII is invisible by construction;
    2. a row orphaned off the subject's path is unreachable by the scoping
       predicate and invisible here too;
    3. anonymized cell *values* are not verified — surrogates are random,
       never NULL, so without a before-state they are indistinguishable from
       originals; that check needs a before-state and is out of scope.

    ``verified`` is therefore the narrow claim that every row-deleted table
    holds zero subject-scoped rows; surviving (anonymize/retain) counts are
    reported for the record and never flip the verdict. This is never a
    determination that the subject is fully erased or that the controller is
    compliant.
    """

    def __init__(
        self,
        data_map: DataMap,
        graph: SubjectGraph,
        metadata: MetaData,
        *,
        audit_sink: AuditSink,
    ) -> None:
        """Wire the verifier to a manifest, its resolved graph, and a sink.

        Args:
            data_map: The application's data map — the same one the erasure
                was planned from; its classification is re-derived here.
            graph: The resolved subject graph for that manifest (see
                :func:`~effaced.adapters.sqlalchemy.resolve_subject_graph`)
                — provides each table's hop chain to the subject.
            metadata: The schema metadata holding the mapped tables; rows are
                counted through its table handles.
            audit_sink: Receives exactly one event per verification —
                ``ERASURE_VERIFIED`` when clean, ``ERASURE_VERIFICATION_FAILED``
                when any row-deleted table still holds subject-scoped rows.
        """
        self._planner = ErasurePlanner(data_map, graph)
        self._graph = graph
        self._metadata = metadata
        self._audit_sink = audit_sink

    def verify_subject_erased(self, session: Session, subject_id: str) -> ErasureVerification:
        """Read the subject's annotated surface back and record the verdict.

        Re-derives the plan's table classification, counts the subject's
        surviving rows per table with ``SELECT COUNT`` statements only, and
        appends one audit event. Counting is strictly read-only; the caller's
        session is never written to or committed here.

        Args:
            session: An open database session; used for reads only.
            subject_id: Identifier on the subject table, coerced to the
                subject column's python type for typed-parameter drivers.

        Returns:
            The verdict: ``verified`` is true iff every row-deleted table is
            empty for this subject; ``residual`` and ``surviving`` carry the
            per-table counts (see :class:`~effaced.ErasureVerification`).

        Raises:
            ManifestError: If a table or hop references a name missing from
                the bound metadata.
            SubjectResolutionError: If the id cannot carry the subject
                column's type.
        """
        plan = self._planner.plan(subject_id)
        residual = {
            step.target: self._count(session, step.target, subject_id)
            for step in plan.local_steps
            if step.strategy is ErasureStrategy.DELETE
        }
        surviving = {
            target: self._count(session, target, subject_id)
            for target in _surviving_targets(plan.local_steps)
        }
        verified = all(count == 0 for count in residual.values())
        verification = ErasureVerification(
            subject_id=subject_id,
            verified_at=datetime.now(UTC),
            verified=verified,
            residual=residual,
            surviving=surviving,
        )
        self._audit_sink.append(_event(verification))
        return verification

    def _count(self, session: Session, table_name: str, subject_id: str) -> int:
        """Count one table's subject-scoped rows without touching them."""
        table = lookup_table(self._metadata, table_name)
        predicate = subject_scope(self._metadata, self._graph, table_name, subject_id)
        counted = session.execute(select(func.count()).select_from(table).where(predicate))
        return int(counted.scalar_one())


def _surviving_targets(local_steps: tuple[ErasureStep, ...]) -> tuple[str, ...]:
    """Tables the plan keeps (anonymize/retain), de-duplicated, in plan order.

    A table both anonymized and retained yields two local steps; it is
    counted once.
    """
    surviving = tuple(
        step.target for step in local_steps if step.strategy is not ErasureStrategy.DELETE
    )
    return tuple(dict.fromkeys(surviving))


def _event(verification: ErasureVerification) -> AuditEvent:
    """One PII-free audit event for this verification.

    Carries scalar counts and the row-deleted table names that still held
    rows (table names are established non-PII references in step events);
    ``failed_tables`` is empty when the verification passed.
    """
    failed = ",".join(name for name, count in verification.residual.items() if count > 0)
    event_type = (
        AuditEventType.ERASURE_VERIFIED
        if verification.verified
        else AuditEventType.ERASURE_VERIFICATION_FAILED
    )
    return AuditEvent(
        event_id=uuid4(),
        event_type=event_type,
        subject_ref=verification.subject_id,
        occurred_at=verification.verified_at,
        payload={
            "tables_checked": len(verification.residual) + len(verification.surviving),
            "residual_rows": sum(verification.residual.values()),
            "surviving_rows": sum(verification.surviving.values()),
            "failed_tables": failed,
        },
    )
