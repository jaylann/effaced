"""The :class:`ErasurePlanner` — Art. 17 as a saga, not a function call."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from effaced.audit.event import AuditEvent
from effaced.audit.event_type import AuditEventType
from effaced.categories import ErasureStrategy
from effaced.erasure.plan import ErasurePlan, ErasureStep
from effaced.erasure.result import ErasureResult
from effaced.exceptions import ConfigurationError, ManifestError, RetentionViolationError
from effaced.saga.outbox_entry import OutboxEntry

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from effaced.annotations import SubjectRef
    from effaced.audit.sink import AuditSink
    from effaced.erasure.step_executor import StepExecutor
    from effaced.manifest import DataMap, SubjectGraph, TableEntry
    from effaced.resolvers import ResolverRegistry
    from effaced.saga.outbox import Outbox


class ErasurePlanner:
    """Plans and executes subject erasure across local and external data.

    The local deletion runs in one atomic transaction in FK-safe order,
    honouring per-field strategies (delete / anonymize / retain). External
    calls cannot join that transaction, so they are enqueued durably in the
    same transaction and fanned out afterwards by the saga runner — the
    system is always in a known, recorded state, even on partial failure.

    The row-level semantics (when a whole row is deleted versus anonymized
    in place) are defined in ADR 0007; the execution and audit semantics
    of :meth:`erase_subject` in ADR 0008. Changing either changes what
    gets deleted and is MAJOR under widened SemVer.
    """

    def __init__(
        self,
        data_map: DataMap,
        graph: SubjectGraph,
        registry: ResolverRegistry | None = None,
        *,
        executor: StepExecutor | None = None,
        outbox: Outbox | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        """Wire the planner to a manifest, its resolved graph, and resolvers.

        :meth:`plan` needs only the first three; :meth:`erase_subject`
        additionally requires the executor, outbox, and audit sink and
        refuses loudly without them.

        Args:
            data_map: The application's data map (column strategies).
            graph: The resolved subject graph for the same manifest (see
                :func:`~effaced.adapters.sqlalchemy.resolve_subject_graph`)
                — provides FK-safe ordering and per-table reachability.
            registry: Resolvers for external systems; ``None`` erases the
                local database only.
            executor: Runs local steps (see
                :class:`~effaced.adapters.sqlalchemy.ErasureExecutor`).
            outbox: Durable queue for external steps; entries enqueue in
                the caller's erasure transaction.
            audit_sink: Receives every erasure outcome, including
                failures.

        Raises:
            ManifestError: If the data map and the graph do not describe
                the same set of tables.
        """
        declared = {entry.name for entry in data_map.tables}
        resolved = set(graph.deletion_order)
        if declared != resolved:
            msg = (
                f"data map and subject graph disagree: tables only in the data "
                f"map {sorted(declared - resolved)!r}, only in the graph "
                f"{sorted(resolved - declared)!r}"
            )
            raise ManifestError(msg)
        self._data_map = data_map
        self._graph = graph
        self._registry = registry
        self._executor = executor
        self._outbox = outbox
        self._audit_sink = audit_sink

    def plan(self, subject_id: str, *, refs: tuple[SubjectRef, ...] = ()) -> ErasurePlan:
        """Compute the erasure programme without executing anything.

        A pure function of the manifest and ``refs``: no session, no I/O,
        and calling it twice yields equal plans.

        Args:
            subject_id: Identifier on the subject table.
            refs: External-system references, recorded on the plan for the
                resolver steps.

        Returns:
            The ordered, inspectable plan (local steps first, FK-safe).

        Raises:
            RetentionViolationError: If a table must keep rows under a
                retention duty while a table on its path to the subject is
                planned for row deletion.
            ManifestError: If a table survives erasure only because the
                manifest declares nothing erasable on it, while a table on
                its path to the subject is planned for row deletion.
        """
        steps = _local_steps(self._data_map, self._graph) + _external_steps(self._registry)
        return ErasurePlan(subject_id=subject_id, steps=steps, refs=refs)

    def erase_subject(
        self,
        session: Session,
        subject_id: str,
        *,
        refs: tuple[SubjectRef, ...] = (),
    ) -> ErasureResult:
        """Execute the plan: atomic local phase + durable external enqueue.

        Local steps run in FK-safe order and the external steps' outbox
        entries are written through the same session, so the caller's
        commit makes the whole erasure durable at once — and a rollback
        undoes every row change *and* every outbox entry together. This
        method never commits or rolls back the session itself; after it
        raises, do not commit the session.

        Audit semantics (ADR 0008): ``ERASURE_REQUESTED`` is appended
        before the first step, one ``ERASURE_STEP_SUCCEEDED`` after each
        local step (``RETAIN`` included — the retention decision is the
        record), ``ERASURE_STEP_FAILED`` on the first failure (then the
        original exception re-raises), and ``ERASURE_LOCAL_COMPLETED``
        last. With the default :class:`~effaced.DatabaseAuditSink` each
        event commits independently of the caller's transaction, so the
        attempt stays recorded even when the erasure rolls back.

        Re-running for an already-erased subject is a no-op success:
        row-deleting tables report zero, surviving rows (anonymized in
        place or retained) re-match by subject id and are reported again,
        and external work is re-enqueued under fresh idempotency keys —
        resolvers treat "already gone" as success, so duplicates converge.

        Args:
            session: An open database session; the local phase commits or
                rolls back as one unit together with the outbox entries.
            subject_id: Identifier on the subject table.
            refs: External-system references; every registered resolver
                receives an outbox entry per ref.

        Returns:
            The local-phase outcome with per-table counts. A surviving row
            anonymized in some columns and retained in others counts in
            both ``anonymized`` and ``retained``. External outcomes land
            in the audit trail asynchronously.

        Raises:
            ConfigurationError: If the planner was built without an
                executor, outbox, or audit sink — or if the plan contains
                external steps but ``refs`` is empty, which would silently
                skip declared external PII.
        """
        executor, outbox, sink = self._require_wiring()
        plan = self.plan(subject_id, refs=refs)
        if plan.external_steps and not plan.refs:
            names = ", ".join(step.target for step in plan.external_steps)
            msg = (
                f"resolvers are registered ({names}) but no subject refs were "
                f"given; erasing without them would silently skip external data"
            )
            raise ConfigurationError(msg)
        sink.append(
            _event(
                AuditEventType.ERASURE_REQUESTED,
                subject_id,
                {
                    "local_steps": len(plan.local_steps),
                    "external_steps": len(plan.external_steps),
                    "refs": len(plan.refs),
                },
            )
        )
        counts = self._run_local_steps(session, executor, plan, sink)
        entries = _outbox_entries(plan)
        self._enqueue(session, outbox, entries, subject_id, sink)
        sink.append(
            _event(
                AuditEventType.ERASURE_LOCAL_COMPLETED,
                subject_id,
                {
                    "deleted": sum(counts[ErasureStrategy.DELETE].values()),
                    "anonymized": sum(counts[ErasureStrategy.ANONYMIZE].values()),
                    "retained": sum(counts[ErasureStrategy.RETAIN].values()),
                    "enqueued": len(entries),
                },
            )
        )
        return ErasureResult(
            subject_id=subject_id,
            completed_at=datetime.now(UTC),
            deleted=counts[ErasureStrategy.DELETE],
            anonymized=counts[ErasureStrategy.ANONYMIZE],
            retained=counts[ErasureStrategy.RETAIN],
            enqueued_external=tuple(step.target for step in plan.external_steps),
        )

    def _require_wiring(self) -> tuple[StepExecutor, Outbox, AuditSink]:
        """The execution collaborators, or a loud refusal naming the gaps."""
        executor, outbox, sink = self._executor, self._outbox, self._audit_sink
        if executor is None or outbox is None or sink is None:
            missing = ", ".join(
                name
                for name, piece in (
                    ("executor", executor),
                    ("outbox", outbox),
                    ("audit_sink", sink),
                )
                if piece is None
            )
            msg = f"erase_subject needs a planner wired with: {missing}"
            raise ConfigurationError(msg)
        return executor, outbox, sink

    def _run_local_steps(
        self,
        session: Session,
        executor: StepExecutor,
        plan: ErasurePlan,
        sink: AuditSink,
    ) -> dict[ErasureStrategy, dict[str, int]]:
        """Run every local step, auditing each outcome; counts by strategy."""
        counts: dict[ErasureStrategy, dict[str, int]] = {
            strategy: {} for strategy in ErasureStrategy
        }
        for step in plan.local_steps:
            try:
                rows = executor.execute(session, self._graph, step, plan.subject_id)
            except Exception as exc:
                sink.append(_failure(plan.subject_id, step.target, step.strategy.value, exc))
                raise
            counts[step.strategy][step.target] = rows
            sink.append(
                _event(
                    AuditEventType.ERASURE_STEP_SUCCEEDED,
                    plan.subject_id,
                    {"target": step.target, "strategy": step.strategy.value, "rows": rows},
                )
            )
        return counts

    def _enqueue(
        self,
        session: Session,
        outbox: Outbox,
        entries: Sequence[OutboxEntry],
        subject_id: str,
        sink: AuditSink,
    ) -> None:
        """Enqueue external work in the caller's transaction, auditing failure."""
        try:
            outbox.enqueue(session, entries)
        except Exception as exc:
            sink.append(_failure(subject_id, "outbox", "enqueue", exc))
            raise


