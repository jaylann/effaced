"""Backup replay against a real Postgres — read_since window + convergence."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import NamedTuple

import pytest
from conftest import Base, seed_two_subjects
from sqlalchemy import Engine, MetaData, select
from sqlalchemy.orm import Session, sessionmaker

from effaced import (
    AuditEventType,
    DatabaseAuditSink,
    EffacedTables,
    ErasurePlanner,
    Outbox,
    Replayer,
    bind_tables,
    collect_data_map,
    resolve_subject_graph,
)
from effaced.adapters.sqlalchemy import ErasureExecutor

pytestmark = pytest.mark.integration


class PgHarness(NamedTuple):
    """A wired planner + replayer over the seeded schema on Postgres."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    sink: DatabaseAuditSink
    planner: ErasurePlanner
    replayer: Replayer


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
        planner = ErasurePlanner(
            data_map,
            resolve_subject_graph(data_map, Base.registry),
            executor=ErasureExecutor(Base.metadata),
            outbox=Outbox(session_factory, tables.outbox),
            audit_sink=sink,
        )
        yield PgHarness(session_factory, tables, sink, planner, Replayer(planner, sink))
    finally:
        effaced_metadata.drop_all(pg_engine)
        Base.metadata.drop_all(pg_engine)


def restore(harness: PgHarness) -> None:
    """Simulate a restore of the schema tables to the seeded backup.

    The ``effaced_*`` tables survive here, playing the surviving copy of
    the trail the mechanism requires (in production: an external sink, a
    replica, or a pre-restore dump).
    """
    with harness.session_factory() as session:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
    with harness.session_factory() as session:
        seed_two_subjects(session)


def test_replay_from_read_since_converges_on_postgres(harness: PgHarness) -> None:
    """Erase → restore → read_since → replay round-trips on a real database."""
    backup_taken_at = datetime.now(UTC)
    with harness.session_factory() as session:
        harness.planner.erase_subject(session, "1")
        session.commit()
    restore(harness)
    plan = harness.replayer.plan(
        harness.sink.read_since(backup_taken_at), backup_taken_at=backup_taken_at
    )
    assert [entry.subject_id for entry in plan.entries] == ["1"]
    with harness.session_factory() as session:
        (result,) = harness.replayer.replay(session, plan)
        session.commit()
    assert result.deleted == {"comments": 2, "order_items": 1, "orders": 1}
    assert result.retained == {"invoices": 1}
    with harness.session_factory() as session:
        users = {
            row["id"]: dict(row)
            for row in session.execute(select(Base.metadata.tables["users"])).mappings()
        }
        assert users[1]["email"] != "alice@example.com"
        assert users[2]["email"] == "bob@example.com"
        invoices = session.execute(select(Base.metadata.tables["invoices"])).mappings().all()
        assert {(row["id"], row["billing_address"]) for row in invoices} == {
            (1, "1 Alice Street"),
            (2, "2 Bob Street"),
        }
        orders = session.execute(select(Base.metadata.tables["orders"])).mappings().all()
        assert [row["user_id"] for row in orders] == [2]
    types = [event.event_type for event in harness.sink.read("1")]
    replayed_at = types.index(AuditEventType.ERASURE_REPLAYED)
    assert types[replayed_at + 1] == AuditEventType.ERASURE_REQUESTED
    assert types.count(AuditEventType.ERASURE_LOCAL_COMPLETED) == 2
