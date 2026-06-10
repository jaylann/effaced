"""SagaRunner.run_once: success, backoff retries, loud abandonment, completion."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from uuid import UUID

import pytest
from conftest import RecordingAuditSink
from sqlalchemy import MetaData, create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    AuditEventType,
    BackoffPolicy,
    EffacedTables,
    Outbox,
    OutboxEntry,
    OutboxStatus,
    ResolverErasure,
    ResolverError,
    ResolverExport,
    ResolverRegistry,
    SagaRunner,
    SubjectRef,
    bind_tables,
)

BACKOFF = BackoffPolicy(
    base_delay=timedelta(minutes=1),
    max_delay=timedelta(minutes=8),
    lease=timedelta(minutes=5),
)


class ScriptedResolver:
    """A resolver double that replays a script of outcomes, then succeeds.

    Each scripted item is either a :class:`ResolverErasure` to return or an
    exception to raise; once the script is exhausted every further call
    succeeds. Calls are recorded for exactly-once assertions.
    """

    def __init__(self, name: str, script: tuple[ResolverErasure | Exception, ...] = ()) -> None:
        self._name = name
        self._script = list(script)
        self.calls: list[SubjectRef] = []

    @property
    def name(self) -> str:
        return self._name

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        raise NotImplementedError

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        self.calls.append(ref)
        if not self._script:
            return ResolverErasure(resolver=self._name)
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RunnerHarness(NamedTuple):
    """A runner over a fresh in-memory database and a recording sink."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    outbox: Outbox
    sink: RecordingAuditSink
    registry: ResolverRegistry


