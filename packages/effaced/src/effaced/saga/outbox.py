"""The :class:`Outbox` — durable storage for pending external calls."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from effaced.annotations import Correction, SubjectRef
from effaced.saga.outbox_entry import OutboxEntry
from effaced.saga.outbox_operation import OutboxOperation
from effaced.saga.outbox_status import OutboxStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sqlalchemy import RowMapping, Table
    from sqlalchemy.orm import Session, sessionmaker

    from effaced.saga.status_counts_source import StatusCountsSource


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
        *,
        status_counts_source: StatusCountsSource | None = None,
    ) -> None:
        """Wire the outbox to the application's session factory and table.

        Args:
            session_factory: Factory producing sessions on the database
                holding the outbox table; used by :meth:`claim_batch` and
                the ``mark_*`` bookkeeping methods.
            outbox: The ``effaced_outbox`` table handle from
                :func:`effaced.bind_tables`.
            status_counts_source: Optional SQL-side aggregator for
                :meth:`status_counts`. When omitted, counting materializes
                every row in Python; inject
                :class:`~effaced.SqlStatusCountsSource` to push the
                aggregation into the database for large outboxes.
        """
        self._session_factory = session_factory
        self._outbox = outbox
        self._status_counts_source = status_counts_source

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
        """Record a successful external call; detect per-operation completion.

        Runs in one transaction that first locks all of the subject's
        entries *of the entry's operation* ``FOR UPDATE`` ordered by
        ``entry_id`` — two runners finishing the same subject's last two
        entries serialize on the same lock order instead of deadlocking,
        so exactly one of them observes the all-succeeded transition. If,
        after this update, every same-operation entry for the subject is
        ``SUCCEEDED`` (an ``ABANDONED`` sibling blocks completion
        permanently; entries of the *other* operation are invisible here —
        ADR 0013), ``on_subject_complete`` is invoked inside the open
        transaction; if it raises, the update rolls back and the entry
        stays ``IN_FLIGHT`` for the lease to heal — a success is never
        recorded without its completion check.

        The update also clears the row's ``payload``: a terminal entry
        never retains corrected values.

        With the default :class:`~effaced.DatabaseAuditSink` the callback
        opens a *second* pooled connection while this transaction still
        holds the first — size the pool for two connections per concurrent
        runner thread. An exhausted pool times out, rolls back, and the
        lease heals the entry, but the work is wasted.

        Args:
            entry: The claimed entry whose resolver call succeeded.
            on_subject_complete: Invoked at most once, while the subject's
                same-operation rows are still locked, when its last entry
                of that operation lands.
        """
        columns = self._outbox.c
        siblings = (
            self._outbox.select()
            .with_only_columns(columns.entry_id, columns.status)
            .where(
                columns.subject_id == entry.subject_id,
                columns.operation == entry.operation.value,
            )
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
                    payload=None,
                )
            )
            statuses[entry.entry_id] = OutboxStatus.SUCCEEDED.value
            if all(status == OutboxStatus.SUCCEEDED.value for status in statuses.values()):
                on_subject_complete()

    def mark_failed(self, entry: OutboxEntry, *, error: str, next_attempt_at: datetime) -> None:
        """Record a retryable failure and schedule the next attempt.

        The row's ``payload`` survives a retryable failure on purpose: the
        retry needs the corrected values.

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
            clear_payload=False,
        )

    def mark_abandoned(self, entry: OutboxEntry, *, error: str) -> None:
        """Record a terminal failure; the entry is never retried.

        Abandonment is loud by contract: the runner appends the step-failed
        audit event *before* calling this, so an abandoned entry always has
        its audit record. The row's ``payload`` is cleared — a terminal
        entry never retains corrected values (ADR 0013).

        Args:
            entry: The claimed entry whose retries are exhausted or whose
                failure is non-retryable.
            error: The exception class name — never its message.
        """
        self._mark(
            entry,
            status=OutboxStatus.ABANDONED,
            error=error,
            next_attempt_at=None,
            clear_payload=True,
        )

    def list_abandoned(self, *, limit: int = 100) -> Sequence[OutboxEntry]:
        """Return abandoned entries for operator inspection, oldest first.

        The read half of "abandoned loudly": every entry whose retries are
        exhausted stays visible here (and in the audit trail) until it is
        handled out of band. Read-only by design — abandonment is permanent
        under ADR 0010, so there is deliberately no requeue surface; what
        an abandoned erasure requires is a determination only you can make.

        Args:
            limit: Maximum entries to return.

        Returns:
            ``ABANDONED`` entries, oldest first (by ``enqueued_at``, then
            ``entry_id``).
        """
        columns = self._outbox.c
        query = (
            self._outbox.select()
            .where(columns.status == OutboxStatus.ABANDONED.value)
            .order_by(columns.enqueued_at, columns.entry_id)
            .limit(limit)
        )
        with self._session_factory() as session:
            return tuple(_entry(row) for row in session.execute(query).mappings())

    def status_counts(self) -> dict[OutboxStatus, int]:
        """Count entries per lifecycle status, for dashboards and health checks.

        Read-only. Every :class:`~effaced.OutboxStatus` member is present in
        the result, zero-filled — a healthy, drained outbox reports explicit
        zeros rather than missing keys. A growing ``ABANDONED`` count is the
        operator signal that erasures need out-of-band attention.

        Counting materializes every row in Python by default, since core
        does not import SQLAlchemy at runtime. For large outboxes, inject a
        :class:`~effaced.SqlStatusCountsSource` at construction to push the
        aggregation into a single ``GROUP BY`` query — the result is
        identical either way.

        Returns:
            A mapping with one entry per status.
        """
        if self._status_counts_source is not None:
            return self._status_counts_source.status_counts(self._outbox, self._session_factory)
        statuses = self._outbox.select().with_only_columns(self._outbox.c.status)
        counts = dict.fromkeys(OutboxStatus, 0)
        with self._session_factory() as session:
            for row in session.execute(statuses):
                counts[OutboxStatus(row.status)] += 1
        return counts

    def _mark(
        self,
        entry: OutboxEntry,
        *,
        status: OutboxStatus,
        error: str,
        next_attempt_at: datetime | None,
        clear_payload: bool,
    ) -> None:
        """Move one entry to ``status`` in a short own transaction."""
        values: dict[str, object] = {
            "status": status.value,
            "last_error": error,
            "next_attempt_at": next_attempt_at,
        }
        if clear_payload:
            values["payload"] = None
        with self._session_factory() as session, session.begin():
            session.execute(
                self._outbox.update()
                .where(self._outbox.c.entry_id == entry.entry_id)
                .values(**values)
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
        "operation": entry.operation.value,
        "payload": (
            {"corrections": [c.model_dump(mode="json") for c in entry.corrections]}
            if entry.corrections
            else None
        ),
        "status": entry.status.value,
        "attempts": entry.attempts,
        "enqueued_at": entry.enqueued_at,
        "last_attempt_at": entry.last_attempt_at,
        "next_attempt_at": entry.next_attempt_at,
        "last_error": entry.last_error,
    }


