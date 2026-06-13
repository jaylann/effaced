"""Postgres-only Outbox behaviours that the SQLite mutation run cannot kill.

Ten saga ``Outbox`` mutants survive the weekly mutmut hard gate because
mutmut runs only the SQLite unit suite, which compiles away
``FOR UPDATE`` / ``SKIP LOCKED`` and returns tied rows in an order Postgres
is free to choose. The tests here pin each behaviour on real Postgres — a
``SKIP LOCKED`` skip that does not block, the ``(enqueued_at, entry_id)`` /
``(next_attempt_at, entry_id)`` tie-breaks, and the ``mark_succeeded``
sibling lock order — so the contract is evidenced even though the mutants
stay on the allowlist by design (the mutation run never executes these
tests; see ``mutation-equivalents.txt`` and PROOFS.md).

A dropped ``ORDER BY entry_id`` tie-break leaves tied rows with no total
order, so its mutant cannot be killed *deterministically* anywhere — these
tests pin the contract the real (tie-broken) query upholds; the reversed
tie-break is a clean kill. The inclusive ``<= now`` claim boundary is **not**
Postgres-only — it is killed in the SQLite suite
(``test_claim_batch.py::test_an_entry_due_at_exactly_now_is_claimable``) and
is not allowlisted.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import NamedTuple
from uuid import UUID

import pytest
from sqlalchemy import Engine, MetaData, select, update
from sqlalchemy.orm import Session, sessionmaker

from effaced import (
    DatabaseAuditSink,
    EffacedTables,
    Outbox,
    OutboxEntry,
    OutboxStatus,
    SubjectRef,
    bind_tables,
)

pytestmark = pytest.mark.integration


class PgHarness(NamedTuple):
    """Outbox + database audit sink on the integration Postgres."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    outbox: Outbox
    sink: DatabaseAuditSink


@pytest.fixture()
def harness(pg_engine: Engine) -> Iterator[PgHarness]:
    """An outbox + audit sink on a freshly created owned-table schema."""
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(pg_engine)
    try:
        session_factory = sessionmaker(pg_engine)
        yield PgHarness(
            session_factory=session_factory,
            tables=tables,
            outbox=Outbox(session_factory, tables.outbox),
            sink=DatabaseAuditSink(session_factory, tables.audit_events),
        )
    finally:
        metadata.drop_all(pg_engine)


def entry(
    number: int,
    *,
    subject_id: str = "1",
    enqueued_at: datetime | None = None,
) -> OutboxEntry:
    """Build an erase entry with a deterministic ``entry_id`` of ``UUID(int=number)``."""
    return OutboxEntry(
        entry_id=UUID(int=number),
        subject_id=subject_id,
        resolver="stripe",
        ref=SubjectRef(kind="stripe", value=f"cus_{number}"),
        enqueued_at=enqueued_at or datetime(2026, 6, 1, 12, 0, number, tzinfo=UTC),
    )


def enqueue(harness: PgHarness, *entries: OutboxEntry) -> None:
    """Persist ``entries`` in one committed transaction."""
    with harness.session_factory() as session:
        harness.outbox.enqueue(session, list(entries))
        session.commit()


def set_row(harness: PgHarness, entry_id: UUID, **values: object) -> None:
    """Force a row into an arbitrary state (simulates history or a crash)."""
    with harness.session_factory() as session:
        table = harness.tables.outbox
        session.execute(update(table).where(table.c.entry_id == entry_id).values(**values))
        session.commit()


def pending_ids(harness: PgHarness) -> set[UUID]:
    """The entry_ids of every still-``PENDING`` row."""
    table = harness.tables.outbox
    query = select(table.c.entry_id).where(table.c.status == OutboxStatus.PENDING.value)
    with harness.session_factory() as session:
        return set(session.execute(query).scalars())


def status_of(harness: PgHarness, entry_id: UUID) -> str:
    """Read one row's current status value."""
    table = harness.tables.outbox
    with harness.session_factory() as session:
        return session.execute(
            select(table.c.status).where(table.c.entry_id == entry_id)
        ).scalar_one()


def test_claim_batch_skips_locked_rows_without_blocking(harness: PgHarness) -> None:
    """``SKIP LOCKED`` makes the claim step over a held row instead of waiting.

    Kills ``claim_batch__mutmut_6`` / ``_19`` (``skip_locked=True`` flipped to
    ``False``/``None``): under a blocking ``FOR UPDATE`` the claim would wait on
    the oldest locked row, the background thread would never set the event, and
    ``worker.join`` would time out. With ``SKIP LOCKED`` it returns the three
    unlocked rows at once.
    """
    enqueue(harness, entry(1), entry(2), entry(3), entry(4))  # ascending enqueued_at
    table = harness.tables.outbox
    claimed_ids: list[UUID] = []
    returned = threading.Event()

    def claim() -> None:
        claimed_ids.extend(c.entry_id for c in harness.outbox.claim_batch(limit=50))
        returned.set()

    with harness.session_factory() as session, session.begin():
        # Hold the oldest row (sorts first) FOR UPDATE, as a concurrent claimer would.
        session.execute(
            select(table.c.entry_id).where(table.c.entry_id == UUID(int=1)).with_for_update()
        ).one()
        worker = threading.Thread(target=claim)
        worker.start()
        worker.join(timeout=5)
    # Lock released before assertions, mirroring the requeue lock-hold test.
    assert returned.is_set()  # non-blocking: the claim returned despite the held lock
    assert set(claimed_ids) == {UUID(int=2), UUID(int=3), UUID(int=4)}


