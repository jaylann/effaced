"""SubjectErasureLock against real Postgres — serialization + anchor locking (ADR 0026).

SQLite silently drops ``FOR UPDATE`` (testing.md), so the locking guarantees
are only provable here. Two proofs:

- two ``erase_subject`` calls for the *same* subject serialize: the second
  blocks on the tombstone row until the first commits;
- an in-flight application write to the subject's anchor row blocks while an
  erasure holds the anchor-row lock, closing the INSERT-races-DELETE window.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import NamedTuple

import pytest
from conftest import Base, seed_two_subjects
from sqlalchemy import Engine, MetaData, select, update
from sqlalchemy.orm import Session, sessionmaker

from effaced import (
    DatabaseAuditSink,
    EffacedTables,
    ErasurePlanner,
    Outbox,
    ResolverRegistry,
    SubjectErasureLock,
    bind_tables,
    collect_data_map,
    resolve_subject_graph,
)
from effaced.adapters.sqlalchemy import ErasureExecutor
from effaced.adapters.sqlalchemy.storage.subject_erasures_table import (
    SUBJECT_ERASURE_REQUESTED,
)

pytestmark = pytest.mark.integration


class PgHarness(NamedTuple):
    """A planner wired with a SubjectErasureLock over the seeded schema."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    planner: ErasurePlanner


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
        data_map = collect_data_map(Base.metadata)
        graph = resolve_subject_graph(data_map, Base.registry)
        planner = ErasurePlanner(
            data_map,
            graph,
            ResolverRegistry(),
            executor=ErasureExecutor(Base.metadata),
            outbox=Outbox(session_factory, tables.outbox),
            audit_sink=DatabaseAuditSink(session_factory, tables.audit_events),
            lock=SubjectErasureLock(Base.metadata, graph, tables.subject_erasures),
        )
        yield PgHarness(session_factory, tables, planner)
    finally:
        effaced_metadata.drop_all(pg_engine)
        Base.metadata.drop_all(pg_engine)


def test_concurrent_same_subject_erasures_serialize(harness: PgHarness) -> None:
    """B's erase_subject blocks on the tombstone until A commits.

    A holds an open erasure transaction for subject 1 (lock acquired, rows
    deleted, not committed). B starts erasing the same subject in another
    connection; it must block on A's tombstone-row ``FOR UPDATE`` and not
    finish until A commits — the serialization barrier.
    """
    b_done = threading.Event()

    def erase_b() -> None:
        with harness.session_factory() as session:
            harness.planner.erase_subject(session, "1")
            session.commit()
        b_done.set()

    with harness.session_factory() as session_a:
        session_a.begin()
        # A acquires the lock and runs the local phase, holding the transaction.
        harness.planner.erase_subject(session_a, "1")
        worker = threading.Thread(target=erase_b)
        worker.start()
        time.sleep(0.5)
        # B is blocked on A's held tombstone lock — it has not finished.
        assert not b_done.is_set()
        session_a.commit()
    # A released its locks; B serializes through and completes.
    worker.join(timeout=10)
    assert b_done.is_set()

    # One tombstone, and the subject's local rows are gone (B's no-op success
    # over A's committed state — the idempotency contract, ADR 0009).
    with harness.session_factory() as session:
        tombstones = [
            dict(row) for row in session.execute(select(harness.tables.subject_erasures)).mappings()
        ]
        comments = session.execute(
            select(Base.metadata.tables["comments"]).where(
                Base.metadata.tables["comments"].c.user_id == 1
            )
        ).all()
    assert len(tombstones) == 1
    assert tombstones[0]["subject_ref"] == "1"
    assert tombstones[0]["status"] == SUBJECT_ERASURE_REQUESTED
    assert comments == []


def test_in_flight_write_to_the_subject_blocks_mid_erasure(harness: PgHarness) -> None:
    """An UPDATE of the subject's anchor row waits while an erasure holds it.

    The erasure locks the subject table's rows ``FOR UPDATE`` for the local
    phase, so a concurrent application write to the subject's own row blocks
    until the erasure commits — the INSERT/UPDATE-races-DELETE window closed.
    """
    write_done = threading.Event()
    users = Base.metadata.tables["users"]

    def write_subject() -> None:
        with harness.session_factory() as session, session.begin():
            session.execute(update(users).where(users.c.id == 2).values(name="changed"))
        write_done.set()

    with harness.session_factory() as session_a:
        session_a.begin()
        harness.planner.erase_subject(session_a, "2")
        worker = threading.Thread(target=write_subject)
        worker.start()
        time.sleep(0.5)
        # The competing write is blocked on the erasure's anchor-row lock.
        assert not write_done.is_set()
        session_a.commit()
    worker.join(timeout=10)
    assert write_done.is_set()