def _corrections(payload: object) -> tuple[Correction, ...]:
    """Reconstruct a row's corrections; an absent or empty payload is ``()``."""
    if not isinstance(payload, dict):
        return ()
    return tuple(Correction.model_validate(item) for item in payload.get("corrections", ()))


def _claimed(row: RowMapping, *, now: datetime, lease: timedelta) -> OutboxEntry:
    """One claimed entry in its post-claim state (mirror of :func:`_row`)."""
    return OutboxEntry(
        entry_id=row["entry_id"],
        subject_id=row["subject_id"],
        resolver=row["resolver"],
        ref=SubjectRef(kind=row["ref_kind"], value=row["ref_value"], extra=row["ref_extra"]),
        operation=OutboxOperation(row["operation"]),
        corrections=_corrections(row["payload"]),
        status=OutboxStatus.IN_FLIGHT,
        attempts=row["attempts"] + 1,
        enqueued_at=row["enqueued_at"],
        last_attempt_at=now,
        next_attempt_at=now + lease,
        last_error=row["last_error"],
    )


def _entry(row: RowMapping) -> OutboxEntry:
    """One entry exactly as stored (mirror of :func:`_row`)."""
    return OutboxEntry(
        entry_id=row["entry_id"],
        subject_id=row["subject_id"],
        resolver=row["resolver"],
        ref=SubjectRef(kind=row["ref_kind"], value=row["ref_value"], extra=row["ref_extra"]),
        operation=OutboxOperation(row["operation"]),
        corrections=_corrections(row["payload"]),
        status=OutboxStatus(row["status"]),
        attempts=row["attempts"],
        enqueued_at=row["enqueued_at"],
        last_attempt_at=row["last_attempt_at"],
        next_attempt_at=row["next_attempt_at"],
        last_error=row["last_error"],
    )
