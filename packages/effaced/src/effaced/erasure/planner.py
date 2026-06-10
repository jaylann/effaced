"""The :class:`ErasurePlanner` — Art. 17 as a saga, not a function call."""

from __future__ import annotations

from typing import TYPE_CHECKING

from effaced.categories import ErasureStrategy
from effaced.erasure.plan import ErasurePlan, ErasureStep
from effaced.erasure.result import ErasureResult
from effaced.exceptions import ManifestError, RetentionViolationError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from effaced.annotations import SubjectRef
    from effaced.manifest import DataMap, SubjectGraph, TableEntry
    from effaced.resolvers import ResolverRegistry


class ErasurePlanner:
    """Plans and executes subject erasure across local and external data.

    The local deletion runs in one atomic transaction in FK-safe order,
    honouring per-field strategies (delete / anonymize / retain). External
    calls cannot join that transaction, so they are enqueued durably in the
    same transaction and fanned out afterwards by the saga runner — the
    system is always in a known, recorded state, even on partial failure.

    The row-level semantics (when a whole row is deleted versus anonymized
    in place) are defined in ADR 0007; changing them changes what gets
    deleted and is MAJOR under widened SemVer.
    """

    def __init__(
        self,
        data_map: DataMap,
        graph: SubjectGraph,
        registry: ResolverRegistry | None = None,
    ) -> None:
        """Wire the planner to a manifest, its resolved graph, and resolvers.

        Args:
            data_map: The application's data map (column strategies).
            graph: The resolved subject graph for the same manifest (see
                :func:`~effaced.adapters.sqlalchemy.resolve_subject_graph`)
                — provides FK-safe ordering and per-table reachability.
            registry: Resolvers for external systems; ``None`` erases the
                local database only.

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

        Args:
            session: An open database session; the local phase commits or
                rolls back as one unit together with the outbox entries.
            subject_id: Identifier on the subject table.
            refs: External-system references for resolver steps.

        Returns:
            The local-phase outcome; external outcomes land in the audit
            trail asynchronously.
        """
        raise NotImplementedError


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
