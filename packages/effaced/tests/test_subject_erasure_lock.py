"""SubjectErasureLock — tombstone upsert idempotency and planner wiring (ADR 0026).

The serialization and anchor-row locking guarantees need real ``FOR UPDATE``
and live in ``test_erase_subject_lock_pg.py``; SQLite silently drops row
locking, so these unit tests cover the portable half: the tombstone upsert is
idempotent and records a fresh request on re-erasure, and the planner acquires
the lock before the first audit event when one is wired (and not at all when it
is not).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from conftest import Base, RecordingAuditSink, seed_two_subjects
from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    AuditEventType,
    EffacedTables,
    ErasurePlanner,
    Outbox,
    ResolverRegistry,
    SubjectErasureLock,
    SubjectLock,
    bind_tables,
    collect_data_map,
    resolve_subject_graph,
)
from effaced.adapters.sqlalchemy import ErasureExecutor
from effaced.adapters.sqlalchemy.storage.subject_erasures_table import (
    SUBJECT_ERASURE_COMPLETED,
    SUBJECT_ERASURE_REQUESTED,
)

if TYPE_CHECKING:
    from effaced.annotations import SubjectIdentifier


class RecordingLock:
    """A ``SubjectLock`` spy recording call order against the audit events."""

    def __init__(self, sink: RecordingAuditSink) -> None:
        self._sink = sink
        self.calls: list[SubjectIdentifier] = []
        self.events_at_acquire: list[AuditEventType] = []
        self.marked: list[SubjectIdentifier] = []
        self.events_at_mark: list[AuditEventType] = []

    def acquire(self, session: Session, subject_ref: SubjectIdentifier) -> None:
        self.calls.append(subject_ref)
        self.events_at_acquire = [event.event_type for event in self._sink.events]

    def mark_erased(self, session: Session, subject_ref: SubjectIdentifier) -> None:
        self.marked.append(subject_ref)
        self.events_at_mark = [event.event_type for event in self._sink.events]


def _engine_with_tables() -> tuple[sessionmaker[Session], EffacedTables]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    effaced_metadata = MetaData()
    tables = bind_tables(effaced_metadata)
    Base.metadata.create_all(engine)
    effaced_metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    with session_factory() as session:
        seed_two_subjects(session)
    return session_factory, tables


def _lock(tables: EffacedTables) -> SubjectErasureLock:
    data_map = collect_data_map(Base.metadata)
    graph = resolve_subject_graph(data_map, Base.registry)
    return SubjectErasureLock(Base.metadata, graph, tables.subject_erasures)


def _tombstones(session: Session, tables: EffacedTables) -> list[dict[str, object]]:
    return [dict(row) for row in session.execute(select(tables.subject_erasures)).mappings()]


def test_acquire_inserts_one_requested_tombstone() -> None:
    session_factory, tables = _engine_with_tables()
    lock = _lock(tables)
    with session_factory() as session:
        lock.acquire(session, "1")
        session.commit()
    with session_factory() as session:
        rows = _tombstones(session, tables)
    assert len(rows) == 1
    assert rows[0]["subject_ref"] == "1"
    assert rows[0]["status"] == SUBJECT_ERASURE_REQUESTED
    assert rows[0]["requested_at"] is not None
    assert rows[0]["erased_at"] is None


def test_acquire_is_idempotent_and_refreshes_requested_at() -> None:
    """Re-erasing a tombstoned subject keeps one row and records a fresh request."""
    session_factory, tables = _engine_with_tables()
    lock = _lock(tables)
    with session_factory() as session:
        lock.acquire(session, "1")
        session.commit()
    # Backdate the first request so a refresh is observable deterministically.
    backdated = datetime.now(UTC) - timedelta(days=1)
    with session_factory() as session:
        session.execute(
            tables.subject_erasures.update()
            .where(tables.subject_erasures.c.subject_ref == "1")
            .values(requested_at=backdated)
        )
        session.commit()
    with session_factory() as session:
        lock.acquire(session, "1")
        session.commit()
    with session_factory() as session:
        rows = _tombstones(session, tables)
    assert len(rows) == 1
    # SQLite round-trips DateTime as tz-naive; compare on the naive instants.
    refreshed = rows[0]["requested_at"]
    assert isinstance(refreshed, datetime)
    assert refreshed > backdated.replace(tzinfo=None)
    assert rows[0]["status"] == SUBJECT_ERASURE_REQUESTED


def test_mark_erased_sets_completed_status_and_erased_at() -> None:
    """After the local phase, the tombstone records completion with a timestamp."""
    session_factory, tables = _engine_with_tables()
    lock = _lock(tables)
    with session_factory() as session:
        lock.acquire(session, "1")
        lock.mark_erased(session, "1")
        session.commit()
    with session_factory() as session:
        rows = _tombstones(session, tables)
    assert len(rows) == 1
    assert rows[0]["status"] == SUBJECT_ERASURE_COMPLETED
    assert rows[0]["erased_at"] is not None


def test_re_erasing_a_completed_subject_returns_to_requested() -> None:
    """A fresh erasure of a completed subject re-opens the tombstone.

    The detection surface tracks the *latest* erasure: a re-request moves the
    row back to ``requested`` (and clears nothing else here), and marking it
    again completes it. So a completed row means the most recent erasure
    finished, never a stale one.
    """
    session_factory, tables = _engine_with_tables()
    lock = _lock(tables)
    with session_factory() as session:
        lock.acquire(session, "1")
        lock.mark_erased(session, "1")
        session.commit()
    with session_factory() as session:
        lock.acquire(session, "1")
        session.commit()
    with session_factory() as session:
        rows = _tombstones(session, tables)
    assert rows[0]["status"] == SUBJECT_ERASURE_REQUESTED


def test_acquire_tombstones_distinct_subjects_separately() -> None:
    session_factory, tables = _engine_with_tables()
    lock = _lock(tables)
    with session_factory() as session:
        lock.acquire(session, "1")
        lock.acquire(session, "2")
        session.commit()
    with session_factory() as session:
        refs = {row["subject_ref"] for row in _tombstones(session, tables)}
    assert refs == {"1", "2"}


def _planner(
    session_factory: sessionmaker[Session],
    tables: EffacedTables,
    sink: RecordingAuditSink,
    lock: SubjectLock | None,
) -> ErasurePlanner:
    data_map = collect_data_map(Base.metadata)
    return ErasurePlanner(
        data_map,
        resolve_subject_graph(data_map, Base.registry),
        ResolverRegistry(),
        executor=ErasureExecutor(Base.metadata),
        outbox=Outbox(session_factory, tables.outbox),
        audit_sink=sink,
        lock=lock,
    )


def test_planner_acquires_before_first_event_and_marks_after_steps() -> None:
    session_factory, tables = _engine_with_tables()
    sink = RecordingAuditSink()
    lock = RecordingLock(sink)
    planner = _planner(session_factory, tables, sink, lock)
    with session_factory() as session:
        planner.erase_subject(session, "1")
        session.commit()
    assert lock.calls == ["1"]
    # acquire ran before ERASURE_REQUESTED — no event had been appended yet.
    assert lock.events_at_acquire == []
    assert sink.events[0].event_type == AuditEventType.ERASURE_REQUESTED
    # mark_erased ran after the local steps but before ERASURE_LOCAL_COMPLETED.
    assert lock.marked == ["1"]
    assert AuditEventType.ERASURE_STEP_SUCCEEDED in lock.events_at_mark
    assert AuditEventType.ERASURE_LOCAL_COMPLETED not in lock.events_at_mark


def test_planner_without_lock_takes_none_and_writes_no_tombstone() -> None:
    session_factory, tables = _engine_with_tables()
    sink = RecordingAuditSink()
    planner = _planner(session_factory, tables, sink, lock=None)
    with session_factory() as session:
        planner.erase_subject(session, "1")
        session.commit()
    with session_factory() as session:
        assert _tombstones(session, tables) == []
    assert sink.events[0].event_type == AuditEventType.ERASURE_REQUESTED


def test_planner_wired_lock_records_a_completed_tombstone() -> None:
    session_factory, tables = _engine_with_tables()
    sink = RecordingAuditSink()
    planner = _planner(session_factory, tables, sink, _lock(tables))
    with session_factory() as session:
        planner.erase_subject(session, "1")
        session.commit()
    with session_factory() as session:
        rows = _tombstones(session, tables)
    assert len(rows) == 1
    assert rows[0]["subject_ref"] == "1"
    # The successful local phase marked the tombstone completed.
    assert rows[0]["status"] == SUBJECT_ERASURE_COMPLETED
    assert rows[0]["erased_at"] is not None


def test_rolled_back_erasure_leaves_no_completed_tombstone() -> None:
    """The completion mark is durable only with the erasure (same transaction).

    A failing local phase that rolls back must leave no completed tombstone —
    the surface never claims an erasure that did not commit.
    """
    session_factory, tables = _engine_with_tables()
    sink = RecordingAuditSink()
    planner = _planner(session_factory, tables, sink, _lock(tables))
    with session_factory() as session:
        planner.erase_subject(session, "1")
        session.rollback()
    with session_factory() as session:
        assert _tombstones(session, tables) == []
