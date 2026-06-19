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
from sqlalchemy import Engine, MetaData, select
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
    lock: SubjectErasureLock


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
        lock = SubjectErasureLock(Base.metadata, graph, tables.subject_erasures)
        planner = ErasurePlanner(
            data_map,
            graph,
            ResolverRegistry(),
            executor=ErasureExecutor(Base.metadata),
            outbox=Outbox(session_factory, tables.outbox),
            audit_sink=DatabaseAuditSink(session_factory, tables.audit_events),
            lock=lock,
        )
        yield PgHarness(session_factory, tables, planner, lock)
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


def test_in_flight_child_insert_blocks_on_the_anchor_lock(harness: PgHarness) -> None:
    """A new child row for the subject waits on the anchor-row lock alone.

    This isolates the anchor lock's *distinct* contribution (ADR 0026 race 2):
    the lock is taken via ``acquire`` directly, so no erasure step has run and
    the only lock held on ``users.id == 2`` is ``acquire``'s ``FOR UPDATE`` —
    not the executor's later anonymize UPDATE. A concurrent application INSERT
    of a child row referencing that subject takes ``FOR KEY SHARE`` on the
    anchor row to validate its foreign key; ``FOR KEY SHARE`` conflicts with
    ``FOR UPDATE``, so the INSERT blocks until the lock holder commits. Against
    a no-op anchor lock the INSERT would not block — that is what makes this a
    real proof rather than a coincidence of the anonymize step.
    """
    insert_done = threading.Event()
    comments = Base.metadata.tables["comments"]

    def insert_child() -> None:
        with harness.session_factory() as session, session.begin():
            session.execute(comments.insert().values(id=999, user_id=2, parent_id=None))
        insert_done.set()

    with harness.session_factory() as session_a:
        session_a.begin()
        # Take ONLY the subject-erasure locks — no executor step runs here.
        harness.lock.acquire(session_a, "2")
        worker = threading.Thread(target=insert_child)
        worker.start()
        time.sleep(0.5)
        # The child INSERT is blocked on the held anchor-row FOR UPDATE.
        assert not insert_done.is_set()
        session_a.commit()
    worker.join(timeout=10)
    assert insert_done.is_set()
