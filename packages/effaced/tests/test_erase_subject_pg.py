"""erase_subject() against a real Postgres — atomicity, retention, idempotency."""

from __future__ import annotations

from collections.abc import Iterator
from typing import NamedTuple

import pytest
from conftest import Base, Comment, FakeResolver, seed_two_subjects
from sqlalchemy import Engine, MetaData, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from effaced import (
    AuditEventType,
    DatabaseAuditSink,
    EffacedTables,
    ErasurePlanner,
    ErasureStep,
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

pytestmark = pytest.mark.integration

REFS = (
    SubjectRef(kind="crm", value="crm-1"),
    SubjectRef(kind="stripe", value="cus_1"),
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


class PgHarness(NamedTuple):
    """A fully wired planner over the seeded schema on Postgres."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    sink: DatabaseAuditSink
    planner: ErasurePlanner
    executor: ErasureExecutor
    outbox: Outbox


@pytest.fixture()
def harness(pg_engine: Engine) -> Iterator[PgHarness]:
    effaced_metadata = MetaData()
    tables = bind_tables(effaced_metadata)
    Base.metadata.create_all(pg_engine)
    effaced_metadata.create_all(pg_engine)
    try:
        session_factory = sessionmaker(pg_engine)
        with session_factory() as session:
            seed_two_subjects(session)
            session.commit()
        sink = DatabaseAuditSink(session_factory, tables.audit_events)
        registry = ResolverRegistry()
        registry.register(FakeResolver("crm"))
        registry.register(FakeResolver("stripe"))
        data_map = collect_data_map(Base.metadata)
        executor = ErasureExecutor(Base.metadata)
        outbox = Outbox(session_factory, tables.outbox)
        planner = ErasurePlanner(
            data_map,
            resolve_subject_graph(data_map, Base.registry),
            registry,
            executor=executor,
            outbox=outbox,
            audit_sink=sink,
        )
        yield PgHarness(session_factory, tables, sink, planner, executor, outbox)
    finally:
        effaced_metadata.drop_all(pg_engine)
        Base.metadata.drop_all(pg_engine)


def count_rows(session: Session, name: str) -> int:
    return len(session.execute(select(Base.metadata.tables[name])).all())


def outbox_rows(harness: PgHarness, session: Session) -> list[dict[str, object]]:
    return [dict(row) for row in session.execute(select(harness.tables.outbox)).mappings()]


def test_killed_transaction_rolls_back_rows_and_outbox_together(harness: PgHarness) -> None:
    """Acceptance (a): the local phase and the enqueued entries are one unit."""
    with harness.session_factory() as session:
        harness.planner.erase_subject(session, "1", refs=REFS)
        assert count_rows(session, "comments") == 1
        assert len(outbox_rows(harness, session)) == 2
        session.rollback()  # the transaction dies before commit
    with harness.session_factory() as session:
        assert count_rows(session, "comments") == 3
        assert count_rows(session, "orders") == 2
        assert count_rows(session, "order_items") == 2
        users = session.execute(select(Base.metadata.tables["users"])).mappings().all()
        assert {row["email"] for row in users} == {"alice@example.com", "bob@example.com"}
        assert outbox_rows(harness, session) == []
    # The attempt stays recorded: the sink commits independently.
    events = [event.event_type for event in harness.sink.read("1")]
    assert events[0] == AuditEventType.ERASURE_REQUESTED
    assert events[-1] == AuditEventType.ERASURE_LOCAL_COMPLETED


def test_partial_deletion_failure_rolls_back_and_audits(harness: PgHarness) -> None:
    """A fault after real deletions leaves the data intact and the trail loud."""
    data_map = collect_data_map(Base.metadata)
    failing = ErasurePlanner(
        data_map,
        resolve_subject_graph(data_map, Base.registry),
        executor=FailingExecutor(harness.executor, fail_at="users"),
        outbox=harness.outbox,
        audit_sink=harness.sink,
    )
    with harness.session_factory() as session:
        with pytest.raises(RuntimeError, match="injected fault"):
            failing.erase_subject(session, "1")
        assert count_rows(session, "comments") == 1  # partial deletion happened
        session.rollback()
    with harness.session_factory() as session:
        assert count_rows(session, "comments") == 3
        assert count_rows(session, "orders") == 2
        assert outbox_rows(harness, session) == []
    events = harness.sink.read("1")
    types = [event.event_type for event in events]
    assert types == [
        AuditEventType.ERASURE_REQUESTED,
        *[AuditEventType.ERASURE_STEP_SUCCEEDED] * 4,
        AuditEventType.ERASURE_STEP_FAILED,
    ]
    assert events[-1].payload == {
        "target": "users",
        "strategy": "anonymize",
        "error": "RuntimeError",
    }


def test_cross_subject_comment_parent_fails_loudly(harness: PgHarness) -> None:
    """ADR 0007: an FK into the deletion set from outside the subject path
    surfaces as the database's integrity error, never as silent data loss."""
    with harness.session_factory() as session:
        session.add(Comment(id=99, user_id=2, parent_id=1))  # B replies to A
        session.commit()
    with harness.session_factory() as session:
        with pytest.raises(IntegrityError):
            harness.planner.erase_subject(session, "1", refs=REFS)
        session.rollback()
    with harness.session_factory() as session:
        assert count_rows(session, "comments") == 4
    failed = harness.sink.read("1")[-1]
    assert failed.event_type == AuditEventType.ERASURE_STEP_FAILED
    assert failed.payload["error"] == "IntegrityError"


def test_committed_erasure_retains_invoice_and_reports_counts(harness: PgHarness) -> None:
    """Acceptance (b): the retained invoice survives untouched, counts correct."""
    with harness.session_factory() as session:
        result = harness.planner.erase_subject(session, "1", refs=REFS)
        session.commit()
    assert result.deleted == {"comments": 2, "order_items": 1, "orders": 1}
    assert result.anonymized == {"users": 1}
    assert result.retained == {"invoices": 1}
    assert result.enqueued_external == ("crm", "stripe")
    with harness.session_factory() as session:
        invoices = session.execute(select(Base.metadata.tables["invoices"])).mappings().all()
        assert {(row["id"], row["billing_address"]) for row in invoices} == {
            (1, "1 Alice Street"),
            (2, "2 Bob Street"),
        }
        users = {
            row["id"]: dict(row)
            for row in session.execute(select(Base.metadata.tables["users"])).mappings()
        }
        assert users[1]["email"] != "alice@example.com"
        assert users[2]["email"] == "bob@example.com"
        rows = outbox_rows(harness, session)
        assert len(rows) == 2
        assert all(row["status"] == "pending" for row in rows)
    types = [event.event_type for event in harness.sink.read("1")]
    assert types == [
        AuditEventType.ERASURE_REQUESTED,
        *[AuditEventType.ERASURE_STEP_SUCCEEDED] * 5,
        AuditEventType.ERASURE_LOCAL_COMPLETED,
    ]


def test_rerun_for_an_erased_subject_is_a_no_op_success(harness: PgHarness) -> None:
    """Acceptance (d): nothing further to delete; external work re-enqueues."""
    with harness.session_factory() as session:
        harness.planner.erase_subject(session, "1", refs=REFS)
        session.commit()
    with harness.session_factory() as session:
        rerun = harness.planner.erase_subject(session, "1", refs=REFS)
        session.commit()
    assert rerun.deleted == {"comments": 0, "order_items": 0, "orders": 0}
    assert rerun.anonymized == {"users": 1}
    assert rerun.retained == {"invoices": 1}
    with harness.session_factory() as session:
        rows = outbox_rows(harness, session)
        assert len(rows) == 4
        assert len({row["entry_id"] for row in rows}) == 4
