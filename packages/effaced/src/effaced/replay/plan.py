"""The :class:`ReplayPlan` models — which committed erasures a restore undid."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from effaced.audit.event_type import AuditEventType
from effaced.exceptions import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from effaced.audit.event import AuditEvent


class ReplayPlanEntry(BaseModel):
    """One subject whose committed erasure the restore resurrected.

    The entry is evidence, not just a work item: it counts the qualifying
    ``ERASURE_LOCAL_COMPLETED`` events and cites the latest one, so the
    decision to replay is traceable back to the surviving trail.

    Attributes:
        subject_id: The subject identifier (the events' ``subject_ref``).
        completions: How many qualifying completions the window holds.
        last_completed_at: When the latest qualifying completion occurred.
        source_event_id: The latest qualifying completion event — the
            evidence the replay decision rests on.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_id: str = Field(min_length=1)
    completions: int = Field(ge=1)
    last_completed_at: datetime
    source_event_id: UUID


class ReplayPlan(BaseModel):
    """What one surviving trail says must be replayed after a restore.

    Derived purely from audit events (ADR 0023): same events in, equal plan
    out — no clock, no database. Subjects whose post-backup window shows a
    committed local erasure are replayable; everything the trail cannot
    settle is surfaced, never guessed, in the same counted-never-guessed
    posture as the retention sweep (ADR 0012).

    Attributes:
        backup_taken_at: The cutoff instant the plan was derived against
            (timezone-aware; the boundary is inclusive).
        entries: Replayable subjects, ordered by ``(last_completed_at,
            subject_id)`` for deterministic execution.
        indeterminate: Subjects with an interrupted post-cutoff attempt
            (``ERASURE_REQUESTED`` with no terminal event) — the trail does
            not show whether anything was committed. Operator's call.
        failed_only: Subjects whose post-cutoff attempts all failed — those
            erasures rolled back, so the restore resurrected nothing of
            them. Listed for completeness, never executed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    backup_taken_at: datetime
    entries: tuple[ReplayPlanEntry, ...] = ()
    indeterminate: tuple[str, ...] = ()
    failed_only: tuple[str, ...] = ()

    @classmethod
    def derive(
        cls,
        events: Sequence[AuditEvent],
        *,
        backup_taken_at: datetime,
    ) -> ReplayPlan:
        """Classify a surviving trail against a backup point.

        A pure function: no I/O, no clock, and any ordering of the same
        events yields an equal plan. Per subject, looking only at events
        with ``occurred_at >= backup_taken_at`` (inclusive — whether a
        commit at the backup instant made the backup is unknowable, and
        over-replay is a convergent no-op): any ``ERASURE_LOCAL_COMPLETED``
        makes the subject replayable; otherwise any ``ERASURE_STEP_FAILED``
        lists it under ``failed_only``; otherwise any ``ERASURE_REQUESTED``
        lists it under ``indeterminate``. All other event types are ignored.

        Args:
            events: The surviving trail — from an external sink, a replica,
                or a pre-restore dump. The restored database's own trail
                lost the post-backup window and cannot serve here.
            backup_taken_at: When the restored backup was taken. Must be
                timezone-aware; the trail's timestamps are UTC.

        Returns:
            The plan: replayable entries plus the surfaced remainder.

        Raises:
            ConfigurationError: If ``backup_taken_at`` is timezone-naive —
                comparing it against the trail's UTC timestamps would be a
                silent lie.
        """
        if (
            backup_taken_at.tzinfo is None
            or backup_taken_at.tzinfo.utcoffset(backup_taken_at) is None
        ):
            msg = (
                "backup_taken_at must be timezone-aware; the audit trail's "
                "timestamps are UTC and a naive cutoff cannot be honestly "
                "compared against them"
            )
            raise ConfigurationError(msg)
        window: dict[str, list[AuditEvent]] = {}
        for event in events:
            if event.occurred_at >= backup_taken_at:
                window.setdefault(event.subject_ref, []).append(event)
        entries: list[ReplayPlanEntry] = []
        indeterminate: list[str] = []
        failed_only: list[str] = []
        for subject_id, subject_events in window.items():
            completions = [
                event
                for event in subject_events
                if event.event_type is AuditEventType.ERASURE_LOCAL_COMPLETED
            ]
            if completions:
                entries.append(_entry(subject_id, completions))
            elif _holds(subject_events, AuditEventType.ERASURE_STEP_FAILED):
                failed_only.append(subject_id)
            elif _holds(subject_events, AuditEventType.ERASURE_REQUESTED):
                indeterminate.append(subject_id)
        entries.sort(key=lambda entry: (entry.last_completed_at, entry.subject_id))
        return cls(
            backup_taken_at=backup_taken_at,
            entries=tuple(entries),
            indeterminate=tuple(sorted(indeterminate)),
            failed_only=tuple(sorted(failed_only)),
        )


def _entry(subject_id: str, completions: list[AuditEvent]) -> ReplayPlanEntry:
    """One replayable subject, citing the latest qualifying completion.

    "Latest" resolves ties in ``occurred_at`` by ``event_id`` — the same
    rule the database sink reads with, so the cited event is the last row
    of the subject's read order.
    """
    latest = max(completions, key=lambda event: (event.occurred_at, event.event_id))
    return ReplayPlanEntry(
        subject_id=subject_id,
        completions=len(completions),
        last_completed_at=latest.occurred_at,
        source_event_id=latest.event_id,
    )


def _holds(events: list[AuditEvent], event_type: AuditEventType) -> bool:
    """Whether any of the events is of the given type."""
    return any(event.event_type is event_type for event in events)
