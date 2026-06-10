"""Outbox.claim_batch: due entries only, oldest first, leased while claimed."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from uuid import UUID

import pytest
from sqlalchemy import MetaData, create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import EffacedTables, Outbox, OutboxEntry, OutboxStatus, SubjectRef, bind_tables

LEASE = timedelta(minutes=5)


class ClaimHarness(NamedTuple):
    """An outbox wired to a fresh in-memory database."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    outbox: Outbox


@pytest.fixture()
def harness() -> Iterator[ClaimHarness]:
    """An outbox on a fresh in-memory SQLite database."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    yield ClaimHarness(
        session_factory=session_factory,
        tables=tables,
        outbox=Outbox(session_factory, tables.outbox),
    )
    engine.dispose()


def entry(number: int, *, enqueued_at: datetime | None = None) -> OutboxEntry:
    return OutboxEntry(
        entry_id=UUID(int=number),
        subject_id="1",
        resolver="stripe",
        ref=SubjectRef(kind="stripe", value=f"cus_{number}"),
        enqueued_at=enqueued_at or datetime(2026, 6, 1, 12, 0, number, tzinfo=UTC),
    )


def seed(harness: ClaimHarness, *entries: OutboxEntry) -> None:
    with harness.session_factory() as session:
        harness.outbox.enqueue(session, list(entries))
        session.commit()


def set_row(harness: ClaimHarness, entry_id: UUID, **values: object) -> None:
    """Force a row into an arbitrary state (simulates history or a crash)."""
    with harness.session_factory() as session:
        table = harness.tables.outbox
        session.execute(update(table).where(table.c.entry_id == entry_id).values(**values))
        session.commit()


def rows_by_id(harness: ClaimHarness) -> dict[UUID, dict[str, object]]:
    with harness.session_factory() as session:
        result = session.execute(select(harness.tables.outbox)).mappings()
        return {row["entry_id"]: dict(row) for row in result}


def test_claims_pending_entries_oldest_first_within_limit(harness: ClaimHarness) -> None:
    seed(harness, entry(3), entry(1), entry(2))
    claimed = harness.outbox.claim_batch(2, lease=LEASE)
    assert [c.entry_id for c in claimed] == [UUID(int=1), UUID(int=2)]
    assert rows_by_id(harness)[UUID(int=3)]["status"] == OutboxStatus.PENDING.value


def test_claimed_entries_reflect_their_post_claim_state(harness: ClaimHarness) -> None:
    seed(harness, entry(1))
    before = datetime.now(UTC)
    (claimed,) = harness.outbox.claim_batch(lease=LEASE)
    assert claimed.status is OutboxStatus.IN_FLIGHT
    assert claimed.attempts == 1
    assert claimed.subject_id == "1"
    assert claimed.ref == SubjectRef(kind="stripe", value="cus_1")
    assert claimed.last_attempt_at is not None
    assert before <= claimed.last_attempt_at <= datetime.now(UTC)
    assert claimed.next_attempt_at == claimed.last_attempt_at + LEASE
    row = rows_by_id(harness)[UUID(int=1)]
    assert row["status"] == OutboxStatus.IN_FLIGHT.value
    assert row["attempts"] == 1


def test_unexpired_claims_and_scheduled_retries_are_not_due(harness: ClaimHarness) -> None:
    seed(harness, entry(1), entry(2))
    future = datetime.now(UTC) + timedelta(minutes=10)
    set_row(harness, UUID(int=1), status=OutboxStatus.IN_FLIGHT.value, next_attempt_at=future)
    set_row(harness, UUID(int=2), status=OutboxStatus.FAILED.value, next_attempt_at=future)
    assert harness.outbox.claim_batch(lease=LEASE) == ()


def test_expired_lease_is_reclaimed(harness: ClaimHarness) -> None:
    """Crash-safety: a dead runner's IN_FLIGHT entry becomes claimable again."""
    seed(harness, entry(1))
    harness.outbox.claim_batch(lease=LEASE)
    assert harness.outbox.claim_batch(lease=LEASE) == ()  # lease still protects it
    past = datetime.now(UTC) - timedelta(seconds=1)
    set_row(harness, UUID(int=1), next_attempt_at=past)
    (reclaimed,) = harness.outbox.claim_batch(lease=LEASE)
    assert reclaimed.entry_id == UUID(int=1)
    assert reclaimed.attempts == 2


def test_failed_entries_become_due_when_their_backoff_elapses(harness: ClaimHarness) -> None:
    seed(harness, entry(1))
    past = datetime.now(UTC) - timedelta(seconds=1)
    set_row(
        harness,
        UUID(int=1),
        status=OutboxStatus.FAILED.value,
        attempts=2,
        next_attempt_at=past,
        last_error="TimeoutError",
    )
    (claimed,) = harness.outbox.claim_batch(lease=LEASE)
    assert claimed.attempts == 3
    assert claimed.last_error == "TimeoutError"


def test_terminal_entries_are_never_claimed(harness: ClaimHarness) -> None:
    seed(harness, entry(1), entry(2))
    set_row(harness, UUID(int=1), status=OutboxStatus.SUCCEEDED.value, next_attempt_at=None)
    set_row(harness, UUID(int=2), status=OutboxStatus.ABANDONED.value, next_attempt_at=None)
    assert harness.outbox.claim_batch(lease=LEASE) == ()


def test_empty_outbox_claims_nothing(harness: ClaimHarness) -> None:
    assert harness.outbox.claim_batch(lease=LEASE) == ()
