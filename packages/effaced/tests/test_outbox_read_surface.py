"""The outbox's read-only operator surface: abandoned/scheduled listings, counts."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    EffacedTables,
    Outbox,
    OutboxEntry,
    OutboxStatus,
    SqlStatusCountsSource,
    SubjectRef,
    bind_tables,
)

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


@pytest.fixture(params=["python", "sql"])
def counting_outbox(request: pytest.FixtureRequest, harness: ReadHarness) -> Outbox:
    """The harness outbox, once counting in Python and once SQL-side.

    Both paths share the harness's seeded database; only the
    ``status_counts`` strategy differs, so every count assertion runs
    against the default Python loop and the injected SQL aggregator.
    """
    if request.param == "sql":
        return Outbox(
            harness.session_factory,
            harness.tables.outbox,
            status_counts_source=SqlStatusCountsSource(),
        )
    return harness.outbox


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


def schedule(harness: ReadHarness, item: OutboxEntry, *, resume_at: datetime) -> None:
    harness.outbox.mark_scheduled(item, resume_at=resume_at)


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
    """Non-default lifecycle fields come back exactly as stored.

    Guards the row→entry mapping against the claim-time mapper's habits
    (`attempts + 1`, overwritten timestamps): a mismapped copy would fail
    on these values.
    """
    original = OutboxEntry(
        entry_id=UUID(int=7),
        subject_id="42",
        resolver="stripe",
        ref=SubjectRef(kind="stripe_customer", value="cus_7", extra={"account": "acct_9"}),
        status=OutboxStatus.FAILED,
        attempts=3,
        enqueued_at=ENQUEUED_AT,
        last_attempt_at=ENQUEUED_AT + timedelta(minutes=5),
    )
    seed(harness, [original])
    abandon(harness, original, error="StripeError")
    (listed,) = harness.outbox.list_abandoned()
    assert listed.entry_id == original.entry_id
    assert listed.subject_id == "42"
    assert listed.resolver == "stripe"
    assert listed.ref == original.ref
    assert listed.status is OutboxStatus.ABANDONED
    assert listed.attempts == 3
    # SQLite hands timestamps back naive; they are stored as UTC
    assert listed.enqueued_at.replace(tzinfo=UTC) == original.enqueued_at
    assert listed.last_attempt_at is not None
    assert listed.last_attempt_at.replace(tzinfo=UTC) == original.last_attempt_at
    assert listed.next_attempt_at is None
    assert listed.last_error == "StripeError"


def test_list_scheduled_returns_only_scheduled_entries(harness: ReadHarness) -> None:
    seed(harness, [entry(1), entry(2), entry(3)])
    abandon(harness, entry(1))
    schedule(harness, entry(2), resume_at=ENQUEUED_AT + timedelta(days=30))
    listed = harness.outbox.list_scheduled()
    assert [item.entry_id for item in listed] == [UUID(int=2)]
    assert listed[0].status is OutboxStatus.SCHEDULED


def test_list_scheduled_orders_by_horizon_and_respects_limit(harness: ReadHarness) -> None:
    """Nearest horizon first — ``next_attempt_at``, not enqueue order.

    The deliberate divergence from :meth:`list_abandoned`: entry 1 enqueued
    first but expires last, so it surfaces last.
    """
    seed(harness, [entry(1), entry(2), entry(3)])
    schedule(harness, entry(1), resume_at=ENQUEUED_AT + timedelta(days=30))
    schedule(harness, entry(2), resume_at=ENQUEUED_AT + timedelta(days=10))
    schedule(harness, entry(3), resume_at=ENQUEUED_AT + timedelta(days=20))
    assert [item.entry_id for item in harness.outbox.list_scheduled()] == [
        UUID(int=2),
        UUID(int=3),
        UUID(int=1),
    ]
    assert [item.entry_id for item in harness.outbox.list_scheduled(limit=2)] == [
        UUID(int=2),
        UUID(int=3),
    ]


def test_list_scheduled_round_trips_the_full_entry(harness: ReadHarness) -> None:
    """A parked entry comes back carrying its horizon and a reset budget.

    ``mark_scheduled`` resets ``attempts``/``last_error`` and stamps
    ``next_attempt_at`` with the horizon (ADR 0022); the read surface must
    surface exactly that, plus the untouched identity fields.
    """
    original = OutboxEntry(
        entry_id=UUID(int=7),
        subject_id="42",
        resolver="stripe",
        ref=SubjectRef(kind="stripe_customer", value="cus_7", extra={"account": "acct_9"}),
        enqueued_at=ENQUEUED_AT,
    )
    horizon = ENQUEUED_AT + timedelta(days=90)
    seed(harness, [original])
    schedule(harness, original, resume_at=horizon)
    (listed,) = harness.outbox.list_scheduled()
    assert listed.entry_id == original.entry_id
    assert listed.subject_id == "42"
    assert listed.resolver == "stripe"
    assert listed.ref == original.ref
    assert listed.status is OutboxStatus.SCHEDULED
    assert listed.attempts == 0
    assert listed.last_error is None
    # SQLite hands timestamps back naive; they are stored as UTC
    assert listed.enqueued_at.replace(tzinfo=UTC) == original.enqueued_at
    assert listed.next_attempt_at is not None
    assert listed.next_attempt_at.replace(tzinfo=UTC) == horizon


def test_status_counts_are_zero_filled(counting_outbox: Outbox) -> None:
    assert counting_outbox.status_counts() == dict.fromkeys(OutboxStatus, 0)


def test_status_counts_track_lifecycle_transitions(
    harness: ReadHarness, counting_outbox: Outbox
) -> None:
    seed(harness, [entry(1), entry(2), entry(3, subject_id="2")])
    abandon(harness, entry(2))
    counts = counting_outbox.status_counts()
    assert counts[OutboxStatus.PENDING] == 2
    assert counts[OutboxStatus.ABANDONED] == 1
    assert counts[OutboxStatus.IN_FLIGHT] == 0
    assert counts[OutboxStatus.FAILED] == 0
    assert counts[OutboxStatus.SUCCEEDED] == 0


@pytest.mark.property
@settings(max_examples=50, deadline=None)
@given(statuses=st.lists(st.sampled_from(list(OutboxStatus)), max_size=40))
def test_sql_and_python_status_counts_agree(statuses: list[OutboxStatus]) -> None:
    """For any population of statuses, both paths return the same zero-filled map."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    try:
        session_factory = sessionmaker(engine)
        python_outbox = Outbox(session_factory, tables.outbox)
        sql_outbox = Outbox(
            session_factory, tables.outbox, status_counts_source=SqlStatusCountsSource()
        )
        entries = [
            entry(number).model_copy(update={"status": status})
            for number, status in enumerate(statuses, start=1)
        ]
        with session_factory() as session:
            python_outbox.enqueue(session, entries)
            session.commit()

        expected = dict.fromkeys(OutboxStatus, 0)
        for status in statuses:
            expected[status] += 1
        assert sql_outbox.status_counts() == python_outbox.status_counts() == expected
    finally:
        engine.dispose()


def test_read_surface_mutates_nothing(harness: ReadHarness) -> None:
    seed(harness, [entry(1), entry(2)])
    abandon(harness, entry(2))
    before = harness.outbox.status_counts()
    harness.outbox.list_abandoned()
    harness.outbox.status_counts()
    assert harness.outbox.status_counts() == before
    claimed = harness.outbox.claim_batch()
    assert [item.entry_id for item in claimed] == [UUID(int=1)]
