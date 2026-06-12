"""SagaRunner against real Postgres — exactly-once claiming under concurrency."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from uuid import uuid4

import pytest
from sqlalchemy import Engine, MetaData, select
from sqlalchemy.orm import Session, sessionmaker

from effaced import (
    AuditEventType,
    BackoffPolicy,
    Correction,
    DatabaseAuditSink,
    EffacedTables,
    Outbox,
    OutboxEntry,
    OutboxOperation,
    OutboxStatus,
    PiiCategory,
    ResolverErasure,
    ResolverExport,
    ResolverRectification,
    ResolverRegistry,
    SagaRunner,
    SubjectRef,
    bind_tables,
)

pytestmark = pytest.mark.integration


class ThreadSafeResolver:
    """A resolver double recording every call under a lock."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._lock = threading.Lock()
        self.calls: list[str] = []
        self.rectify_calls: list[tuple[str, tuple[Correction, ...]]] = []

    @property
    def name(self) -> str:
        return self._name

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        raise NotImplementedError

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        await asyncio.sleep(0)  # encourage interleaving between runners
        with self._lock:
            self.calls.append(ref.value)
        return ResolverErasure(resolver=self._name)

    async def rectify_subject(
        self, ref: SubjectRef, corrections: tuple[Correction, ...]
    ) -> ResolverRectification:
        await asyncio.sleep(0)
        with self._lock:
            self.rectify_calls.append((ref.value, corrections))
        return ResolverRectification(resolver=self._name)


class PgHarness(NamedTuple):
    """Outbox + database audit sink on the integration Postgres."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    outbox: Outbox
    sink: DatabaseAuditSink
    resolver: ThreadSafeResolver


@pytest.fixture()
def harness(pg_engine: Engine) -> Iterator[PgHarness]:
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
            resolver=ThreadSafeResolver("stripe"),
        )
    finally:
        metadata.drop_all(pg_engine)


def build_runner(harness: PgHarness, *, batch_size: int = 7) -> SagaRunner:
    """A runner with its own registry, as a separate worker process would have."""
    registry = ResolverRegistry()
    registry.register(harness.resolver)
    return SagaRunner(registry, harness.outbox, harness.sink, batch_size=batch_size)


def enqueue(harness: PgHarness, entries: list[OutboxEntry]) -> None:
    with harness.session_factory() as session:
        harness.outbox.enqueue(session, entries)
        session.commit()


def entry(subject_id: str, value: str) -> OutboxEntry:
    return OutboxEntry(
        entry_id=uuid4(),
        subject_id=subject_id,
        resolver="stripe",
        ref=SubjectRef(kind="stripe", value=value),
        enqueued_at=datetime.now(UTC),
    )


def drain(runner: SagaRunner, processed: list[int]) -> None:
    """Loop run_once until the queue has nothing due, recording the total."""
    total = 0
    while True:
        count = asyncio.run(runner.run_once())
        if count == 0:
            break
        total += count
    processed.append(total)


def all_statuses(harness: PgHarness) -> list[str]:
    with harness.session_factory() as session:
        rows = session.execute(select(harness.tables.outbox.c.status)).scalars()
        return [str(status) for status in rows]


def completed_events(harness: PgHarness, subject_id: str) -> list[str]:
    return [
        str(event.event_type)
        for event in harness.sink.read(subject_id)
        if event.event_type is AuditEventType.ERASURE_COMPLETED
    ]


def test_two_concurrent_runners_process_each_entry_exactly_once(harness: PgHarness) -> None:
    """Acceptance: 100 entries, two runners, no double-execution (SKIP LOCKED)."""
    values = [f"cus_{i}" for i in range(100)]
    enqueue(harness, [entry(f"subject-{i % 10}", value) for i, value in enumerate(values)])

    processed: list[int] = []
    threads = [
        threading.Thread(target=drain, args=(build_runner(harness), processed)) for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(harness.resolver.calls) == sorted(values)  # each exactly once
    assert sum(processed) == 100
    assert all_statuses(harness) == [OutboxStatus.SUCCEEDED.value] * 100
    for i in range(10):
        assert completed_events(harness, f"subject-{i}") == ["erasure_completed"]


def test_concurrent_finishers_emit_exactly_one_completion(harness: PgHarness) -> None:
    """The locked completion check serializes runners racing on one subject."""
    enqueue(harness, [entry("subject-race", f"cus_{i}") for i in range(40)])

    processed: list[int] = []
    threads = [
        threading.Thread(target=drain, args=(build_runner(harness, batch_size=3), processed))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert completed_events(harness, "subject-race") == ["erasure_completed"]


def test_skip_locked_skips_rows_held_by_an_open_claim(harness: PgHarness) -> None:
    enqueue(harness, [entry("subject-1", f"cus_{i}") for i in range(5)])
    with harness.session_factory() as session, session.begin():
        table = harness.tables.outbox
        locked = session.execute(select(table.c.entry_id).with_for_update(skip_locked=True)).all()
        assert len(locked) == 5
        # A concurrent claimer skips the locked rows instead of blocking.
        assert harness.outbox.claim_batch() == ()
    claimed = harness.outbox.claim_batch()
    assert len(claimed) == 5


def test_expired_lease_lets_a_second_runner_take_over(harness: PgHarness) -> None:
    """Crash-safety: a dead runner's claim is healed by lease expiry."""
    enqueue(harness, [entry("subject-1", "cus_1")])
    short_lease = BackoffPolicy(lease=timedelta(milliseconds=50))
    first = harness.outbox.claim_batch(lease=short_lease.lease)
    assert [claimed.attempts for claimed in first] == [1]
    # The first runner dies here without settling its claim.
    time.sleep(0.2)
    second = harness.outbox.claim_batch()
    assert [claimed.attempts for claimed in second] == [2]
    assert second[0].entry_id == first[0].entry_id


