"""The :class:`Replayer` — re-applying committed erasures after a backup restore."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from effaced.audit.event import AuditEvent
from effaced.audit.event_type import AuditEventType
from effaced.replay.plan import ReplayPlan

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sqlalchemy.orm import Session

    from effaced.annotations import SubjectRef
    from effaced.audit.sink import AuditSink
    from effaced.erasure.planner import ErasurePlanner
    from effaced.erasure.result import ErasureResult


class Replayer:
    """Replays the erasures a backup restore resurrected (ADR 0023).

    A restore brings back every subject whose erasure was committed after
    the backup point. The surviving audit trail says exactly which those
    are; :meth:`plan` classifies it and :meth:`replay` re-runs the wired
    :class:`~effaced.ErasurePlanner` per subject — no second erasure
    engine, so ADR 0007/0008/0009 semantics apply verbatim and each
    replayed erasure appends its full audit sequence.

    Replay is a mechanism for converging after a restore, never a
    determination that the restore — or the deployment — is compliant.
    """

    def __init__(
        self,
        planner: ErasurePlanner,
        audit_sink: AuditSink,
        *,
        refs_for: Callable[[str], tuple[SubjectRef, ...]] | None = None,
    ) -> None:
        """Wire the replayer to an execution-ready planner and a sink.

        Args:
            planner: A planner wired for
                :meth:`~effaced.ErasurePlanner.erase_subject` (executor,
                outbox, audit sink).
            audit_sink: Receives one ``ERASURE_REPLAYED`` event per
                replayed subject, before any mutation.
            refs_for: Re-derives a subject's external-system refs from the
                restored data — the trail is PII-free and cannot carry
                them. ``None`` (the default) replays the local database
                only, which is correct when external systems were not
                restored: their erasures stand.
        """
        self._planner = planner
        self._audit_sink = audit_sink
        self._refs_for = refs_for

    def plan(
        self,
        events: Sequence[AuditEvent],
        *,
        backup_taken_at: datetime,
    ) -> ReplayPlan:
        """Classify a surviving trail against the backup point.

        Delegates to :meth:`ReplayPlan.derive <effaced.ReplayPlan.derive>`
        — a pure function; see there for the classification rules.

        Args:
            events: The surviving trail (external sink, replica, or
                pre-restore dump; see :class:`~effaced.ReplaySource`).
            backup_taken_at: When the restored backup was taken
                (timezone-aware; the boundary is inclusive).

        Returns:
            The plan — inspect it before executing.

        Raises:
            ConfigurationError: If ``backup_taken_at`` is timezone-naive.
        """
        return ReplayPlan.derive(events, backup_taken_at=backup_taken_at)

    def replay(self, session: Session, plan: ReplayPlan) -> tuple[ErasureResult, ...]:
        """Re-apply every replayable erasure in the plan.

        Per entry, in plan order: one additive ``ERASURE_REPLAYED`` event
        is appended **before** any mutation (ADR 0015's ordering rule — if
        the sink is down, nothing changes), then the planner's
        ``erase_subject`` re-runs the erasure, appending its full ADR 0009
        sequence. Subjects listed under ``indeterminate`` or
        ``failed_only`` are never executed.

        Runs in the caller's open session and never commits (ADR 0006).
        Fail-fast: the first failure re-raises and later entries are not
        started — ``erase_subject``'s contract forbids committing after it
        raises, so continuing in the same session would be unsound. The
        caller rolls back; independently committed audit events persist
        (duplicates possible, missing never), and re-running the replay
        converges — a replay of a replay is a no-op success.

        Args:
            session: An open database session; commit or roll back the
                whole replay as one unit.
            plan: The derived plan to execute.

        Returns:
            One :class:`~effaced.ErasureResult` per replayed subject, in
            plan order.

        Raises:
            ConfigurationError: If the planner is not wired for execution.
            ResolverError: If ``refs_for`` returns a ref whose ``kind``
                matches no registered resolver.
        """
        results: list[ErasureResult] = []
        for entry in plan.entries:
            self._audit_sink.append(
                AuditEvent(
                    event_id=uuid4(),
                    event_type=AuditEventType.ERASURE_REPLAYED,
                    subject_ref=entry.subject_id,
                    occurred_at=datetime.now(UTC),
                    payload={
                        "backup_taken_at": plan.backup_taken_at.isoformat(),
                        "source_event_id": str(entry.source_event_id),
                        "completions": entry.completions,
                    },
                )
            )
            refs = self._refs_for(entry.subject_id) if self._refs_for is not None else ()
            results.append(self._planner.erase_subject(session, entry.subject_id, refs=refs))
        return tuple(results)