def _event(
    event_type: AuditEventType,
    subject_id: str,
    payload: dict[str, str | int | bool],
) -> AuditEvent:
    """One audit event for this erasure, stamped now (UTC)."""
    return AuditEvent(
        event_id=uuid4(),
        event_type=event_type,
        subject_ref=subject_id,
        occurred_at=datetime.now(UTC),
        payload=payload,
    )


def _failure(subject_id: str, target: str, strategy: str, exc: Exception) -> AuditEvent:
    """The step-failed event.

    Carries the exception class only, never its message — database errors
    embed row values, and the trail must stay PII-free.
    """
    return _event(
        AuditEventType.ERASURE_STEP_FAILED,
        subject_id,
        {"target": target, "strategy": strategy, "error": type(exc).__name__},
    )


def _outbox_entries(plan: ErasurePlan) -> tuple[OutboxEntry, ...]:
    """One pending entry per (external step, ref) pair, fresh idempotency keys.

    Every resolver receives every ref: a ref's ``kind`` is not a resolver
    name, so any routing heuristic would risk silently skipping external
    erasure. Resolvers are idempotent — over-asking converges, under-asking
    is unrecoverable. Selective routing can be added additively later.
    """
    now = datetime.now(UTC)
    return tuple(
        OutboxEntry(entry_id=uuid4(), resolver=step.target, ref=ref, enqueued_at=now)
        for step in plan.external_steps
        for ref in plan.refs
    )


