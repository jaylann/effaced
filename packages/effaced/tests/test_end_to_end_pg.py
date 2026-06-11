"""The joined pipeline on real Postgres: erase_subject -> outbox -> saga runner.

The SQLite end-to-end suite (``test_end_to_end_fault_injection.py``) proves
the wiring, but SQLite silently drops ``FOR UPDATE`` / ``SKIP LOCKED``, so
the completion check's row locking and the planner->claim handoff
(``entry_id`` shape, str-published/Integer-PK ``subject_id`` coercion, ref
serialization) are only exercised here, against a real database. The
per-layer suites cover the planner (``test_erase_subject_pg.py``) and the
runner (``test_saga_runner_pg.py``) separately, never on one wiring.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import NamedTuple

import pytest
from conftest import Base, StatefulResolver, seed_two_subjects
from sqlalchemy import Engine, MetaData, select
from sqlalchemy.orm import Session, sessionmaker

from effaced import (
    AuditEventType,
    DatabaseAuditSink,
    EffacedTables,
    ErasurePlanner,
    Outbox,
    OutboxStatus,
    ResolverRegistry,
    SagaRunner,
    SubjectRef,
    bind_tables,
    collect_data_map,
    resolve_subject_graph,
)
from effaced.adapters.sqlalchemy import ErasureExecutor

pytestmark = pytest.mark.integration

REFS = (SubjectRef(kind="stripe", value="cus_1"),)


class Pipeline(NamedTuple):
    """A planner and a saga runner sharing one outbox, sink, and registry."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    sink: DatabaseAuditSink
    planner: ErasurePlanner
    saga: SagaRunner
    resolver: StatefulResolver


@pytest.fixture()
def pipeline(pg_engine: Engine) -> Iterator[Pipeline]:
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
        resolver = StatefulResolver("stripe", {"cus_1"})
        registry = ResolverRegistry()
        registry.register(resolver)
        data_map = collect_data_map(Base.metadata)
        outbox = Outbox(session_factory, tables.outbox)
        planner = ErasurePlanner(
            data_map,
            resolve_subject_graph(data_map, Base.registry),
            registry,
            executor=ErasureExecutor(Base.metadata),
            outbox=outbox,
            audit_sink=sink,
        )
        saga = SagaRunner(registry, outbox, sink)
        yield Pipeline(session_factory, tables, sink, planner, saga, resolver)
    finally:
        effaced_metadata.drop_all(pg_engine)
        Base.metadata.drop_all(pg_engine)


def drain(saga: SagaRunner) -> None:
    """Run the saga until nothing is due — bounded against a runaway loop."""
    rounds = 0
    while asyncio.run(saga.run_once()):
        rounds += 1
        assert rounds <= 10


def statuses(pipeline: Pipeline) -> list[str]:
    with pipeline.session_factory() as session:
        rows = session.execute(select(pipeline.tables.outbox.c.status)).scalars()
        return [str(status) for status in rows]


def test_full_pipeline_converges_to_completed_on_postgres(pipeline: Pipeline) -> None:
    """The planner's entry is claimed, executed, and completed under real locking.

    The local phase commits, the saga runner drains the entry the planner
    enqueued, the external store is emptied, and exactly one
    ERASURE_COMPLETED fires — proving the handoff the SQLite suite cannot.
    """
    with pipeline.session_factory() as session:
        result = pipeline.planner.erase_subject(session, "1", refs=REFS)
        session.commit()
    assert result.enqueued_external == ("stripe",)

    drain(pipeline.saga)

    assert statuses(pipeline) == [OutboxStatus.SUCCEEDED.value]
    assert pipeline.resolver.records == set()
    completed = [
        event
        for event in pipeline.sink.read("1")
        if event.event_type is AuditEventType.ERASURE_COMPLETED
    ]
    assert len(completed) == 1
    assert completed[0].subject_ref == "1"


def test_full_pipeline_retains_invoice_and_never_touches_subject_two(
    pipeline: Pipeline,
) -> None:
    """The committed erasure honours retention and leaves subject 2 byte-identical."""
    with pipeline.session_factory() as session:
        pipeline.planner.erase_subject(session, "1", refs=REFS)
        session.commit()
    drain(pipeline.saga)

    with pipeline.session_factory() as session:
        invoices = {
            (row["id"], row["billing_address"])
            for row in session.execute(select(Base.metadata.tables["invoices"])).mappings()
        }
        users = {
            row["id"]: dict(row)
            for row in session.execute(select(Base.metadata.tables["users"])).mappings()
        }
        comments = session.execute(select(Base.metadata.tables["comments"])).mappings().all()
    # Retention is sacred: subject 1's invoice survives untouched.
    assert (1, "1 Alice Street") in invoices
    # Subject 2 is wholly untouched, in every table.
    assert (2, "2 Bob Street") in invoices
    assert users[2]["email"] == "bob@example.com"
    assert users[2]["name"] == "Bob Roe"
    assert users[2]["theme"] == "light"
    assert [row["user_id"] for row in comments] == [2]
