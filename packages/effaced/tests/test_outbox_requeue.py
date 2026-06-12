"""Outbox.requeue: supervised ABANDONED→PENDING transition (ADR 0015)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from uuid import UUID

import pytest
from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    AuditEvent,
    AuditEventType,
    ConfigurationError,
    Correction,
    EffacedTables,
    Outbox,
    OutboxEntry,
    OutboxOperation,
    OutboxStatus,
    PiiCategory,
    SubjectRef,
    bind_tables,
)


class RecordingSink:
    """An in-memory audit sink recording every appended event."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self.events.append(event)

    def read(self, subject_ref: str) -> Sequence[AuditEvent]:
        return [event for event in self.events if event.subject_ref == subject_ref]


class FailingSink:
    """An audit sink that raises on the ``fail_on``-th append (1-based).

    ``fail_on=1`` is a fully "down" sink; a larger value lets the first
    appends succeed and fails partway through a batch — the case that
    proves requeue is all-or-nothing, not per-entry.
    """

    def __init__(self, *, fail_on: int = 1) -> None:
        self._fail_on = fail_on
        self.events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        if len(self.events) + 1 == self._fail_on:
            msg = "sink is down"
            raise RuntimeError(msg)
        self.events.append(event)

    def read(self, subject_ref: str) -> Sequence[AuditEvent]:
        return [event for event in self.events if event.subject_ref == subject_ref]


class RequeueHarness(NamedTuple):
    """An outbox + recording sink wired to a fresh in-memory database."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    sink: RecordingSink
    outbox: Outbox


@pytest.fixture()
def harness() -> Iterator[RequeueHarness]:
    """An outbox with a recording sink on a fresh in-memory SQLite database."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    sink = RecordingSink()
    yield RequeueHarness(
        session_factory=session_factory,
        tables=tables,
        sink=sink,
        outbox=Outbox(session_factory, tables.outbox, audit_sink=sink),
    )
    engine.dispose()


ENQUEUED_AT = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

CORRECTIONS = (
    Correction(category=PiiCategory.CONTACT, value="new@example.com"),
    Correction(category=PiiCategory.IDENTITY, value="New Name"),
)


def entry(
    number: int,
    *,
    subject_id: str = "1",
    resolver: str = "stripe",
    operation: OutboxOperation = OutboxOperation.ERASE,
) -> OutboxEntry:
    return OutboxEntry(
        entry_id=UUID(int=number),
        subject_id=subject_id,
        resolver=resolver,
        ref=SubjectRef(kind="stripe", value=f"cus_{number}"),
        operation=operation,
        corrections=CORRECTIONS if operation is OutboxOperation.RECTIFY else (),
        enqueued_at=ENQUEUED_AT,
    )


def seed(harness: RequeueHarness, entries: list[OutboxEntry]) -> None:
    with harness.session_factory() as session:
        harness.outbox.enqueue(session, entries)
        session.commit()


def abandon(harness: RequeueHarness, item: OutboxEntry, *, error: str = "ResolverError") -> None:
    """Drive an entry to ABANDONED with a known error and attempts > 0."""
    claimed = next(c for c in harness.outbox.claim_batch(limit=200) if c.entry_id == item.entry_id)
    harness.outbox.mark_abandoned(claimed, error=error)


def stored_row(harness: RequeueHarness, entry_id: UUID) -> dict[str, object]:
    columns = harness.tables.outbox.c
    with harness.session_factory() as session:
        statement = select(harness.tables.outbox).where(columns.entry_id == entry_id)
        return dict(session.execute(statement).mappings().one())


