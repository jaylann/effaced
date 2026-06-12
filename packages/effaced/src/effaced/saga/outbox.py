"""The :class:`Outbox` — durable storage for pending external calls."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from effaced.annotations import Correction, SubjectRef
from effaced.audit.event import AuditEvent
from effaced.audit.event_type import AuditEventType
from effaced.exceptions import ConfigurationError
from effaced.saga.outbox_entry import OutboxEntry
from effaced.saga.outbox_operation import OutboxOperation
from effaced.saga.outbox_status import OutboxStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from uuid import UUID

    from sqlalchemy import RowMapping, Table
    from sqlalchemy.orm import Session, sessionmaker

    from effaced.audit import AuditSink
    from effaced.saga.status_counts_source import StatusCountsSource


_DEFAULT_LEASE = timedelta(minutes=5)

_CLAIMABLE = (
    OutboxStatus.PENDING.value,
    OutboxStatus.FAILED.value,
    OutboxStatus.IN_FLIGHT.value,
    OutboxStatus.SCHEDULED.value,
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
        audit_sink: AuditSink | None = None,
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
            audit_sink: The trail :meth:`requeue` appends its supervised
                ``*_REQUEUED`` events to. Optional because the read and
                bookkeeping surfaces need no sink; :meth:`requeue` raises
                :class:`~effaced.ConfigurationError` when called without
                one. Pass the same sink the saga runner writes to.
        """
        self._session_factory = session_factory
        self._outbox = outbox
        self._status_counts_source = status_counts_source
        self._audit_sink = audit_sink

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
        entries whose backoff has elapsed, ``IN_FLIGHT`` entries whose
        claim lease has expired (a crashed runner's work, healed here),
        and ``SCHEDULED`` entries whose retention horizon has passed
        (parked by :meth:`mark_scheduled`, re-claimed to verify expiry —
        ADR 0018).
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

    def mark_scheduled(self, entry: OutboxEntry, *, resume_at: datetime) -> None:
        """Park the entry until its retention horizon (ADR 0018).

        Records that the external system can only *expire* the data: the
        entry moves to ``SCHEDULED`` with ``next_attempt_at=resume_at``
        (the same gate that schedules retries), ``attempts=0``, and
        ``last_error=NULL`` — the verification after the horizon gets the
        full retry budget, the ADR 0015 requeue precedent; the prior
        struggle lives in the ``ERASURE_EXPIRY_SCHEDULED`` event the
        runner appends *before* calling this. A ``SCHEDULED`` entry is not
        terminal: it blocks ``ERASURE_COMPLETED`` until re-claimed after
        the horizon and verified gone.

        Args:
            entry: The claimed entry whose erasure was scheduled.
            resume_at: When the entry becomes claimable again — the
                retention horizon (clamped by the runner to at least one
                backoff step from now).
        """
        with self._session_factory() as session, session.begin():
            session.execute(
                self._outbox.update()
                .where(self._outbox.c.entry_id == entry.entry_id)
                .values(
                    status=OutboxStatus.SCHEDULED.value,
                    attempts=0,
                    next_attempt_at=resume_at,
                    last_error=None,
                )
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
        handled out of band. The ids it returns are exactly what
        :meth:`requeue` consumes once the underlying cause is fixed —
        abandonment is terminal under ADR 0010 *until an operator
        requeues* (ADR 0015); whether an abandoned erasure needs that is a
        determination only you can make.

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

    def requeue(self, entry_ids: Iterable[UUID]) -> Sequence[OutboxEntry]:
        """Return abandoned *erase* entries to the queue with a fresh budget.

        The single supervised mutation of the operator surface (ADR 0015):
        the operator, having fixed the cause an entry abandoned for, hands
        back the ids :meth:`list_abandoned` produced. Each id that is
        currently ``ABANDONED`` flips to ``PENDING`` with
        ``next_attempt_at = NULL`` (due immediately), ``attempts = 0`` (the
        full budget, not one borrowed attempt), and ``last_error = NULL``.
        The prior struggle is not lost — it moves into the requeue audit
        event, where history belongs, instead of lingering in columns that
        now describe a fresh entry. ``entry_id`` is unchanged, so the
        resolver-side idempotency key holds and re-execution converges.

        **Erase-only — rectify entries cannot be requeued.** A rectify
        entry's corrections (real PII) live in the row's ``payload`` and
        are *cleared at abandonment* under ADR 0013, exactly as on success.
        A requeued rectify entry would re-execute with no corrections — a
        silent no-op that would still fire ``RECTIFICATION_COMPLETED``, an
        Art. 16 correctness hole. So an ``ABANDONED`` rectify entry among
        the ids raises :class:`~effaced.ConfigurationError` naming it,
        *before* any audit append or status change (validation-first, the
        same ordering as the rest of the trail): nothing flips and no event
        is written. The remediation for an abandoned rectification is to
        re-issue it through the :class:`~effaced.Rectifier`, which
        re-enqueues a fresh entry carrying the corrections again.

        **Append-first audit.** Before any row flips, one
        ``ERASURE_REQUEUED`` event is appended per entry, payload
        ``{entry_id, resolver, prior_attempts, prior_error}`` — the prior
        error is the exception *class name* only, never a message. The
        append precedes the status change under ADR 0010's ordering rule:
        if the sink is down, the append raises and nothing transitions; a
        crash between the sink commit and the outbox commit can duplicate
        an event but never lose one. With the default
        :class:`~effaced.DatabaseAuditSink` each append opens a *second*
        pooled connection while this transaction still holds the ``FOR
        UPDATE`` locks — the same connection-budget footgun as
        :meth:`mark_succeeded`; size the pool for two connections per
        caller, or an exhausted pool deadlocks the requeue against itself.

        **Idempotent and skip-tolerant.** Ids that are missing, or whose
        entry is no longer ``ABANDONED`` (a colleague requeued first, a
        generation already succeeded), are silently skipped — never
        errors. Calling :meth:`requeue` twice with the same ids is success;
        the return value reports only the entries that actually flipped.

        The whole call runs in one transaction that first locks the
        affected rows ``FOR UPDATE`` ordered by ``entry_id`` — the same
        lock order as :meth:`mark_succeeded`'s completion check, so a
        requeue racing a concurrent runner serializes instead of
        deadlocking. (SQLite ignores row locking; the serialization
        guarantee holds only on dialects that support ``FOR UPDATE``.)

        Args:
            entry_ids: The ids to requeue, as produced by
                :meth:`list_abandoned`. Order is irrelevant; locking always
                follows ``entry_id`` order.

        Returns:
            The entries that actually flipped, in their post-requeue
            ``PENDING`` state. Empty when no supplied id was ``ABANDONED``.

        Raises:
            ConfigurationError: If the outbox was constructed without an
                ``audit_sink`` (the supervised requeue event has nowhere to
                land), or if any supplied ``ABANDONED`` entry is a rectify
                entry (its corrections were cleared at abandonment, ADR
                0013 — re-issue via the :class:`~effaced.Rectifier`). Both
                are raised before any event or flip.
        """
        if self._audit_sink is None:
            msg = "Outbox.requeue requires an audit_sink; construct the Outbox with one"
            raise ConfigurationError(msg)
        ids = list(dict.fromkeys(entry_ids))
        if not ids:
            return ()
        columns = self._outbox.c
        locked = (
            self._outbox.select()
            .where(columns.entry_id.in_(ids))
            .order_by(columns.entry_id)
            .with_for_update()
        )
        with self._session_factory() as session, session.begin():
            rows = session.execute(locked).mappings().all()
            abandoned = [row for row in rows if row["status"] == OutboxStatus.ABANDONED.value]
            _reject_rectify_entries(abandoned)
            for row in abandoned:
                self._audit_sink.append(_requeued_event(row))
            if not abandoned:
                return ()
            session.execute(
                self._outbox.update()
                .where(columns.entry_id.in_([row["entry_id"] for row in abandoned]))
                .values(
                    status=OutboxStatus.PENDING.value,
                    attempts=0,
                    next_attempt_at=None,
                    last_error=None,
                )
            )
            return tuple(_requeued_entry(row) for row in abandoned)

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


def _reject_rectify_entries(rows: list[RowMapping]) -> None:
    """Refuse to requeue an abandoned rectify entry (ADR 0013 and ADR 0015).

    A rectify entry's corrections are cleared at abandonment, so requeuing
    it would re-execute with nothing to apply — a silent no-op rectification
    that still completes. Raised before any append or flip; re-issue the
    rectification via the :class:`~effaced.Rectifier` instead.
    """
    rectify = [row for row in rows if OutboxOperation(row["operation"]) is OutboxOperation.RECTIFY]
    if not rectify:
        return
    ids = ", ".join(str(row["entry_id"]) for row in rectify)
    msg = (
        f"cannot requeue abandoned rectify entries ({ids}): their corrections were "
        "cleared at abandonment (ADR 0013); re-issue the rectification via the Rectifier"
    )
    raise ConfigurationError(msg)


def _requeued_event(row: RowMapping) -> AuditEvent:
    """The append-first ``ERASURE_REQUEUED`` event for one abandoned row.

    Requeue is erase-only (rectify entries are refused upstream), so the
    event type is fixed. The payload carries the prior struggle
    (``prior_attempts``/``prior_error`` — the exception *class name* only)
    so the row's columns can reset to a fresh budget. ``prior_error`` is
    omitted entirely when the row carried none, rather than emitted as an
    empty string — an abandoned row always has one, so this is defensive,
    but the payload shape is MAJOR-protected and an absent error is "no
    error", never the empty-string error class.
    """
    payload: dict[str, str | int | bool] = {
        "entry_id": str(row["entry_id"]),
        "resolver": row["resolver"],
        "prior_attempts": row["attempts"],
    }
    if row["last_error"] is not None:
        payload["prior_error"] = row["last_error"]
    return AuditEvent(
        event_id=uuid4(),
        event_type=AuditEventType.ERASURE_REQUEUED,
        subject_ref=row["subject_id"],
        occurred_at=datetime.now(UTC),
        payload=payload,
    )


def _requeued_entry(row: RowMapping) -> OutboxEntry:
    """One entry in its post-requeue ``PENDING`` state (fresh retry budget)."""
    return _entry(row).model_copy(
        update={
            "status": OutboxStatus.PENDING,
            "attempts": 0,
            "next_attempt_at": None,
            "last_error": None,
        }
    )