def _local_steps(data_map: DataMap, graph: SubjectGraph) -> tuple[ErasureStep, ...]:
    """Per-table steps in FK-safe deletion order."""
    deleted = frozenset(entry.name for entry in data_map.tables if _row_deleted(entry, graph))
    _check_conflicts(data_map, graph, deleted)
    steps: list[ErasureStep] = []
    for name in graph.deletion_order:
        steps.extend(_table_steps(data_map.table(name), row_deleted=name in deleted))
    return tuple(steps)


def _row_deleted(entry: TableEntry, graph: SubjectGraph) -> bool:
    """Whole-row deletion: fully PII-owned and every annotated column DELETE."""
    return graph.access(entry.name).fully_pii_owned and all(
        column.spec.erasure is ErasureStrategy.DELETE for column in entry.columns
    )


def _table_steps(entry: TableEntry, *, row_deleted: bool) -> tuple[ErasureStep, ...]:
    """One table's steps: a row delete, or column-level anonymize + retain."""
    if row_deleted:
        return (ErasureStep(target=entry.name, strategy=ErasureStrategy.DELETE),)
    anonymize = tuple(
        column.name for column in entry.columns if column.spec.erasure is not ErasureStrategy.RETAIN
    )
    retain = tuple(
        column.name for column in entry.columns if column.spec.erasure is ErasureStrategy.RETAIN
    )
    steps: list[ErasureStep] = []
    if anonymize:
        steps.append(
            ErasureStep(target=entry.name, strategy=ErasureStrategy.ANONYMIZE, columns=anonymize)
        )
    if retain:
        steps.append(
            ErasureStep(target=entry.name, strategy=ErasureStrategy.RETAIN, columns=retain)
        )
    return tuple(steps)


def _check_conflicts(data_map: DataMap, graph: SubjectGraph, deleted: frozenset[str]) -> None:
    """Reject plans where surviving rows depend on a row-deleted table.

    Detection walks subject hop chains only (ADR 0007): an FK reference to
    a row-deleted table *outside* the survivor's subject path is not visible
    here and surfaces at execution time as a database integrity error.
    """
    for entry in data_map.tables:
        if entry.name in deleted:
            continue
        chain = tuple(hop.target_table for hop in graph.access(entry.name).hops)
        ancestor = next((table for table in chain if table in deleted), None)
        if ancestor is None:
            continue
        retained = next(
            (c for c in entry.columns if c.spec.erasure is ErasureStrategy.RETAIN), None
        )
        if retained is not None:
            reason = retained.spec.retention.reason if retained.spec.retention else "unspecified"
            msg = (
                f"table {entry.name!r} must keep its rows (column "
                f"{retained.name!r} is retained under {reason!r}) but its "
                f"subject path passes through {ancestor!r}, which is planned "
                f"for row deletion — deleting {ancestor!r} would orphan the "
                f"retained rows"
            )
            raise RetentionViolationError(msg)
        msg = (
            f"table {entry.name!r} survives erasure (it is not fully PII-owned "
            f"or declares nothing erasable) but its subject path passes through "
            f"{ancestor!r}, which is planned for row deletion; annotate the "
            f"remaining columns or keep {ancestor!r}'s rows"
        )
        raise ManifestError(msg)


def _external_steps(registry: ResolverRegistry | None) -> tuple[ErasureStep, ...]:
    """One whole-subject deletion step per registered resolver."""
    if registry is None:
        return ()
    return tuple(
        ErasureStep(target=resolver.name, strategy=ErasureStrategy.DELETE, external=True)
        for resolver in registry.all()
    )