@pytest.fixture()
def harness() -> Iterator[RunnerHarness]:
    """An outbox + registry + sink on a fresh in-memory SQLite database."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    yield RunnerHarness(
        session_factory=session_factory,
        tables=tables,
        outbox=Outbox(session_factory, tables.outbox),
        sink=RecordingAuditSink(),
        registry=ResolverRegistry(),
    )
    engine.dispose()


def runner(harness: RunnerHarness, *, max_attempts: int = 8) -> SagaRunner:
    return SagaRunner(
        harness.registry,
        harness.outbox,
        harness.sink,
        max_attempts=max_attempts,
        backoff=BACKOFF,
    )


def entry(number: int, *, subject_id: str = "1", resolver: str = "stripe") -> OutboxEntry:
    return OutboxEntry(
        entry_id=UUID(int=number),
        subject_id=subject_id,
        resolver=resolver,
        ref=SubjectRef(kind=resolver, value=f"cus_{number}"),
        enqueued_at=datetime(2026, 6, 1, 12, 0, number, tzinfo=UTC),
    )


def seed(harness: RunnerHarness, *entries: OutboxEntry) -> None:
    with harness.session_factory() as session:
        harness.outbox.enqueue(session, list(entries))
        session.commit()


def rows_by_id(harness: RunnerHarness) -> dict[UUID, dict[str, object]]:
    with harness.session_factory() as session:
        result = session.execute(select(harness.tables.outbox)).mappings()
        return {row["entry_id"]: dict(row) for row in result}


def make_due(harness: RunnerHarness) -> None:
    """Expire every pending schedule so the next claim picks the rows up."""
    table = harness.tables.outbox
    past = datetime.now(UTC) - timedelta(seconds=1)
    with harness.session_factory() as session:
        session.execute(
            update(table)
            .where(table.c.status.notin_([s.value for s in TERMINAL]))
            .values(next_attempt_at=past)
        )
        session.commit()


TERMINAL = (OutboxStatus.SUCCEEDED, OutboxStatus.ABANDONED)


def events_of(harness: RunnerHarness, event_type: AuditEventType) -> list[dict[str, object]]:
    return [dict(e.payload) for e in harness.sink.events if e.event_type is event_type]


def test_happy_path_succeeds_audits_and_completes_per_subject(harness: RunnerHarness) -> None:
    resolver = ScriptedResolver("stripe")
    harness.registry.register(resolver)
    seed(harness, entry(1), entry(2), entry(3, subject_id="2"))

    assert asyncio.run(runner(harness).run_once()) == 3

    assert all(
        row["status"] == OutboxStatus.SUCCEEDED.value for row in rows_by_id(harness).values()
    )
    assert len(resolver.calls) == 3
    succeeded = events_of(harness, AuditEventType.ERASURE_STEP_SUCCEEDED)
    assert (
        succeeded
        == [
            {
                "target": "stripe",
                "strategy": "delete",
                "external": True,
                "already_absent": False,
                "attempts": 1,
            }
        ]
        * 3
    )
    completed = [e for e in harness.sink.events if e.event_type is AuditEventType.ERASURE_COMPLETED]
    assert sorted(e.subject_ref for e in completed) == ["1", "2"]
    assert all(e.payload == {} for e in completed)
    assert asyncio.run(runner(harness).run_once()) == 0


def test_already_gone_is_success(harness: RunnerHarness) -> None:
    """Fault injection: a duplicate erase converges, never errors."""
    harness.registry.register(
        ScriptedResolver("stripe", (ResolverErasure(resolver="stripe", already_absent=True),))
    )
    seed(harness, entry(1))
    assert asyncio.run(runner(harness).run_once()) == 1
    assert rows_by_id(harness)[UUID(int=1)]["status"] == OutboxStatus.SUCCEEDED.value
    (payload,) = events_of(harness, AuditEventType.ERASURE_STEP_SUCCEEDED)
    assert payload["already_absent"] is True


def test_transient_failure_schedules_a_backoff_retry(harness: RunnerHarness) -> None:
    """Fault injection: a timeout retries with backoff and is not audited."""
    resolver = ScriptedResolver("stripe", (TimeoutError("provider timeout"),))
    harness.registry.register(resolver)
    seed(harness, entry(1))

    before = datetime.now(UTC)
    assert asyncio.run(runner(harness).run_once()) == 1
    row = rows_by_id(harness)[UUID(int=1)]
    assert row["status"] == OutboxStatus.FAILED.value
    assert row["last_error"] == "TimeoutError"
    scheduled = row["next_attempt_at"]
    assert isinstance(scheduled, datetime)
    expected = before + BACKOFF.delay(1)
    assert abs(scheduled.replace(tzinfo=UTC) - expected) < timedelta(seconds=5)
    assert harness.sink.events == []  # transient failures are not audited

    assert asyncio.run(runner(harness).run_once()) == 0  # not due yet
    make_due(harness)
    assert asyncio.run(runner(harness).run_once()) == 1  # retried, script exhausted -> success
    assert rows_by_id(harness)[UUID(int=1)]["status"] == OutboxStatus.SUCCEEDED.value
    assert len(resolver.calls) == 2
    (payload,) = events_of(harness, AuditEventType.ERASURE_STEP_SUCCEEDED)
    assert payload["attempts"] == 2


def test_non_retryable_resolver_error_abandons_immediately(harness: RunnerHarness) -> None:
    """Fault injection: ResolverError is terminal — audited, never retried."""
    resolver = ScriptedResolver("stripe", (ResolverError("subject is contractually locked"),))
    harness.registry.register(resolver)
    seed(harness, entry(1))

    assert asyncio.run(runner(harness).run_once()) == 1
    assert rows_by_id(harness)[UUID(int=1)]["status"] == OutboxStatus.ABANDONED.value
    (payload,) = events_of(harness, AuditEventType.ERASURE_STEP_FAILED)
    assert payload == {
        "target": "stripe",
        "strategy": "delete",
        "external": True,
        "error": "ResolverError",
        "attempts": 1,
        "abandoned": True,
    }
    make_due(harness)  # even if somehow rescheduled...
    assert asyncio.run(runner(harness).run_once()) == 0  # ...terminal rows are never reclaimed
    assert len(resolver.calls) == 1


def test_exhausted_retries_abandon_loudly(harness: RunnerHarness) -> None:
    resolver = ScriptedResolver("stripe", tuple(TimeoutError() for _ in range(10)))
    harness.registry.register(resolver)
    seed(harness, entry(1))

    saga = runner(harness, max_attempts=3)
    for _ in range(3):
        make_due(harness)
        asyncio.run(saga.run_once())

    row = rows_by_id(harness)[UUID(int=1)]
    assert row["status"] == OutboxStatus.ABANDONED.value
    assert row["last_error"] == "TimeoutError"
    assert len(resolver.calls) == 3
    (payload,) = events_of(harness, AuditEventType.ERASURE_STEP_FAILED)
    assert payload["attempts"] == 3
    assert payload["abandoned"] is True
    assert events_of(harness, AuditEventType.ERASURE_COMPLETED) == []


def test_unknown_resolver_abandons_instead_of_wedging_the_queue(harness: RunnerHarness) -> None:
    harness.registry.register(ScriptedResolver("stripe"))
    seed(harness, entry(1, resolver="ghost"))
    assert asyncio.run(runner(harness).run_once()) == 1
    assert rows_by_id(harness)[UUID(int=1)]["status"] == OutboxStatus.ABANDONED.value
    (payload,) = events_of(harness, AuditEventType.ERASURE_STEP_FAILED)
    assert payload["error"] == "ResolverError"


def test_completion_waits_for_the_subjects_last_entry(harness: RunnerHarness) -> None:
    resolver = ScriptedResolver("stripe", (TimeoutError(),))
    harness.registry.register(resolver)
    seed(harness, entry(1), entry(2))  # same subject; entry 1 fails first

    asyncio.run(runner(harness).run_once())
    assert events_of(harness, AuditEventType.ERASURE_COMPLETED) == []

    make_due(harness)
    asyncio.run(runner(harness).run_once())
    completed = events_of(harness, AuditEventType.ERASURE_COMPLETED)
    assert completed == [{}]


def test_an_abandoned_sibling_blocks_completion_forever(harness: RunnerHarness) -> None:
    """ADR 0010: ERASURE_COMPLETED requires every entry SUCCEEDED."""
    harness.registry.register(ScriptedResolver("stripe", (ResolverError("locked"),)))
    seed(harness, entry(1), entry(2))

    asyncio.run(runner(harness).run_once())
    rows = rows_by_id(harness)
    assert rows[UUID(int=1)]["status"] == OutboxStatus.ABANDONED.value
    assert rows[UUID(int=2)]["status"] == OutboxStatus.SUCCEEDED.value
    assert events_of(harness, AuditEventType.ERASURE_COMPLETED) == []

    seed(harness, entry(3))  # a re-run enqueues fresh work for the same subject
    asyncio.run(runner(harness).run_once())
    assert events_of(harness, AuditEventType.ERASURE_COMPLETED) == []


def test_empty_outbox_is_a_quiet_no_op(harness: RunnerHarness) -> None:
    assert asyncio.run(runner(harness).run_once()) == 0
    assert harness.sink.events == []