def test_claim_orders_tied_enqueued_at_by_entry_id(harness: PgHarness) -> None:
    """Rows tying on ``enqueued_at`` claim in ``entry_id`` order.

    Pins ``claim_batch__mutmut_9`` / ``_11`` (the ``order_by(enqueued_at,
    entry_id)`` tie-break dropped or reversed). Three rows share one
    ``enqueued_at``, inserted in reverse id order; with ``limit=2`` the claimed
    set and the surviving ``PENDING`` row both depend on the tiebreaker, so the
    membership assertion is the strongest available.
    """
    tied = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    enqueue(
        harness,
        entry(3, enqueued_at=tied),
        entry(1, enqueued_at=tied),
        entry(2, enqueued_at=tied),
    )
    claimed = harness.outbox.claim_batch(limit=2)
    assert [c.entry_id for c in claimed] == [UUID(int=1), UUID(int=2)]
    assert pending_ids(harness) == {UUID(int=3)}


def test_list_abandoned_orders_tied_enqueued_at_by_entry_id(harness: PgHarness) -> None:
    """``list_abandoned`` orders rows tying on ``enqueued_at`` by ``entry_id``.

    Pins ``list_abandoned__mutmut_6`` / ``_8`` (the ``(enqueued_at, entry_id)``
    tie-break dropped or reversed). Three abandoned rows share one
    ``enqueued_at``, inserted in reverse id order.
    """
    tied = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    enqueue(
        harness,
        entry(3, enqueued_at=tied),
        entry(1, enqueued_at=tied),
        entry(2, enqueued_at=tied),
    )
    for number in (1, 2, 3):
        set_row(harness, UUID(int=number), status=OutboxStatus.ABANDONED.value)
    listed = harness.outbox.list_abandoned()
    assert [e.entry_id for e in listed] == [UUID(int=1), UUID(int=2), UUID(int=3)]


def test_list_scheduled_orders_tied_next_attempt_at_by_entry_id(harness: PgHarness) -> None:
    """``list_scheduled`` orders rows tying on ``next_attempt_at`` by ``entry_id``.

    Pins ``list_scheduled__mutmut_6`` / ``_8`` (the ``(next_attempt_at,
    entry_id)`` tie-break dropped or reversed). Three scheduled rows share one
    horizon, inserted in reverse id order.
    """
    horizon = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)
    enqueue(harness, entry(3), entry(1), entry(2))
    for number in (1, 2, 3):
        set_row(
            harness,
            UUID(int=number),
            status=OutboxStatus.SCHEDULED.value,
            next_attempt_at=horizon,
        )
    listed = harness.outbox.list_scheduled()
    assert [e.entry_id for e in listed] == [UUID(int=1), UUID(int=2), UUID(int=3)]


def test_mark_succeeded_serializes_against_a_held_sibling_lock(harness: PgHarness) -> None:
    """``mark_succeeded`` waits on a held sibling lock — same ``entry_id`` order.

    Pins ``mark_succeeded__mutmut_3`` (the sibling completion query's
    ``with_for_update`` dropped). Two erase siblings of one subject are both
    claimed; a held ``FOR UPDATE`` lock on one sibling blocks the other's
    ``mark_succeeded`` (whose sibling query locks both rows ordered by
    ``entry_id``) until release — proving the serialization contract without a
    flaky two-thread deadlock test.
    """
    enqueue(harness, entry(1, subject_id="subject-sib"), entry(2, subject_id="subject-sib"))
    claimed = harness.outbox.claim_batch()
    assert {c.entry_id for c in claimed} == {UUID(int=1), UUID(int=2)}
    other = next(c for c in claimed if c.entry_id == UUID(int=2))

    table = harness.tables.outbox
    done = threading.Event()

    def background_mark() -> None:
        harness.outbox.mark_succeeded(other, on_subject_complete=lambda: None)
        done.set()

    with harness.session_factory() as session, session.begin():
        # Hold sibling #1's lock, as a concurrent finisher of this subject would.
        session.execute(
            select(table.c.entry_id).where(table.c.entry_id == UUID(int=1)).with_for_update()
        ).one()
        worker = threading.Thread(target=background_mark)
        worker.start()
        time.sleep(0.3)
        # The sibling lock blocks mark_succeeded — the row has not flipped yet.
        assert not done.is_set()
    # Lock released; mark_succeeded serializes through and completes.
    worker.join(timeout=5)
    assert done.is_set()
    assert status_of(harness, UUID(int=2)) == OutboxStatus.SUCCEEDED.value
