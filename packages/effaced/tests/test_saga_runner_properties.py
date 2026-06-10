"""Properties: re-execution converges to once; any failure script converges."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from uuid import UUID, uuid4

import pytest
from conftest import RecordingAuditSink, StatefulResolver
from hypothesis import given
from hypothesis import strategies as st
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
    ResolverRegistry,
    SagaRunner,
    SubjectRef,
    bind_tables,
)

pytestmark = pytest.mark.property


class ScriptedFlakyResolver(StatefulResolver):
    """Fails a scripted number of calls per ref, then erases like a real system."""

    def __init__(self, name: str, failures: dict[str, int]) -> None:
        super().__init__(name, set(failures))
        self.remaining = dict(failures)

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        if self.remaining[ref.value] > 0:
            self.remaining[ref.value] -= 1
            msg = "transient outage"
            raise TimeoutError(msg)
        return await super().erase_subject(ref)


class World(NamedTuple):
    """One isolated database + runner + fake external system."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    outbox: Outbox
    sink: RecordingAuditSink
    resolver: StatefulResolver
    saga: SagaRunner


def build_world(
    resolver: StatefulResolver,
    *,
    max_attempts: int = 8,
    backoff: BackoffPolicy | None = None,
) -> World:
    """A fresh in-memory world per hypothesis example (no shared fixtures)."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    outbox = Outbox(session_factory, tables.outbox)
    sink = RecordingAuditSink()
    registry = ResolverRegistry()
    registry.register(resolver)
    saga = SagaRunner(registry, outbox, sink, max_attempts=max_attempts, backoff=backoff)
    return World(session_factory, tables, outbox, sink, resolver, saga)


def enqueue(world: World, *entries: OutboxEntry) -> None:
    with world.session_factory() as session:
        world.outbox.enqueue(session, list(entries))
        session.commit()


def entry(subject_id: str, value: str) -> OutboxEntry:
    return OutboxEntry(
        entry_id=uuid4(),
        subject_id=subject_id,
        resolver="stripe",
        ref=SubjectRef(kind="stripe", value=value),
        enqueued_at=datetime.now(UTC),
    )


def statuses(world: World) -> dict[UUID, str]:
    with world.session_factory() as session:
        rows = session.execute(select(world.tables.outbox)).mappings()
        return {row["entry_id"]: str(row["status"]) for row in rows}


def simulate_crash_before_bookkeeping(world: World, entry_id: UUID) -> None:
    """Rewind a settled entry to a dead runner's state: claimed, lease expired."""
    table = world.tables.outbox
    with world.session_factory() as session:
        session.execute(
            update(table)
            .where(table.c.entry_id == entry_id)
            .values(
                status=OutboxStatus.IN_FLIGHT.value,
                next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
        session.commit()


@given(executions=st.integers(min_value=1, max_value=5))
def test_executing_an_entry_n_times_converges_to_once(executions: int) -> None:
    """The entry id is the idempotency key: re-execution changes nothing."""
    world = build_world(StatefulResolver("stripe", {"cus_target", "cus_bystander"}))
    the_entry = entry("1", "cus_target")
    enqueue(world, the_entry)

    for round_number in range(executions):
        assert asyncio.run(world.saga.run_once()) == 1
        if round_number < executions - 1:
            simulate_crash_before_bookkeeping(world, the_entry.entry_id)

    # External state is exactly what a single execution produces...
    assert world.resolver.records == {"cus_bystander"}  # no bleed onto other records
    assert world.resolver.calls == ["cus_target"] * executions
    # ...the entry is terminally succeeded...
    assert statuses(world) == {the_entry.entry_id: OutboxStatus.SUCCEEDED.value}
    # ...and the trail records the outcome (duplicates allowed by design:
    # crash re-execution is at-least-once; assert state, not exact counts).
    succeeded = [
        e for e in world.sink.events if e.event_type is AuditEventType.ERASURE_STEP_SUCCEEDED
    ]
    assert len(succeeded) == executions
    assert succeeded[0].payload["already_absent"] is False
    assert all(e.payload["already_absent"] is True for e in succeeded[1:])
    completed = [e for e in world.sink.events if e.event_type is AuditEventType.ERASURE_COMPLETED]
    assert len(completed) >= 1
    assert all(e.subject_ref == "1" for e in completed)


@given(counts=st.lists(st.integers(min_value=1, max_value=4), min_size=1, max_size=3))
def test_every_subject_completes_exactly_once_and_only_for_itself(
    counts: list[int],
) -> None:
    """Draining any mix of subjects emits one ERASURE_COMPLETED per subject."""
    values = {
        f"subject-{i}": [f"cus_{i}_{j}" for j in range(count)] for i, count in enumerate(counts)
    }
    world = build_world(StatefulResolver("stripe", {v for refs in values.values() for v in refs}))
    for subject_id, refs in values.items():
        enqueue(world, *(entry(subject_id, value) for value in refs))

    while asyncio.run(world.saga.run_once()):
        pass

    assert world.resolver.records == set()
    assert all(status == OutboxStatus.SUCCEEDED.value for status in statuses(world).values())
    completed = [
        e.subject_ref for e in world.sink.events if e.event_type is AuditEventType.ERASURE_COMPLETED
    ]
    assert sorted(completed) == sorted(values)


@given(
    failures=st.lists(st.integers(min_value=0, max_value=4), min_size=1, max_size=3),
    max_attempts=st.integers(min_value=1, max_value=4),
)
def test_any_transient_failure_script_converges(failures: list[int], max_attempts: int) -> None:
    """Entries land SUCCEEDED iff their outage ends before retries run out.

    Attempts count claims (ADR 0010), so an entry whose first ``k`` calls
    fail succeeds on claim ``k + 1`` when ``k < max_attempts`` and is
    ABANDONED (loudly audited) otherwise. Transient failures in between
    leave no audit event.
    """
    script = {f"cus_{index}": count for index, count in enumerate(failures)}
    world = build_world(
        ScriptedFlakyResolver("stripe", script),
        max_attempts=max_attempts,
        backoff=BackoffPolicy(
            base_delay=timedelta(microseconds=1),
            max_delay=timedelta(microseconds=2),
            lease=timedelta(minutes=5),
        ),
    )
    entries = {value: entry(f"subject-{value}", value) for value in script}
    enqueue(world, *entries.values())

    rounds = 0
    while asyncio.run(world.saga.run_once()):
        rounds += 1
        assert rounds <= 10 * len(script) * max_attempts

    status_by_value = {value: statuses(world)[item.entry_id] for value, item in entries.items()}
    events_by_subject = {
        value: [e for e in world.sink.events if e.subject_ref == f"subject-{value}"]
        for value in script
    }
    for value, outages in script.items():
        if outages < max_attempts:
            assert status_by_value[value] == OutboxStatus.SUCCEEDED.value
            succeeded, completed = events_by_subject[value]
            assert succeeded.event_type is AuditEventType.ERASURE_STEP_SUCCEEDED
            assert succeeded.payload["attempts"] == outages + 1
            assert completed.event_type is AuditEventType.ERASURE_COMPLETED
        else:
            assert status_by_value[value] == OutboxStatus.ABANDONED.value
            (failed,) = events_by_subject[value]
            assert failed.event_type is AuditEventType.ERASURE_STEP_FAILED
            assert failed.payload["abandoned"] is True
            assert failed.payload["error"] == "TimeoutError"
