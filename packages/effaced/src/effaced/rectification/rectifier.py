"""The :class:`Rectifier` — Art. 16 as a saga, mirroring the eraser's shape."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from effaced.audit.event import AuditEvent
from effaced.audit.event_type import AuditEventType
from effaced.exceptions import ConfigurationError, ManifestError, ResolverError
from effaced.rectification.result import RectificationResult
from effaced.rectification.step import RectificationStep
from effaced.resolvers import RectifyingResolver
from effaced.saga.outbox_entry import OutboxEntry
from effaced.saga.outbox_operation import OutboxOperation

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from effaced.annotations import Correction, SubjectRef
    from effaced.audit.sink import AuditSink
    from effaced.categories import PiiCategory
    from effaced.manifest import DataMap, SubjectGraph
    from effaced.rectification.step_executor import RectificationStepExecutor
    from effaced.resolvers import ResolverRegistry
    from effaced.saga.outbox import Outbox


class Rectifier:
    """Applies category-keyed corrections across local and external data.

    The local writes run in the caller's transaction; external corrections
    cannot join it, so they are enqueued durably in the same transaction
    and fanned out afterwards by the saga runner — the half-rectified
    state gets the same cure as the half-erased one (ADR 0013).

    Erasure strategy never gates rectification: ``RETAIN`` and
    ``ANONYMIZE`` columns of a corrected category are rewritten too — an
    inaccurate record retained under a legal duty is the worst of both
    worlds, and Art. 16 does not defer to Art. 17 annotations. Changing
    any of these semantics changes what gets written and is MAJOR under
    widened SemVer.
    """

    def __init__(
        self,
        data_map: DataMap,
        graph: SubjectGraph,
        registry: ResolverRegistry | None = None,
        *,
        executor: RectificationStepExecutor | None = None,
        outbox: Outbox | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        """Wire the rectifier to a manifest, its resolved graph, and resolvers.

        Args:
            data_map: The application's data map (category annotations).
            graph: The resolved subject graph for the same manifest (see
                :func:`~effaced.adapters.sqlalchemy.resolve_subject_graph`).
            registry: Resolvers for external systems; ``None`` rectifies
                the local database only. Registered resolvers without the
                :class:`~effaced.RectifyingResolver` capability are
                skipped and recorded, never an error.
            executor: Runs local steps (see
                :class:`~effaced.adapters.sqlalchemy.RectificationExecutor`).
            outbox: Durable queue for external steps; entries enqueue in
                the caller's rectification transaction.
            audit_sink: Receives every rectification outcome, including
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

    def rectify_subject(
        self,
        session: Session,
        subject_id: str,
        corrections: tuple[Correction, ...],
        *,
        refs: tuple[SubjectRef, ...] = (),
    ) -> RectificationResult:
        """Apply the corrections: atomic local phase + durable external enqueue.

        Every annotated column whose category matches a correction —
        reachable from the subject through the graph's hop chains — is
        updated to the corrected value, regardless of its erasure
        strategy. A category matching no local column is a complete
        answer, not an error. Local writes and the external entries'
        outbox rows go through the same session, so the caller's commit
        makes the whole rectification durable at once — and a rollback
        undoes every row change *and* every outbox entry together. This
        method never commits or rolls back the session itself; after it
        raises, do not commit the session.

        Audit semantics (ADR 0013): ``RECTIFICATION_REQUESTED`` is
        appended before the first step, one
        ``RECTIFICATION_STEP_SUCCEEDED`` after each local step,
        ``RECTIFICATION_STEP_FAILED`` on the first failure (then the
        original exception re-raises), and
        ``RECTIFICATION_LOCAL_COMPLETED`` last. Payloads carry table,
        category, and resolver names plus counts — **old and new values
        never appear in any event**. Validation failures raise before any
        event.

        Each ref is routed to the resolver whose ``name`` equals the
        ref's ``kind`` (ADR 0008). A registered resolver with no matching
        ref — or without the ``rectify_subject`` capability — is skipped
        and recorded in ``skipped_resolvers``; a ref kind matching no
        resolver fails loudly. Enqueued entries carry the corrections in
        the outbox row's payload (real PII, cleared at terminal status).

        Args:
            session: An open database session; the local phase commits or
                rolls back as one unit together with the outbox entries.
            subject_id: Identifier on the subject table.
            corrections: One corrected value per category; duplicates are
                rejected.
            refs: External-system references, routed by kind (ADR 0008).

        Returns:
            The local-phase outcome with per-table row counts. External
            outcomes land in the audit trail asynchronously.

        Raises:
            ConfigurationError: If the rectifier was built without an
                executor, outbox, or audit sink.
            ValueError: If ``corrections`` is empty or repeats a category.
            ResolverError: If a ref's ``kind`` matches no registered
                resolver — a typo must not silently drop an external
                system from the rectification.
        """
        executor, outbox, sink = self._require_wiring()
        _validate(corrections)
        steps = _local_steps(self._data_map, self._graph, corrections)
        entries = _outbox_entries(self._registry, subject_id, corrections, refs)
        sink.append(
            _event(
                AuditEventType.RECTIFICATION_REQUESTED,
                subject_id,
                {
                    "categories": ",".join(
                        sorted(correction.category.value for correction in corrections)
                    ),
                    "local_steps": len(steps),
                    "external_steps": len(entries),
                },
            )
        )
        values: dict[PiiCategory, str | int | float | bool] = {
            correction.category: correction.value for correction in corrections
        }
        rectified = self._run_local_steps(session, executor, steps, subject_id, values, sink)
        self._enqueue(session, outbox, entries, subject_id, sink)
        enqueued = tuple(dict.fromkeys(entry.resolver for entry in entries))
        registered = tuple(r.name for r in self._registry.all()) if self._registry else ()
        skipped = tuple(name for name in registered if name not in enqueued)
        sink.append(
            _event(
                AuditEventType.RECTIFICATION_LOCAL_COMPLETED,
                subject_id,
                {
                    "rectified": sum(rectified.values()),
                    "enqueued": len(entries),
                    "skipped_resolvers": ",".join(skipped),
                },
            )
        )
        return RectificationResult(
            subject_id=subject_id,
            completed_at=datetime.now(UTC),
            rectified=rectified,
            enqueued_external=enqueued,
            skipped_resolvers=skipped,
        )

    def _require_wiring(self) -> tuple[RectificationStepExecutor, Outbox, AuditSink]:
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
            msg = f"rectify_subject needs a rectifier wired with: {missing}"
            raise ConfigurationError(msg)
        return executor, outbox, sink

    def _run_local_steps(
        self,
        session: Session,
        executor: RectificationStepExecutor,
        steps: Sequence[RectificationStep],
        subject_id: str,
        values: dict[PiiCategory, str | int | float | bool],
        sink: AuditSink,
    ) -> dict[str, int]:
        """Run every local step, auditing each outcome; row counts by table."""
        rectified: dict[str, int] = {}
        for step in steps:
            try:
                rows = executor.execute(
                    session, self._graph, step, subject_id, values[step.category]
                )
                sink.append(
                    _event(
                        AuditEventType.RECTIFICATION_STEP_SUCCEEDED,
                        subject_id,
                        {"target": step.target, "category": step.category.value, "rows": rows},
                    )
                )
            except Exception as exc:
                sink.append(_failure(subject_id, step.target, exc))
                raise
            rectified[step.target] = rectified.get(step.target, 0) + rows
        return rectified

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
            sink.append(_failure(subject_id, "outbox", exc))
            raise


