"""Fault injection across the whole pipeline: erase_subject → outbox → saga.

Each test is one cell of the proof suite's fault matrix (see PROOFS.md):
a failure is injected at a specific seam and the resulting state — rows,
outbox, audit trail — is asserted against ADR 0009/0010.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

import pytest
from conftest import (
    Base,
    FailingExecutor,
    RecordingAuditSink,
    StatefulResolver,
    seed_two_subjects,
)
from sqlalchemy import MetaData, create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    AuditEvent,
    AuditEventType,
    BackoffPolicy,
    EffacedTables,
    ErasurePlanner,
    Outbox,
    OutboxStatus,
    ResolverErasure,
    ResolverError,
    ResolverRegistry,
    SagaRunner,
    StepExecutor,
    SubjectRef,
    bind_tables,
    collect_data_map,
    resolve_subject_graph,
)
from effaced.adapters.sqlalchemy import ErasureExecutor

REFS = (SubjectRef(kind="stripe", value="cus_1"),)


class FlakyOnceSystem(StatefulResolver):
    """The first call hits a transient outage; later calls behave normally."""

    def __init__(self, records: set[str]) -> None:
        super().__init__("stripe", records)
        self._failed = False

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        if not self._failed:
            self._failed = True
            msg = "transient outage"
            raise TimeoutError(msg)
        return await super().erase_subject(ref)


class TerminalSystem(StatefulResolver):
    """Every call fails non-retryably (the resolver's ResolverError contract)."""

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        self.calls.append(ref.value)
        msg = "subject locked by provider"
        raise ResolverError(msg)


class OutageSink(RecordingAuditSink):
    """A sink that can be taken down and brought back mid-test."""

    def __init__(self) -> None:
        super().__init__()
        self.down = False

    def append(self, event: AuditEvent) -> None:
        if self.down:
            msg = "sink down"
            raise RuntimeError(msg)
        super().append(event)


class Pipeline(NamedTuple):
    """The full machine: seeded schema, planner, outbox, saga runner, trail."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    sink: RecordingAuditSink
    planner: ErasurePlanner
    saga: SagaRunner


def build_pipeline(
    resolver: StatefulResolver,
    *,
    sink: RecordingAuditSink | None = None,
    executor: StepExecutor | None = None,
    max_attempts: int = 3,
) -> Pipeline:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    effaced_metadata = MetaData()
    tables = bind_tables(effaced_metadata)
    Base.metadata.create_all(engine)
    effaced_metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    with session_factory() as session:
        seed_two_subjects(session)
    registry = ResolverRegistry()
    registry.register(resolver)
    data_map = collect_data_map(Base.metadata)
    outbox = Outbox(session_factory, tables.outbox)
    recording = sink if sink is not None else RecordingAuditSink()
    planner = ErasurePlanner(
        data_map,
        resolve_subject_graph(data_map, Base.registry),
        registry,
        executor=executor if executor is not None else ErasureExecutor(Base.metadata),
        outbox=outbox,
        audit_sink=recording,
    )
    saga = SagaRunner(
        registry,
        outbox,
        recording,
        max_attempts=max_attempts,
        backoff=BackoffPolicy(
            base_delay=timedelta(microseconds=1),
            max_delay=timedelta(microseconds=2),
            lease=timedelta(minutes=5),
        ),
    )
    return Pipeline(session_factory, tables, recording, planner, saga)


def database_snapshot(session: Session) -> dict[str, list[dict[str, object]]]:
    """Every application table's rows, ordered — the byte-identity baseline."""
    return {
        name: [
            dict(row)
            for row in session.execute(
                select(table).order_by(*table.primary_key.columns)
            ).mappings()
        ]
        for name, table in Base.metadata.tables.items()
    }


def entry_statuses(pipeline: Pipeline) -> list[str]:
    with pipeline.session_factory() as session:
        rows = session.execute(select(pipeline.tables.outbox)).mappings()
        return [str(row["status"]) for row in rows]


def events_of(pipeline: Pipeline, event_type: AuditEventType) -> list[AuditEvent]:
    return [event for event in pipeline.sink.events if event.event_type is event_type]


def expire_lease(pipeline: Pipeline) -> None:
    """Rewind the claim lease, as if the holding runner died and time passed."""
    table = pipeline.tables.outbox
    with pipeline.session_factory() as session:
        session.execute(
            update(table).values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.commit()


def drain(pipeline: Pipeline) -> None:
    rounds = 0
    while asyncio.run(pipeline.saga.run_once()):
        rounds += 1
        assert rounds <= 10


def test_failure_before_local_commit_leaves_no_partial_erasure() -> None:
    """A local step fails, the caller rolls back: nothing happened anywhere.

    Rows are byte-identical, no outbox entry survives for the saga to find,
    the resolver is never called, and the trail records the attempt and the
    failure — REQUESTED, the successful steps, then STEP_FAILED (ADR 0009).
    """
    resolver = StatefulResolver("stripe", {"cus_1"})
    pipeline = build_pipeline(
        resolver, executor=FailingExecutor(ErasureExecutor(Base.metadata), fail_at="orders")
    )
    with pipeline.session_factory() as session:
        before = database_snapshot(session)
    with pipeline.session_factory() as session:
        with pytest.raises(RuntimeError, match="injected fault"):
            pipeline.planner.erase_subject(session, "1", refs=REFS)
        session.rollback()
    with pipeline.session_factory() as session:
        assert database_snapshot(session) == before
    assert entry_statuses(pipeline) == []
    assert asyncio.run(pipeline.saga.run_once()) == 0
    assert resolver.calls == []
    types = [event.event_type for event in pipeline.sink.events]
    assert types[0] is AuditEventType.ERASURE_REQUESTED
    assert types[-1] is AuditEventType.ERASURE_STEP_FAILED
    assert set(types[1:-1]) <= {AuditEventType.ERASURE_STEP_SUCCEEDED}
    assert not events_of(pipeline, AuditEventType.ERASURE_LOCAL_COMPLETED)
    assert not events_of(pipeline, AuditEventType.ERASURE_COMPLETED)


def test_transient_external_failure_after_commit_converges_to_completed() -> None:
    """A transient resolver outage retries on backoff and converges, unaudited.

    The entry lands SUCCEEDED on the second claim (attempts counts claims,
    ADR 0010), the outage itself leaves no audit event, and the subject's
    ERASURE_COMPLETED fires exactly once.
    """
    resolver = FlakyOnceSystem({"cus_1"})
    pipeline = build_pipeline(resolver)
    with pipeline.session_factory() as session:
        pipeline.planner.erase_subject(session, "1", refs=REFS)
        session.commit()
    drain(pipeline)
    assert resolver.records == set()
    assert entry_statuses(pipeline) == [OutboxStatus.SUCCEEDED.value]
    assert not events_of(pipeline, AuditEventType.ERASURE_STEP_FAILED)
    (succeeded,) = (
        event
        for event in events_of(pipeline, AuditEventType.ERASURE_STEP_SUCCEEDED)
        if event.payload.get("external") is True
    )
    assert succeeded.payload["attempts"] == 2
    (completed,) = events_of(pipeline, AuditEventType.ERASURE_COMPLETED)
    assert completed.subject_ref == "1"


def test_terminal_external_failure_after_commit_keeps_local_erasure() -> None:
    """A non-retryable resolver failure abandons loudly — and compensates nothing.

    The local erasure stays committed (there is no un-erase), the entry is
    ABANDONED with an audited ``abandoned: true``, the subject's
    ERASURE_COMPLETED never fires, and the abandoned entry is never
    reclaimed.
    """
    resolver = TerminalSystem("stripe", set())
    pipeline = build_pipeline(resolver)
    with pipeline.session_factory() as session:
        pipeline.planner.erase_subject(session, "1", refs=REFS)
        session.commit()
    assert asyncio.run(pipeline.saga.run_once()) == 1
    assert entry_statuses(pipeline) == [OutboxStatus.ABANDONED.value]
    (failed,) = events_of(pipeline, AuditEventType.ERASURE_STEP_FAILED)
    assert failed.payload["abandoned"] is True
    assert failed.payload["error"] == "ResolverError"
    assert not events_of(pipeline, AuditEventType.ERASURE_COMPLETED)
    assert asyncio.run(pipeline.saga.run_once()) == 0
    with pipeline.session_factory() as session:
        rows = database_snapshot(session)
    assert rows["users"][0]["email"] != "alice@example.com"
    assert all(row["user_id"] != 1 for row in rows["orders"])
    assert all(row["user_id"] != 1 for row in rows["comments"])
    assert {"id": 1, "user_id": 1, "billing_address": "1 Alice Street"} in rows["invoices"]


def test_sink_outage_during_settle_keeps_entry_in_flight_and_heals() -> None:
    """No outcome is ever recorded without its audit record (ADR 0010).

    The resolver call succeeds but the sink is down when the runner books
    the outcome: the append precedes the status change, so the entry stays
    IN_FLIGHT and nothing claims success. Once the sink heals and the lease
    expires, the retry converges — ``already_absent`` absorbs the duplicate
    call — and both the success and the completion are recorded.
    """
    resolver = StatefulResolver("stripe", {"cus_1"})
    sink = OutageSink()
    pipeline = build_pipeline(resolver, sink=sink)
    with pipeline.session_factory() as session:
        pipeline.planner.erase_subject(session, "1", refs=REFS)
        session.commit()
    events_before = len(sink.events)
    sink.down = True
    with pytest.raises(RuntimeError, match="sink down"):
        asyncio.run(pipeline.saga.run_once())
    assert resolver.calls == ["cus_1"]
    assert entry_statuses(pipeline) == [OutboxStatus.IN_FLIGHT.value]
    assert len(sink.events) == events_before
    sink.down = False
    expire_lease(pipeline)
    assert asyncio.run(pipeline.saga.run_once()) == 1
    assert entry_statuses(pipeline) == [OutboxStatus.SUCCEEDED.value]
    (succeeded,) = (
        event
        for event in events_of(pipeline, AuditEventType.ERASURE_STEP_SUCCEEDED)
        if event.payload.get("external") is True
    )
    assert succeeded.payload["already_absent"] is True
    (completed,) = events_of(pipeline, AuditEventType.ERASURE_COMPLETED)
    assert completed.subject_ref == "1"
