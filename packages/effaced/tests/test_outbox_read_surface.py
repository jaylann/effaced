"""The outbox's read-only operator surface: abandoned listing and counts."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from uuid import UUID

import pytest
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import EffacedTables, Outbox, OutboxEntry, OutboxStatus, SubjectRef, bind_tables

ENQUEUED_AT = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


class ReadHarness(NamedTuple):
    """An outbox wired to a fresh in-memory database."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    outbox: Outbox


@pytest.fixture()
def harness() -> Iterator[ReadHarness]:
    """An outbox on a fresh in-memory SQLite database."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    yield ReadHarness(
        session_factory=session_factory,
        tables=tables,
        outbox=Outbox(session_factory, tables.outbox),
    )
    engine.dispose()


def entry(number: int, *, subject_id: str = "1", minutes: int = 0) -> OutboxEntry:
    return OutboxEntry(
        entry_id=UUID(int=number),
        subject_id=subject_id,
        resolver="stripe",
        ref=SubjectRef(kind="stripe_customer", value=f"cus_{number}"),
        enqueued_at=ENQUEUED_AT + timedelta(minutes=minutes),
    )


def seed(harness: ReadHarness, entries: list[OutboxEntry]) -> None:
    with harness.session_factory() as session:
        harness.outbox.enqueue(session, entries)
        session.commit()


def abandon(harness: ReadHarness, item: OutboxEntry, *, error: str = "ResolverError") -> None:
    harness.outbox.mark_abandoned(item, error=error)


def test_list_abandoned_returns_only_abandoned_entries(harness: ReadHarness) -> None:
    seed(harness, [entry(1), entry(2), entry(3)])
    abandon(harness, entry(2))
    listed = harness.outbox.list_abandoned()
    assert [item.entry_id for item in listed] == [UUID(int=2)]
    assert listed[0].status is OutboxStatus.ABANDONED
    assert listed[0].last_error == "ResolverError"


def test_list_abandoned_orders_oldest_first_and_respects_limit(harness: ReadHarness) -> None:
    seed(harness, [entry(3, minutes=2), entry(1, minutes=0), entry(2, minutes=1)])
    for number in (1, 2, 3):
        abandon(harness, entry(number))
    assert [item.entry_id for item in harness.outbox.list_abandoned()] == [
        UUID(int=1),
        UUID(int=2),
        UUID(int=3),
    ]
    assert [item.entry_id for item in harness.outbox.list_abandoned(limit=2)] == [
        UUID(int=1),
        UUID(int=2),
    ]


def test_list_abandoned_round_trips_the_full_entry(harness: ReadHarness) -> None:
    original = entry(7)
    seed(harness, [original])
    abandon(harness, original, error="StripeError")
    (listed,) = harness.outbox.list_abandoned()
    assert listed.subject_id == original.subject_id
    assert listed.resolver == original.resolver
    assert listed.ref == original.ref
    assert listed.attempts == original.attempts
    assert listed.next_attempt_at is None


def test_status_counts_are_zero_filled(harness: ReadHarness) -> None:
    assert harness.outbox.status_counts() == dict.fromkeys(OutboxStatus, 0)


def test_status_counts_track_lifecycle_transitions(harness: ReadHarness) -> None:
    seed(harness, [entry(1), entry(2), entry(3, subject_id="2")])
    abandon(harness, entry(2))
    counts = harness.outbox.status_counts()
    assert counts[OutboxStatus.PENDING] == 2
    assert counts[OutboxStatus.ABANDONED] == 1
    assert counts[OutboxStatus.IN_FLIGHT] == 0
    assert counts[OutboxStatus.FAILED] == 0
    assert counts[OutboxStatus.SUCCEEDED] == 0


def test_read_surface_mutates_nothing(harness: ReadHarness) -> None:
    seed(harness, [entry(1), entry(2)])
    abandon(harness, entry(2))
    before = harness.outbox.status_counts()
    harness.outbox.list_abandoned()
    harness.outbox.status_counts()
    assert harness.outbox.status_counts() == before
    claimed = harness.outbox.claim_batch()
    assert [item.entry_id for item in claimed] == [UUID(int=1)]
