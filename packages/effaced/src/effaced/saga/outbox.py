"""The :class:`Outbox` — durable storage for pending external calls."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from effaced.annotations import SubjectRef
from effaced.saga.outbox_entry import OutboxEntry
from effaced.saga.outbox_status import OutboxStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sqlalchemy import RowMapping, Table
    from sqlalchemy.orm import Session, sessionmaker


_DEFAULT_LEASE = timedelta(minutes=5)

_CLAIMABLE = (
    OutboxStatus.PENDING.value,
    OutboxStatus.FAILED.value,
    OutboxStatus.IN_FLIGHT.value,
)


class Outbox:
    """Transactional outbox in the application's own database.

    Entries are enqueued inside the caller's transaction (atomically with
    the local erasure) and claimed by the saga runner afterwards.
    :meth:`enqueue` uses the caller's session; the claim and bookkeeping
    methods run outside any caller transaction and use the factory
    (ADR 0006).
    """

    def __init__(
        self,
        session_factory: sessionmaker,  # type: ignore[type-arg]  # sessionmaker generic unbound here
        outbox: Table,
    ) -> None:
        """Wire the outbox to the application's session factory and table.

        Args:
            session_factory: Factory producing sessions on the database
                holding the outbox table; used by :meth:`claim_batch` and
                the ``mark_*`` bookkeeping methods.
            outbox: The ``effaced_outbox`` table handle from
                :func:`effaced.bind_tables`.
        """
        self._session_factory = session_factory
        self._outbox = outbox

    def enqueue(self, session: Session, entries: Sequence[OutboxEntry]) -> None:
        """Persist entries inside the caller's open transaction.

        Never commits — the entries become durable exactly when the
        caller's transaction does, and a rollback takes them with it.
        The nested :class:`~effaced.SubjectRef` is flattened into the
        table's ``ref_kind``/``ref_value``/``ref_extra`` columns.

        Args:
            session: The SAME session the local erasure runs in — that is
                the whole point; the entries commit or roll back with it.
            entries: The external calls to record.
        """
        if not entries:
            return
        session.execute(self._outbox.insert(), [_row(entry) for entry in entries])

    def claim_batch(
        self, limit: int = 50, *, lease: timedelta = _DEFAULT_LEASE
    ) -> Sequence[OutboxEntry]:
        """Atomically claim due entries for execution, oldest first.

        An entry is due when its ``next_attempt_at`` gate is ``NULL`` or in
        the past and it is not terminal: fresh ``PENDING`` entries, ``FAILED``
        entries whose backoff has elapsed, and ``IN_FLIGHT`` entries whose
        claim lease has expired (a crashed runner's work, healed here).
        Claimed entries move to ``IN_FLIGHT`` with ``attempts`` incremented —
        the claim *is* the attempt, so an entry that crashes its runner every
        time still converges to abandonment — and ``next_attempt_at`` set to
        ``now + lease``.

        On PostgreSQL the selection runs ``FOR UPDATE SKIP LOCKED``, so
        concurrent runners never double-claim. SQLite ignores row locking;
        the no-double-claim guarantee holds only on dialects that support
        ``FOR UPDATE``.

        Args:
            limit: Maximum entries to claim in one batch.
            lease: How long the claim protects the entries from other
                runners. Must comfortably exceed the slowest expected
                resolver call; a too-short lease causes double execution
                (absorbed by resolver idempotency, but wasteful).

        Returns:
            The claimed entries in their post-claim state, oldest first.
        """
        now = datetime.now(UTC)
        columns = self._outbox.c
        due = (
            self._outbox.select()
            .where(
                columns.status.in_(_CLAIMABLE),
                columns.next_attempt_at.is_(None) | (columns.next_attempt_at <= now),
            )
            .order_by(columns.enqueued_at, columns.entry_id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        with self._session_factory() as session, session.begin():
            rows = session.execute(due).mappings().all()
            if not rows:
                return ()
            session.execute(
                self._outbox.update()
                .where(columns.entry_id.in_([row["entry_id"] for row in rows]))
                .values(
                    status=OutboxStatus.IN_FLIGHT.value,
                    attempts=columns.attempts + 1,
                    last_attempt_at=now,
                    next_attempt_at=now + lease,
                )
            )
            return tuple(_claimed(row, now=now, lease=lease) for row in rows)

    def mark_succeeded(
        self, entry: OutboxEntry, *, on_subject_complete: Callable[[], None]
    ) -> None:
        """Record a successful external call; detect subject completion.

        Runs in one transaction that first locks *all* of the subject's
        entries ``FOR UPDATE`` ordered by ``entry_id`` — two runners
        finishing the same subject's last two entries serialize on the same
        lock order instead of deadlocking, so exactly one of them observes
        the all-succeeded transition. If, after this update, every entry
        for the subject is ``SUCCEEDED`` (an ``ABANDONED`` sibling blocks
        completion permanently), ``on_subject_complete`` is invoked inside
        the open transaction; if it raises, the update rolls back and the
        entry stays ``IN_FLIGHT`` for the lease to heal — a success is
        never recorded without its completion check.

        With the default :class:`~effaced.DatabaseAuditSink` the callback
        opens a *second* pooled connection while this transaction still
        holds the first — size the pool for two connections per concurrent
        runner thread. An exhausted pool times out, rolls back, and the
        lease heals the entry, but the work is wasted.

        Args:
            entry: The claimed entry whose resolver call succeeded.
            on_subject_complete: Invoked at most once, while the subject's
                rows are still locked, when its last entry lands.
        """
        columns = self._outbox.c
        siblings = (
            self._outbox.select()
            .with_only_columns(columns.entry_id, columns.status)
            .where(columns.subject_id == entry.subject_id)
            .order_by(columns.entry_id)
            .with_for_update()
        )
        with self._session_factory() as session, session.begin():
            statuses = {row.entry_id: row.status for row in session.execute(siblings)}
            session.execute(
                self._outbox.update()
                .where(columns.entry_id == entry.entry_id)
                .values(
                    status=OutboxStatus.SUCCEEDED.value,
                    next_attempt_at=None,
                    last_error=None,
                )
            )
            statuses[entry.entry_id] = OutboxStatus.SUCCEEDED.value
            if all(status == OutboxStatus.SUCCEEDED.value for status in statuses.values()):
                on_subject_complete()

    def mark_failed(self, entry: OutboxEntry, *, error: str, next_attempt_at: datetime) -> None:
        """Record a retryable failure and schedule the next attempt.

        Args:
            entry: The claimed entry whose resolver call failed.
            error: The exception class name — never its message, which may
                embed PII.
            next_attempt_at: When the entry becomes claimable again (the
                backoff schedule).
        """
        self._mark(
            entry,
            status=OutboxStatus.FAILED,
            error=error,
            next_attempt_at=next_attempt_at,
        )

    def mark_abandoned(self, entry: OutboxEntry, *, error: str) -> None:
        """Record a terminal failure; the entry is never retried.

        Abandonment is loud by contract: the runner appends the
        ``ERASURE_STEP_FAILED`` audit event *before* calling this, so an
        abandoned entry always has its audit record.

        Args:
            entry: The claimed entry whose retries are exhausted or whose
                failure is non-retryable.
            error: The exception class name — never its message.
        """
        self._mark(entry, status=OutboxStatus.ABANDONED, error=error, next_attempt_at=None)

    def _mark(
        self,
        entry: OutboxEntry,
        *,
        status: OutboxStatus,
        error: str,
        next_attempt_at: datetime | None,
    ) -> None:
        """Move one entry to ``status`` in a short own transaction."""
        with self._session_factory() as session, session.begin():
            session.execute(
                self._outbox.update()
                .where(self._outbox.c.entry_id == entry.entry_id)
                .values(
                    status=status.value,
                    last_error=error,
                    next_attempt_at=next_attempt_at,
                )
            )


def _row(entry: OutboxEntry) -> dict[str, object]:
    """Flatten one entry into the outbox table's column values."""
    return {
        "entry_id": entry.entry_id,
        "subject_id": entry.subject_id,
        "resolver": entry.resolver,
        "ref_kind": entry.ref.kind,
        "ref_value": entry.ref.value,
        "ref_extra": dict(entry.ref.extra),
        "status": entry.status.value,
        "attempts": entry.attempts,
        "enqueued_at": entry.enqueued_at,
        "last_attempt_at": entry.last_attempt_at,
        "next_attempt_at": entry.next_attempt_at,
        "last_error": entry.last_error,
    }


def _claimed(row: RowMapping, *, now: datetime, lease: timedelta) -> OutboxEntry:
    """One claimed entry in its post-claim state (mirror of :func:`_row`)."""
    return OutboxEntry(
        entry_id=row["entry_id"],
        subject_id=row["subject_id"],
        resolver=row["resolver"],
        ref=SubjectRef(kind=row["ref_kind"], value=row["ref_value"], extra=row["ref_extra"]),
        status=OutboxStatus.IN_FLIGHT,
        attempts=row["attempts"] + 1,
        enqueued_at=row["enqueued_at"],
        last_attempt_at=now,
        next_attempt_at=now + lease,
        last_error=row["last_error"],
    )
