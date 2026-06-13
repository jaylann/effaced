"""ReplayPlan.derive — pure classification of the surviving trail (ADR 0023)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from effaced import AuditEvent, AuditEventType, ConfigurationError, ReplayPlan

BACKUP_AT = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
BEFORE = BACKUP_AT - timedelta(hours=1)
AFTER = BACKUP_AT + timedelta(hours=1)
LATER = BACKUP_AT + timedelta(hours=2)


def event(
    number: int,
    event_type: AuditEventType,
    *,
    subject: str = "1",
    at: datetime = AFTER,
) -> AuditEvent:
    return AuditEvent(
        event_id=UUID(int=number),
        event_type=event_type,
        subject_ref=subject,
        occurred_at=at,
        payload={},
    )


def test_completion_after_the_backup_point_is_replayable() -> None:
    """A post-backup ERASURE_LOCAL_COMPLETED makes the subject replayable."""
    completed = event(1, AuditEventType.ERASURE_LOCAL_COMPLETED, at=AFTER)
    plan = ReplayPlan.derive([completed], backup_taken_at=BACKUP_AT)
    (entry,) = plan.entries
    assert entry.subject_id == "1"
    assert entry.completions == 1
    assert entry.last_completed_at == AFTER
    assert entry.source_event_id == UUID(int=1)
    assert plan.indeterminate == ()
    assert plan.failed_only == ()


def test_completion_before_the_backup_point_is_not_in_the_plan() -> None:
    """An erasure already inside the backup was never resurrected."""
    completed = event(1, AuditEventType.ERASURE_LOCAL_COMPLETED, at=BEFORE)
    plan = ReplayPlan.derive([completed], backup_taken_at=BACKUP_AT)
    assert plan.entries == ()
    assert plan.indeterminate == ()
    assert plan.failed_only == ()


def test_completion_at_exactly_the_backup_instant_is_replayed() -> None:
    """The boundary is inclusive: when in doubt, replay (a convergent no-op)."""
    completed = event(1, AuditEventType.ERASURE_LOCAL_COMPLETED, at=BACKUP_AT)
    plan = ReplayPlan.derive([completed], backup_taken_at=BACKUP_AT)
    assert [entry.subject_id for entry in plan.entries] == ["1"]


def test_interrupted_attempt_is_indeterminate() -> None:
    """REQUESTED with no terminal event is surfaced, never executed or guessed."""
    requested = event(1, AuditEventType.ERASURE_REQUESTED)
    plan = ReplayPlan.derive([requested], backup_taken_at=BACKUP_AT)
    assert plan.entries == ()
    assert plan.indeterminate == ("1",)
    assert plan.failed_only == ()


def test_failed_attempt_is_failed_only() -> None:
    """A failed attempt rolled back — the restore resurrected nothing of it."""
    events = [
        event(1, AuditEventType.ERASURE_REQUESTED),
        event(2, AuditEventType.ERASURE_STEP_FAILED),
    ]
    plan = ReplayPlan.derive(events, backup_taken_at=BACKUP_AT)
    assert plan.entries == ()
    assert plan.indeterminate == ()
    assert plan.failed_only == ("1",)


def test_failure_then_success_is_replayable() -> None:
    """A retried erasure that completed locally is replayed like any other."""
    events = [
        event(1, AuditEventType.ERASURE_REQUESTED, at=AFTER),
        event(2, AuditEventType.ERASURE_STEP_FAILED, at=AFTER),
        event(3, AuditEventType.ERASURE_REQUESTED, at=LATER),
        event(4, AuditEventType.ERASURE_LOCAL_COMPLETED, at=LATER),
    ]
    plan = ReplayPlan.derive(events, backup_taken_at=BACKUP_AT)
    assert [entry.subject_id for entry in plan.entries] == ["1"]
    assert plan.failed_only == ()
    assert plan.indeterminate == ()


def test_completion_then_interrupted_attempt_stays_replayable() -> None:
    """A trailing interrupted attempt never demotes a committed completion."""
    events = [
        event(1, AuditEventType.ERASURE_LOCAL_COMPLETED, at=AFTER),
        event(2, AuditEventType.ERASURE_REQUESTED, at=LATER),
    ]
    plan = ReplayPlan.derive(events, backup_taken_at=BACKUP_AT)
    assert [entry.subject_id for entry in plan.entries] == ["1"]
    assert plan.indeterminate == ()


def test_multiple_completions_are_counted_and_the_latest_is_cited() -> None:
    """The entry counts every qualifying completion and cites the latest one."""
    events = [
        event(1, AuditEventType.ERASURE_LOCAL_COMPLETED, at=AFTER),
        event(2, AuditEventType.ERASURE_LOCAL_COMPLETED, at=LATER),
    ]
    plan = ReplayPlan.derive(events, backup_taken_at=BACKUP_AT)
    (entry,) = plan.entries
    assert entry.completions == 2
    assert entry.last_completed_at == LATER
    assert entry.source_event_id == UUID(int=2)


def test_simultaneous_completions_break_the_tie_by_event_id() -> None:
    """Equal timestamps resolve to the larger event id — read order's last row."""
    events = [
        event(7, AuditEventType.ERASURE_LOCAL_COMPLETED, at=AFTER),
        event(3, AuditEventType.ERASURE_LOCAL_COMPLETED, at=AFTER),
    ]
    plan = ReplayPlan.derive(events, backup_taken_at=BACKUP_AT)
    (entry,) = plan.entries
    assert entry.source_event_id == UUID(int=7)