# --- rectify entries (ADR 0013) ----------------------------------------------

CORRECTIONS = (
    Correction(category=PiiCategory.CONTACT, value="new@example.com"),
    Correction(category=PiiCategory.IDENTITY, value="New Name"),
)


def rectify_entry(subject_id: str, value: str) -> OutboxEntry:
    return OutboxEntry(
        entry_id=uuid4(),
        subject_id=subject_id,
        resolver="stripe",
        ref=SubjectRef(kind="stripe", value=value),
        operation=OutboxOperation.RECTIFY,
        corrections=CORRECTIONS,
        enqueued_at=datetime.now(UTC),
    )


def test_corrections_payload_round_trips_jsonb_and_clears_on_success(
    harness: PgHarness,
) -> None:
    """The JSONB payload survives storage and claim, and is NULL once terminal."""
    enqueue(harness, [rectify_entry("subject-r", "cus_r")])
    (claimed,) = harness.outbox.claim_batch()
    assert claimed.operation is OutboxOperation.RECTIFY
    assert claimed.corrections == CORRECTIONS
    completions: list[str] = []
    harness.outbox.mark_succeeded(claimed, on_subject_complete=lambda: completions.append("done"))
    assert completions == ["done"]
    with harness.session_factory() as session:
        row = session.execute(select(harness.tables.outbox)).mappings().one()
        assert row["status"] == OutboxStatus.SUCCEEDED.value
        assert row["payload"] is None


def test_erase_and_rectify_complete_independently_under_real_locking(
    harness: PgHarness,
) -> None:
    """The locked completion check is scoped per (subject, operation)."""
    enqueue(harness, [entry("subject-mixed", "cus_e"), rectify_entry("subject-mixed", "cus_r")])
    runner = build_runner(harness)
    processed: list[int] = []
    drain(runner, processed)
    assert sum(processed) == 2
    assert harness.resolver.calls == ["cus_e"]
    assert harness.resolver.rectify_calls == [("cus_r", CORRECTIONS)]
    events = [str(event.event_type) for event in harness.sink.read("subject-mixed")]
    assert events.count("erasure_completed") == 1
    assert events.count("rectification_completed") == 1
