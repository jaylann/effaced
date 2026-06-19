"""The :class:`SubjectErasureLock` — the SQLAlchemy :class:`SubjectLock`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from effaced.adapters.sqlalchemy.scoping import subject_scope
from effaced.adapters.sqlalchemy.storage.subject_erasures_table import (
    SUBJECT_ERASURE_REQUESTED,
)
from effaced.annotations import canonical_subject_id

if TYPE_CHECKING:
    from sqlalchemy import MetaData, Table
    from sqlalchemy.orm import Session

    from effaced.annotations import SubjectIdentifier
    from effaced.manifest import SubjectGraph


class SubjectErasureLock:
    """Serializes erasures of one subject and locks its anchor rows (ADR 0026).

    The SQLAlchemy implementation of
    :class:`~effaced.erasure.SubjectLock`. :meth:`acquire` runs inside the
    caller's open erasure transaction and takes two row locks, in this fixed
    order:

    1. The subject's tombstone row in ``effaced_subject_erasures`` — upserted
       (a fresh ``requested_at`` each time) and locked ``FOR UPDATE``, so a
       second concurrent erasure of the *same* subject blocks here until the
       first commits.
    2. The subject's **anchor rows** — the subject table's rows for this
       identity, located by the one shared hop-chain predicate
       (:func:`~effaced.adapters.sqlalchemy.subject_scope`, never forked) and
       locked ``FOR UPDATE``, so an in-flight application write to the
       subject's own row, or a child INSERT resolving its foreign key against
       the anchor, blocks until the erasure commits.

    The local steps that follow reach the anchor (subject) table last in
    FK-safe order — already locked — and the outbox enqueue only inserts new
    rows, so no later step acquires a lock out of this order. The fixed
    tombstone → anchor order means two concurrent same-subject erasures both
    queue on the tombstone row first and serialize there, never deadlocking;
    and the erasure transaction never holds an ``effaced_outbox`` row lock
    while taking these, so it cannot invert against the saga runner's
    entry-level ``FOR UPDATE`` (a separate, runner-side transaction over
    already-committed rows).

    SQLite silently drops ``FOR UPDATE``: the serialization and anchor-lock
    guarantees hold only on dialects that honour row locking (Postgres). The
    tombstone upsert itself is portable and exercised on both.
    """

    def __init__(self, metadata: MetaData, graph: SubjectGraph, table: Table) -> None:
        """Wire the lock to the schema, subject graph, and tombstone table.

        Args:
            metadata: The ``MetaData`` holding the manifest's tables — the
                same one the executor and data map use, so the anchor
                predicate resolves the subject table.
            graph: The resolved subject graph; supplies the subject table and
                its identity columns for the anchor-row lock.
            table: The ``effaced_subject_erasures`` handle from
                :func:`~effaced.bind_tables` (``EffacedTables.subject_erasures``).
        """
        self._metadata = metadata
        self._graph = graph
        self._table = table

    def acquire(self, session: Session, subject_ref: SubjectIdentifier) -> None:
        """Take the tombstone and anchor-row locks (see :class:`SubjectLock`).

        Never commits or rolls back: the locks are held until the caller's
        erasure transaction ends.

        Args:
            session: The caller's open erasure session.
            subject_ref: The subject identifier (single-column ``str`` or
                composite :class:`~effaced.CompositeSubjectId`).
        """
        canonical = canonical_subject_id(subject_ref)
        self._upsert_tombstone(session, canonical)
        self._lock_tombstone(session, canonical)
        self._lock_anchor_rows(session, subject_ref)

    def _upsert_tombstone(self, session: Session, canonical: str) -> None:
        """Insert or refresh the subject's tombstone with a fresh request time.

        A first erasure inserts the row; a re-erasure records a fresh
        ``requested_at`` (the erasure was genuinely re-requested) on the
        existing row. Dialect-portable: Postgres uses ``INSERT … ON CONFLICT
        DO UPDATE`` (which also locks the conflicting row); elsewhere an
        ``INSERT`` is attempted and an ``UPDATE`` falls back on the primary-key
        conflict inside a SAVEPOINT.
        """
        now = datetime.now(UTC)
        values = {
            "subject_ref": canonical,
            "requested_at": now,
            "erased_at": None,
            "status": SUBJECT_ERASURE_REQUESTED,
        }
        if session.get_bind().dialect.name == "postgresql":
            statement = pg_insert(self._table).values(**values)
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=[self._table.c.subject_ref],
                    set_={
                        "requested_at": now,
                        "status": SUBJECT_ERASURE_REQUESTED,
                    },
                )
            )
            return
        self._insert_or_update(session, canonical, now)

    def _insert_or_update(self, session: Session, canonical: str, now: datetime) -> None:
        """Portable upsert fallback: INSERT, then UPDATE on a PK conflict."""
        try:
            with session.begin_nested():
                session.execute(
                    self._table.insert().values(
                        subject_ref=canonical,
                        requested_at=now,
                        erased_at=None,
                        status=SUBJECT_ERASURE_REQUESTED,
                    )
                )
        except IntegrityError:
            session.execute(
                self._table.update()
                .where(self._table.c.subject_ref == canonical)
                .values(requested_at=now, status=SUBJECT_ERASURE_REQUESTED)
            )

    def _lock_tombstone(self, session: Session, canonical: str) -> None:
        """Lock the subject's tombstone row ``FOR UPDATE`` (no-op on SQLite)."""
        session.execute(
            self._table.select().where(self._table.c.subject_ref == canonical).with_for_update()
        )

    def _lock_anchor_rows(self, session: Session, subject_ref: SubjectIdentifier) -> None:
        """Lock the subject's anchor rows ``FOR UPDATE`` (no-op on SQLite).

        The anchor rows are the subject table's rows for this identity,
        located by the shared hop-chain predicate — the same one the
        executor scopes deletes with, never forked.
        """
        predicate = subject_scope(
            self._metadata, self._graph, self._graph.subject_table, subject_ref
        )
        anchor = self._metadata.tables[self._graph.subject_table]
        session.execute(select(anchor).where(predicate).with_for_update())