def test_unrelated_event_types_never_classify_a_subject() -> None:
    """Only REQUESTED / STEP_FAILED / LOCAL_COMPLETED drive classification."""
    events = [
        event(1, AuditEventType.ERASURE_STEP_SUCCEEDED),
        event(2, AuditEventType.ERASURE_COMPLETED),
        event(3, AuditEventType.ERASURE_REQUEUED),
        event(4, AuditEventType.ERASURE_REPLAYED),
        event(5, AuditEventType.CONSENT_GRANTED),
        event(6, AuditEventType.EXPORT_COMPLETED),
        event(7, AuditEventType.RETENTION_EXPIRED),
    ]
    plan = ReplayPlan.derive(events, backup_taken_at=BACKUP_AT)
    assert plan.entries == ()
    assert plan.indeterminate == ()
    assert plan.failed_only == ()


def test_pre_backup_activity_never_classifies_a_subject() -> None:
    """Attempts entirely before the backup point are outside the window."""
    events = [
        event(1, AuditEventType.ERASURE_REQUESTED, at=BEFORE),
        event(2, AuditEventType.ERASURE_STEP_FAILED, at=BEFORE),
    ]
    plan = ReplayPlan.derive(events, backup_taken_at=BACKUP_AT)
    assert plan.entries == ()
    assert plan.indeterminate == ()
    assert plan.failed_only == ()


def test_entries_are_ordered_by_completion_time_then_subject() -> None:
    """Plan order is deterministic: (last_completed_at, subject_id)."""
    events = [
        event(1, AuditEventType.ERASURE_LOCAL_COMPLETED, subject="9", at=AFTER),
        event(2, AuditEventType.ERASURE_LOCAL_COMPLETED, subject="2", at=LATER),
        event(3, AuditEventType.ERASURE_LOCAL_COMPLETED, subject="10", at=AFTER),
    ]
    plan = ReplayPlan.derive(events, backup_taken_at=BACKUP_AT)
    assert [entry.subject_id for entry in plan.entries] == ["10", "9", "2"]


def test_plan_is_unchanged_under_event_reordering() -> None:
    """Derivation is a pure function of the event *set*, not its order."""
    events = [
        event(1, AuditEventType.ERASURE_LOCAL_COMPLETED, subject="1", at=AFTER),
        event(2, AuditEventType.ERASURE_REQUESTED, subject="2", at=AFTER),
        event(3, AuditEventType.ERASURE_STEP_FAILED, subject="3", at=AFTER),
        event(4, AuditEventType.ERASURE_LOCAL_COMPLETED, subject="1", at=LATER),
        event(5, AuditEventType.CONSENT_GRANTED, subject="4", at=AFTER),
    ]
    forward = ReplayPlan.derive(events, backup_taken_at=BACKUP_AT)
    shuffled = ReplayPlan.derive(list(reversed(events)), backup_taken_at=BACKUP_AT)
    assert forward == shuffled


def test_naive_backup_timestamp_raises_configuration_error() -> None:
    """A timezone-unaware cutoff cannot be honestly compared against UTC."""
    naive = datetime(2026, 6, 1, 12, 0)
    with pytest.raises(ConfigurationError, match="timezone-aware"):
        ReplayPlan.derive([], backup_taken_at=naive)