def test_requeue_flips_abandoned_to_pending_with_a_fresh_budget(
    harness: RequeueHarness,
) -> None:
    """An ABANDONED entry returns to PENDING with attempts/gate/error reset."""
    seed(harness, [entry(1)])
    abandon(harness, entry(1))
    row = stored_row(harness, UUID(int=1))
    assert row["status"] == OutboxStatus.ABANDONED.value
    assert row["attempts"] == 1  # one claim happened
    assert row["last_error"] == "ResolverError"

    requeued = harness.outbox.requeue([UUID(int=1)])

    assert [item.entry_id for item in requeued] == [UUID(int=1)]
    assert requeued[0].status is OutboxStatus.PENDING
    assert requeued[0].attempts == 0
    assert requeued[0].next_attempt_at is None
    assert requeued[0].last_error is None
    row = stored_row(harness, UUID(int=1))
    assert row["status"] == OutboxStatus.PENDING.value
    assert row["attempts"] == 0
    assert row["next_attempt_at"] is None
    assert row["last_error"] is None


def test_requeue_skips_missing_and_non_abandoned_ids(harness: RequeueHarness) -> None:
    """Missing or non-ABANDONED ids are silently skipped, never errors."""
    seed(harness, [entry(1)])
    abandon(harness, entry(1))
    seed(harness, [entry(2)])  # seeded after the claim, so it stays PENDING
    # entry(2) is still PENDING; UUID(int=99) does not exist.
    requeued = harness.outbox.requeue([UUID(int=1), UUID(int=2), UUID(int=99)])
    assert [item.entry_id for item in requeued] == [UUID(int=1)]
    assert stored_row(harness, UUID(int=2))["status"] == OutboxStatus.PENDING.value


def test_requeue_is_idempotent(harness: RequeueHarness) -> None:
    """Calling requeue twice with the same ids equals calling it once."""
    seed(harness, [entry(1)])
    abandon(harness, entry(1))
    first = harness.outbox.requeue([UUID(int=1)])
    assert [item.entry_id for item in first] == [UUID(int=1)]
    # The entry is PENDING now, so the second call flips nothing.
    second = harness.outbox.requeue([UUID(int=1)])
    assert second == ()
    assert stored_row(harness, UUID(int=1))["status"] == OutboxStatus.PENDING.value
    # Exactly one requeue event was appended.
    requeue_events = [
        event
        for event in harness.sink.events
        if event.event_type is AuditEventType.ERASURE_REQUEUED
    ]
    assert len(requeue_events) == 1


def test_requeue_appends_an_erasure_event_class_name_only(harness: RequeueHarness) -> None:
    """The audit event carries the entry context with a class-name-only error."""
    seed(harness, [entry(1)])
    abandon(harness, entry(1), error="StripeError")
    harness.outbox.requeue([UUID(int=1)])
    (event,) = harness.sink.events
    assert event.event_type is AuditEventType.ERASURE_REQUEUED
    assert event.subject_ref == "1"
    assert event.payload == {
        "entry_id": str(UUID(int=1)),
        "resolver": "stripe",
        "prior_attempts": 1,
        "prior_error": "StripeError",
    }


def test_requeue_refuses_an_abandoned_rectify_entry(harness: RequeueHarness) -> None:
    """A rectify entry can't be requeued — its corrections were cleared (ADR 0013).

    Requeuing would re-execute with no corrections (a silent no-op
    rectification that still completes), so it raises before any append or
    flip; the row stays ABANDONED and no event is written.
    """
    seed(harness, [entry(1, operation=OutboxOperation.RECTIFY)])
    abandon(harness, entry(1, operation=OutboxOperation.RECTIFY))
    with pytest.raises(ConfigurationError, match=str(UUID(int=1))):
        harness.outbox.requeue([UUID(int=1)])
    assert stored_row(harness, UUID(int=1))["status"] == OutboxStatus.ABANDONED.value
    assert harness.sink.events == []


