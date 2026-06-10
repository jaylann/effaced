"""erase_subject() — atomic local phase, durable enqueue, audited outcomes."""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple
from uuid import UUID

import pytest
from conftest import Base, FakeResolver, RecordingAuditSink, seed_two_subjects
from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    AuditEventType,
    ConfigurationError,
    EffacedTables,
    ErasurePlanner,
    Outbox,
    ResolverRegistry,
    StepExecutor,
    SubjectGraph,
    SubjectRef,
    bind_tables,
    collect_data_map,
    resolve_subject_graph,
)
from effaced.adapters.sqlalchemy import ErasureExecutor
from effaced.erasure import ErasureStep

REFS = (
    SubjectRef(kind="stripe_customer", value="cus_1"),
    SubjectRef(kind="email", value="ref-7"),
)


class FailingExecutor:
    """Delegates to a real executor until it reaches the named table."""

    def __init__(self, inner: StepExecutor, fail_at: str) -> None:
        self._inner = inner
        self._fail_at = fail_at

    def execute(
        self, session: Session, graph: SubjectGraph, step: ErasureStep, subject_id: str
    ) -> int:
        if step.target == self._fail_at:
            msg = "injected fault"
            raise RuntimeError(msg)
        return self._inner.execute(session, graph, step, subject_id)


class ExplodingOutbox(Outbox):
    """An outbox whose enqueue always fails."""

    def enqueue(self, session: Session, entries: Sequence[object]) -> None:
        msg = "outbox down"
        raise RuntimeError(msg)


class ExplodingSink(RecordingAuditSink):
    """A sink that refuses every append."""

    def append(self, event: object) -> None:
        msg = "sink down"
        raise RuntimeError(msg)