def _validate(corrections: tuple[Correction, ...]) -> None:
    """Reject empty or category-duplicated correction sets before any event."""
    if not corrections:
        msg = "rectify_subject needs at least one correction"
        raise ValueError(msg)
    categories = [correction.category for correction in corrections]
    duplicates = sorted(
        {category.value for category in categories if categories.count(category) > 1}
    )
    if duplicates:
        msg = (
            f"corrections repeat the categories {', '.join(duplicates)} — "
            f"one corrected value per category"
        )
        raise ValueError(msg)


def _local_steps(
    data_map: DataMap, graph: SubjectGraph, corrections: tuple[Correction, ...]
) -> tuple[RectificationStep, ...]:
    """Per-table steps in graph order: one per (table, matched category).

    Erasure strategy is deliberately not consulted — ``RETAIN`` and
    ``ANONYMIZE`` columns of the category are rectified too (ADR 0013).
    """
    steps: list[RectificationStep] = []
    for name in graph.deletion_order:
        entry = data_map.table(name)
        for correction in corrections:
            columns = tuple(
                column.name
                for column in entry.columns
                if column.spec.category is correction.category
            )
            if columns:
                steps.append(
                    RectificationStep(target=name, category=correction.category, columns=columns)
                )
    return tuple(steps)


def _outbox_entries(
    registry: ResolverRegistry | None,
    subject_id: str,
    corrections: tuple[Correction, ...],
    refs: tuple[SubjectRef, ...],
) -> tuple[OutboxEntry, ...]:
    """One pending rectify entry per matched (rectifying resolver, ref) pair.

    Routing follows ADR 0008: a ref goes to the resolver whose name equals
    the ref's kind. A kind matching no registered resolver fails loudly — a
    typo'd kind must never silently drop an external system. A matched
    resolver without ``rectify_subject`` produces no entry; the caller
    records it in ``skipped_resolvers`` (capability absence is an honest
    answer, never an error — ADR 0013).

    Raises:
        ResolverError: If a ref's ``kind`` matches no registered resolver.
    """
    resolvers = registry.all() if registry is not None else ()
    names = {resolver.name for resolver in resolvers}
    unmatched = sorted({ref.kind for ref in refs} - names)
    if unmatched:
        msg = (
            f"no resolver registered for ref kind(s) {unmatched!r}; refs are "
            f"routed to the resolver whose name equals the ref's kind"
        )
        raise ResolverError(msg)
    rectifying = {
        resolver.name for resolver in resolvers if isinstance(resolver, RectifyingResolver)
    }
    now = datetime.now(UTC)
    return tuple(
        OutboxEntry(
            entry_id=uuid4(),
            subject_id=subject_id,
            resolver=ref.kind,
            ref=ref,
            operation=OutboxOperation.RECTIFY,
            corrections=corrections,
            enqueued_at=now,
        )
        for ref in refs
        if ref.kind in rectifying
    )


def _event(
    event_type: AuditEventType,
    subject_id: str,
    payload: dict[str, str | int | bool],
) -> AuditEvent:
    """One audit event for this rectification, stamped now (UTC)."""
    return AuditEvent(
        event_id=uuid4(),
        event_type=event_type,
        subject_ref=subject_id,
        occurred_at=datetime.now(UTC),
        payload=payload,
    )


def _failure(subject_id: str, target: str, exc: Exception) -> AuditEvent:
    """The step-failed event.

    Carries the exception class only, never its message — database errors
    embed row values, and the trail must stay PII-free.
    """
    return _event(
        AuditEventType.RECTIFICATION_STEP_FAILED,
        subject_id,
        {"target": target, "error": type(exc).__name__},
    )