def test_requeue_of_a_mixed_batch_refuses_all_before_any_flip(
    harness: RequeueHarness,
) -> None:
    """One abandoned rectify id poisons the whole batch — validation-first.

    The erase sibling must NOT flip and NO event must be appended: the
    rectify rejection happens before any side effect, so the operator gets
    an all-or-nothing answer rather than a half-applied requeue.
    """
    seed(harness, [entry(1), entry(2, operation=OutboxOperation.RECTIFY)])
    for claimed in harness.outbox.claim_batch(limit=200):
        harness.outbox.mark_abandoned(claimed, error="ResolverError")
    with pytest.raises(ConfigurationError, match=str(UUID(int=2))):
        harness.outbox.requeue([UUID(int=1), UUID(int=2)])
    assert stored_row(harness, UUID(int=1))["status"] == OutboxStatus.ABANDONED.value
    assert stored_row(harness, UUID(int=2))["status"] == OutboxStatus.ABANDONED.value
    assert harness.sink.events == []


def test_requeue_append_first_no_flip_when_sink_is_down(harness: RequeueHarness) -> None:
    """Sink down ⇒ exception before any flip ⇒ nothing transitions."""
    seed(harness, [entry(1)])
    abandon(harness, entry(1))
    failing = Outbox(harness.session_factory, harness.tables.outbox, audit_sink=FailingSink())
    with pytest.raises(RuntimeError):
        failing.requeue([UUID(int=1)])
    # The append failed before the flip — the row is still ABANDONED.
    assert stored_row(harness, UUID(int=1))["status"] == OutboxStatus.ABANDONED.value


def test_requeue_is_all_or_nothing_when_the_sink_dies_mid_batch(
    harness: RequeueHarness,
) -> None:
    """A sink failing on the 2nd of N appends flips NO row — append-first is per batch.

    Pins the MAJOR-protected all-or-nothing property: a refactor that
    moved the flip inside the per-entry loop would leave the first entry
    PENDING here and pass every single-entry test.
    """
    seed(harness, [entry(1), entry(2), entry(3)])
    for claimed in harness.outbox.claim_batch(limit=200):
        harness.outbox.mark_abandoned(claimed, error="ResolverError")
    sink = FailingSink(fail_on=2)
    failing = Outbox(harness.session_factory, harness.tables.outbox, audit_sink=sink)
    with pytest.raises(RuntimeError):
        failing.requeue([UUID(int=1), UUID(int=2), UUID(int=3)])
    # The first append succeeded, the second raised — but no row flipped.
    for number in (1, 2, 3):
        assert stored_row(harness, UUID(int=number))["status"] == OutboxStatus.ABANDONED.value


def test_requeue_without_an_audit_sink_raises_clearly(harness: RequeueHarness) -> None:
    """An outbox constructed without a sink refuses to requeue."""
    no_sink = Outbox(harness.session_factory, harness.tables.outbox)
    seed(harness, [entry(1)])
    abandon(harness, entry(1))
    with pytest.raises(ConfigurationError):
        no_sink.requeue([UUID(int=1)])


def test_requeued_entry_is_due_immediately_and_reclaimable(harness: RequeueHarness) -> None:
    """A requeued entry re-enters the claimable population with a full budget."""
    seed(harness, [entry(1)])
    abandon(harness, entry(1))
    harness.outbox.requeue([UUID(int=1)])
    (claimed,) = harness.outbox.claim_batch()
    assert claimed.entry_id == UUID(int=1)
    assert claimed.attempts == 1  # the fresh claim, off a reset budget


def test_requeue_resets_the_lease_gate_for_a_failed_then_abandoned_entry(
    harness: RequeueHarness,
) -> None:
    """next_attempt_at is cleared even if the abandoned row carried a future gate."""
    seed(harness, [entry(1)])
    claimed = next(c for c in harness.outbox.claim_batch() if c.entry_id == UUID(int=1))
    harness.outbox.mark_failed(
        claimed, error="TimeoutError", next_attempt_at=ENQUEUED_AT + timedelta(hours=1)
    )
    reclaimed = next(c for c in harness.outbox.claim_batch() if c.entry_id == UUID(int=1))
    harness.outbox.mark_abandoned(reclaimed, error="ResolverError")
    requeued = harness.outbox.requeue([UUID(int=1)])
    assert len(requeued) == 1
    assert requeued[0].next_attempt_at is None
    assert requeued[0].attempts == 0