class Harness(NamedTuple):
    """A fully wired planner over the seeded two-subject schema."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    sink: RecordingAuditSink
    planner: ErasurePlanner
    executor: ErasureExecutor
    outbox: Outbox


def build_harness(
    *,
    executor: StepExecutor | None = None,
    outbox_cls: type[Outbox] = Outbox,
    sink: RecordingAuditSink | None = None,
    resolvers: tuple[str, ...] = ("crm", "stripe"),
) -> Harness:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    effaced_metadata = MetaData()
    tables = bind_tables(effaced_metadata)
    Base.metadata.create_all(engine)
    effaced_metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    with session_factory() as session:
        seed_two_subjects(session)
        session.commit()
    registry = ResolverRegistry()
    for name in resolvers:
        registry.register(FakeResolver(name))
    data_map = collect_data_map(Base.metadata)
    real_executor = ErasureExecutor(Base.metadata)
    outbox = outbox_cls(session_factory, tables.outbox)
    recording = sink if sink is not None else RecordingAuditSink()
    planner = ErasurePlanner(
        data_map,
        resolve_subject_graph(data_map, Base.registry),
        registry,
        executor=executor if executor is not None else real_executor,
        outbox=outbox,
        audit_sink=recording,
    )
    return Harness(session_factory, tables, recording, planner, real_executor, outbox)


@pytest.fixture()
def harness() -> Harness:
    return build_harness()


def table_rows(session: Session, name: str) -> list[dict[str, object]]:
    return [dict(row) for row in session.execute(select(Base.metadata.tables[name])).mappings()]


def outbox_rows(harness: Harness, session: Session) -> list[dict[str, object]]:
    return [dict(row) for row in session.execute(select(harness.tables.outbox)).mappings()]


def event_types(harness: Harness) -> list[AuditEventType]:
    return [event.event_type for event in harness.sink.events]


def test_happy_path_counts_and_retained_invoice(harness: Harness) -> None:
    with harness.session_factory() as session:
        result = harness.planner.erase_subject(session, "1", refs=REFS)
        session.commit()
    assert result.subject_id == "1"
    assert result.deleted == {"comments": 2, "order_items": 2, "orders": 2}
    assert result.anonymized == {"users": 1}
    assert result.retained == {"invoices": 1}
    assert result.enqueued_external == ("crm", "stripe")
    assert result.completed_at.tzinfo is not None
    with harness.session_factory() as session:
        ada, bob = table_rows(session, "users")
        assert ada["email"] != "ada@example.com"
        assert ada["theme"] == "dark"
        assert bob["email"] == "bob@example.com"
        invoices = table_rows(session, "invoices")
        assert {"id": 1, "user_id": 1, "billing_address": "1 Ada Lane"} in invoices


def test_no_cross_subject_bleed(harness: Harness) -> None:
    with harness.session_factory() as session:
        before = {
            name: [row for row in table_rows(session, name) if row.get("user_id") == 2]
            for name in ("invoices", "orders", "comments")
        }
        harness.planner.erase_subject(session, "1", refs=REFS)
        session.commit()
    with harness.session_factory() as session:
        for name, rows in before.items():
            assert [row for row in table_rows(session, name) if row.get("user_id") == 2] == rows
        assert [row["id"] for row in table_rows(session, "order_items")] == [3]


def test_audit_sequence_and_payloads(harness: Harness) -> None:
    with harness.session_factory() as session:
        harness.planner.erase_subject(session, "1", refs=REFS)
        session.commit()
    events = harness.sink.events
    assert event_types(harness) == [
        AuditEventType.ERASURE_REQUESTED,
        *[AuditEventType.ERASURE_STEP_SUCCEEDED] * 5,
        AuditEventType.ERASURE_LOCAL_COMPLETED,
    ]
    assert all(event.subject_ref == "1" for event in events)
    assert events[0].payload == {"local_steps": 5, "external_steps": 2, "refs": 2}
    succeeded = [event.payload for event in events[1:6]]
    assert {"target": "invoices", "strategy": "retain", "rows": 1} in succeeded
    assert {"target": "users", "strategy": "anonymize", "rows": 1} in succeeded
    assert {"target": "comments", "strategy": "delete", "rows": 2} in succeeded
    assert events[-1].payload == {"deleted": 6, "anonymized": 1, "retained": 1, "enqueued": 4}


def test_outbox_fan_out_is_resolver_times_ref(harness: Harness) -> None:
    with harness.session_factory() as session:
        harness.planner.erase_subject(session, "1", refs=REFS)
        rows = outbox_rows(harness, session)
        assert len(rows) == 4
        assert {(row["resolver"], row["ref_value"]) for row in rows} == {
            ("crm", "cus_1"),
            ("crm", "ref-7"),
            ("stripe", "cus_1"),
            ("stripe", "ref-7"),
        }
        assert all(row["status"] == "pending" for row in rows)
        assert len({row["entry_id"] for row in rows}) == 4
        session.commit()


def test_rollback_discards_rows_and_outbox_but_keeps_audit(harness: Harness) -> None:
    with harness.session_factory() as session:
        harness.planner.erase_subject(session, "1", refs=REFS)
        session.rollback()
    with harness.session_factory() as session:
        users = table_rows(session, "users")
        assert len(users) == 2
        assert users[0]["email"] == "ada@example.com"
        assert len(table_rows(session, "comments")) == 3
        assert outbox_rows(harness, session) == []
    assert AuditEventType.ERASURE_LOCAL_COMPLETED in event_types(harness)


def test_mid_stream_failure_is_audited_and_propagates(harness: Harness) -> None:
    data_map = collect_data_map(Base.metadata)
    failing = ErasurePlanner(
        data_map,
        resolve_subject_graph(data_map, Base.registry),
        executor=FailingExecutor(harness.executor, fail_at="orders"),
        outbox=harness.outbox,
        audit_sink=harness.sink,
    )
    with harness.session_factory() as session:
        with pytest.raises(RuntimeError, match="injected fault"):
            failing.erase_subject(session, "1")
        session.rollback()
    assert event_types(harness)[-1] == AuditEventType.ERASURE_STEP_FAILED
    assert harness.sink.events[-1].payload == {
        "target": "orders",
        "strategy": "delete",
        "error": "RuntimeError",
    }
    with harness.session_factory() as session:
        assert len(table_rows(session, "comments")) == 3
        assert len(table_rows(session, "order_items")) == 3
        assert outbox_rows(harness, session) == []


def test_enqueue_failure_is_audited_as_outbox_step() -> None:
    harness = build_harness(outbox_cls=ExplodingOutbox, resolvers=("crm",))
    with harness.session_factory() as session:
        with pytest.raises(RuntimeError, match="outbox down"):
            harness.planner.erase_subject(session, "1", refs=REFS[:1])
        session.rollback()
    assert harness.sink.events[-1].event_type == AuditEventType.ERASURE_STEP_FAILED
    assert harness.sink.events[-1].payload == {
        "target": "outbox",
        "strategy": "enqueue",
        "error": "RuntimeError",
    }


def test_unwired_planner_refuses_loudly() -> None:
    data_map = collect_data_map(Base.metadata)
    planner = ErasurePlanner(data_map, resolve_subject_graph(data_map, Base.registry))
    engine = create_engine("sqlite://", poolclass=StaticPool)
    with (
        sessionmaker(engine)() as session,
        pytest.raises(ConfigurationError, match=r"executor.*outbox.*audit_sink"),
    ):
        planner.erase_subject(session, "1")
    engine.dispose()


def test_external_steps_without_refs_refuse_before_any_event(harness: Harness) -> None:
    with harness.session_factory() as session, pytest.raises(ConfigurationError, match="refs"):
        harness.planner.erase_subject(session, "1")
    assert harness.sink.events == []
    with harness.session_factory() as session:
        assert len(table_rows(session, "comments")) == 3


def test_failing_sink_stops_the_erasure_before_any_row_changes() -> None:
    harness = build_harness(sink=ExplodingSink(), resolvers=())
    with harness.session_factory() as session:
        with pytest.raises(RuntimeError, match="sink down"):
            harness.planner.erase_subject(session, "1")
        session.rollback()
    with harness.session_factory() as session:
        assert len(table_rows(session, "comments")) == 3
        assert table_rows(session, "users")[0]["email"] == "ada@example.com"


def test_rerun_for_an_erased_subject_is_a_no_op_success(harness: Harness) -> None:
    """Deleted tables stay empty; the surviving (anonymized/retained) rows
    re-match by subject id and are reported again; external work is
    re-enqueued with fresh idempotency keys (resolvers converge on it)."""
    with harness.session_factory() as session:
        harness.planner.erase_subject(session, "1", refs=REFS)
        session.commit()
    with harness.session_factory() as session:
        first_anonymized = table_rows(session, "users")[0]
        rerun = harness.planner.erase_subject(session, "1", refs=REFS)
        session.commit()
    assert rerun.deleted == {"comments": 0, "order_items": 0, "orders": 0}
    assert rerun.anonymized == {"users": 1}
    assert rerun.retained == {"invoices": 1}
    assert rerun.enqueued_external == ("crm", "stripe")
    with harness.session_factory() as session:
        rows = outbox_rows(harness, session)
        assert len(rows) == 8
        assert isinstance(first_anonymized["id"], int)
        assert len({UUID(str(row["entry_id"])) for row in rows}) == 8
