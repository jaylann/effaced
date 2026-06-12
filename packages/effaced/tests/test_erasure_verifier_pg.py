"""ErasureVerifier against a real Postgres — the coerce_subject_id typed path."""

from __future__ import annotations

from collections.abc import Iterator
from typing import NamedTuple

import pytest
from conftest import Base, seed_two_subjects
from sqlalchemy import Engine, MetaData
from sqlalchemy.orm import Session, sessionmaker

from effaced import (
    AuditEventType,
    DatabaseAuditSink,
    EffacedTables,
    ErasurePlanner,
    ErasureVerifier,
    Outbox,
    ResolverRegistry,
    bind_tables,
    collect_data_map,
    resolve_subject_graph,
)
from effaced.adapters.sqlalchemy import ErasureExecutor

pytestmark = pytest.mark.integration


class PgHarness(NamedTuple):
    """A planner + verifier over the seeded schema on Postgres."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    planner: ErasurePlanner
    verifier: ErasureVerifier
    verify_sink: DatabaseAuditSink


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
        data_map = collect_data_map(Base.metadata)
        graph = resolve_subject_graph(data_map, Base.registry)
        planner = ErasurePlanner(
            data_map,
            graph,
            ResolverRegistry(),
            executor=ErasureExecutor(Base.metadata),
            outbox=Outbox(session_factory, tables.outbox),
            audit_sink=sink,
        )
        verifier = ErasureVerifier(data_map, graph, Base.metadata, audit_sink=sink)
        yield PgHarness(session_factory, tables, planner, verifier, sink)
    finally:
        effaced_metadata.drop_all(pg_engine)
        Base.metadata.drop_all(pg_engine)


def test_verify_after_committed_erasure_is_clean_on_postgres(harness: PgHarness) -> None:
    """The integer subject id round-trips through the typed-parameter path."""
    with harness.session_factory() as session:
        harness.planner.erase_subject(session, "1")
        session.commit()
    with harness.session_factory() as session:
        verification = harness.verifier.verify_subject_erased(session, "1")
    assert verification.verified is True
    assert verification.residual == {"comments": 0, "order_items": 0, "orders": 0}
    assert verification.surviving == {"users": 1, "invoices": 1}
    verified = [
        event
        for event in harness.verify_sink.read("1")
        if event.event_type == AuditEventType.ERASURE_VERIFIED
    ]
    assert len(verified) == 1
    assert verified[0].payload == {
        "tables_checked": 5,
        "residual_rows": 0,
        "surviving_rows": 2,
        "failed_tables": "",
    }


def test_pre_erasure_verification_is_false_on_postgres(harness: PgHarness) -> None:
    with harness.session_factory() as session:
        verification = harness.verifier.verify_subject_erased(session, "1")
    assert verification.verified is False
    assert verification.residual == {"comments": 2, "order_items": 1, "orders": 1}
    failed = [
        event
        for event in harness.verify_sink.read("1")
        if event.event_type == AuditEventType.ERASURE_VERIFICATION_FAILED
    ]
    assert len(failed) == 1
    assert failed[0].payload["residual_rows"] == 4
