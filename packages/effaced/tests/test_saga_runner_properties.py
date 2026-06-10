"""Property: executing an outbox entry N times converges to executing it once."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from uuid import UUID, uuid4

import pytest
from conftest import RecordingAuditSink
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import MetaData, create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    AuditEventType,
    EffacedTables,
    Outbox,
    OutboxEntry,
    OutboxStatus,
    ResolverErasure,
    ResolverExport,
    ResolverRegistry,
    SagaRunner,
    SubjectRef,
    bind_tables,
)

pytestmark = pytest.mark.property


class StatefulResolver:
    """A fake external system: a set of records that erasure removes.

    The second erase of the same value finds nothing and reports
    ``already_absent=True`` — the idempotency contract real resolvers
    must honour.
    """

    def __init__(self, name: str, records: set[str]) -> None:
        self._name = name
        self.records = records
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        raise NotImplementedError

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        self.calls.append(ref.value)
        if ref.value in self.records:
            self.records.remove(ref.value)
            return ResolverErasure(resolver=self._name)
        return ResolverErasure(resolver=self._name, already_absent=True)


class World(NamedTuple):
    """One isolated database + runner + fake external system."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    outbox: Outbox
    sink: RecordingAuditSink
    resolver: StatefulResolver
    saga: SagaRunner


def build_world(records: set[str]) -> World:
    """A fresh in-memory world per hypothesis example (no shared fixtures)."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    outbox = Outbox(session_factory, tables.outbox)
    sink = RecordingAuditSink()
    resolver = StatefulResolver("stripe", records)
    registry = ResolverRegistry()
    registry.register(resolver)
    saga = SagaRunner(registry, outbox, sink)
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
    world = build_world(records={"cus_target", "cus_bystander"})
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
    world = build_world(records={v for refs in values.values() for v in refs})
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
