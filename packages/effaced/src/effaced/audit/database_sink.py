"""The :class:`DatabaseAuditSink` — default sink, your own database."""

from __future__ import annotations

from typing import TYPE_CHECKING

from effaced.audit.event import AuditEvent
from effaced.audit.event_type import AuditEventType
from effaced.audit.hash_chain import compute_event_hash
from effaced.exceptions import AuditIntegrityError, ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy import RowMapping, Table
    from sqlalchemy.orm import sessionmaker


class DatabaseAuditSink:
    """Append-only audit storage in the application's own database.

    The default sink: zero data leaves the user's system in OSS mode. Rows
    are insert-only; the table carries no update path and the sink exposes
    none. Each :meth:`append` commits in its own short transaction (ADR
    0006), so an event survives even when the caller's surrounding
    transaction later rolls back — audit evidence is never lost to an
    unrelated failure.

    Each appended row also carries a tamper-evidence hash chain (ADR 0028):
    its ``event_hash`` binds its content to the prior row's hash, so an
    out-of-band edit of any recorded row is *detectable* (not *prevented*) by
    :class:`~effaced.AuditChainVerifier`.
    """

    def __init__(
        self,
        session_factory: sessionmaker,  # type: ignore[type-arg]  # sessionmaker generic unbound here
        audit_events: Table,
    ) -> None:
        """Wire the sink to the application's session factory and trail table.

        Args:
            session_factory: Factory producing sessions on the database
                that should hold the trail.
            audit_events: The ``effaced_audit_events`` table handle from
                :func:`effaced.bind_tables`.
        """
        self._session_factory = session_factory
        self._audit_events = audit_events

    def append(self, event: AuditEvent) -> None:
        """Durably append one event (insert-only), extending the hash chain.

        Commits immediately in a transaction of its own. A duplicate
        ``event_id`` raises the database's integrity error — an existing
        row is never overwritten.

        Within that same transaction the prior chained row's ``event_hash``
        is read (the row with the greatest ``occurred_at``, ``event_id``
        tiebreak — the same total order :meth:`read` uses) and this event's
        ``prior_hash``/``event_hash`` are computed and written, so the chain
        extends atomically with the insert (ADR 0028). The per-append
        transaction is the serialization point; under genuinely concurrent
        appends two rows may chain to the same predecessor, which
        :class:`~effaced.AuditChainVerifier` reports as a fork — surfaced,
        never silently healed.

        Args:
            event: The event to persist.
        """
        with self._session_factory.begin() as session:
            columns = self._audit_events.c
            prior_statement = (
                self._audit_events.select()
                .with_only_columns(columns.event_hash)
                .where(columns.event_hash.isnot(None))
                .order_by(columns.occurred_at.desc(), columns.event_id.desc())
                .limit(1)
            )
            prior_hash = session.execute(prior_statement).scalars().first()
            event_hash = compute_event_hash(event, prior_hash)
            session.execute(
                self._audit_events.insert().values(
                    event_id=event.event_id,
                    event_type=event.event_type.value,
                    subject_ref=event.subject_ref,
                    occurred_at=event.occurred_at,
                    payload=dict(event.payload),
                    prior_hash=prior_hash,
                    event_hash=event_hash,
                )
            )

    def read(self, subject_ref: str) -> Sequence[AuditEvent]:
        """Read one subject's trail, oldest first.

        Ordering is by ``occurred_at``, ties broken by ``event_id`` so
        repeated reads always agree.

        Args:
            subject_ref: The opaque subject reference to filter by.

        Returns:
            All events recorded for the subject.

        Raises:
            AuditIntegrityError: If the trail contains an ``event_type``
                this version of effaced cannot interpret (recorded by a
                newer release). This is deliberately all-or-nothing: one
                unreadable entry fails the whole read rather than serving
                a silently incomplete trail — partial evidence presented
                as complete would be worse than no answer. Upgrading
                effaced restores readability; nothing is lost.
        """
        columns = self._audit_events.c
        statement = (
            self._audit_events.select()
            .where(columns.subject_ref == subject_ref)
            .order_by(columns.occurred_at.asc(), columns.event_id.asc())
        )
        with self._session_factory() as session:
            rows = session.execute(statement).mappings().all()
        return tuple(self._to_event(row) for row in rows)

    def read_since(self, since: datetime) -> Sequence[AuditEvent]:
        """Read every subject's events from ``since`` onward, oldest first.

        The :class:`~effaced.ReplaySource` capability (ADR 0023): the
        window a backup-replay derivation consumes. The boundary is
        inclusive (``occurred_at >= since``) — matching the replay rule
        that an erasure at exactly the backup instant is replayed — and
        ordering ties in ``occurred_at`` resolve by ``event_id``, exactly
        as :meth:`read` does.

        Args:
            since: The instant to read from, inclusive. Must be
                timezone-aware — the trail's timestamps are UTC, and a
                naive comparison could silently shift the window boundary
                by the session offset, dropping events from the read.

        Returns:
            All events at or after ``since``, across all subjects.

        Raises:
            ConfigurationError: If ``since`` is timezone-naive — the same
                guard :meth:`ReplayPlan.derive <effaced.ReplayPlan.derive>`
                applies to its cutoff, for the same reason.
            AuditIntegrityError: If the window contains an ``event_type``
                this version of effaced cannot interpret — all-or-nothing,
                as in :meth:`read`.
        """
        if since.tzinfo is None or since.tzinfo.utcoffset(since) is None:
            msg = (
                "since must be timezone-aware; the audit trail's timestamps "
                "are UTC and a naive bound can silently shift the window"
            )
            raise ConfigurationError(msg)
        columns = self._audit_events.c
        statement = (
            self._audit_events.select()
            .where(columns.occurred_at >= since)
            .order_by(columns.occurred_at.asc(), columns.event_id.asc())
        )
        with self._session_factory() as session:
            rows = session.execute(statement).mappings().all()
        return tuple(self._to_event(row) for row in rows)

    @staticmethod
    def _to_event(row: RowMapping) -> AuditEvent:
        """Rebuild one stored row into an :class:`AuditEvent`."""
        raw_type = row["event_type"]
        try:
            event_type = AuditEventType(raw_type)
        except ValueError as exc:
            msg = (
                f"audit event {row['event_id']} has event_type {raw_type!r}, which this "
                f"version of effaced cannot interpret; upgrade effaced to read this trail"
            )
            raise AuditIntegrityError(msg) from exc
        return AuditEvent(
            event_id=row["event_id"],
            event_type=event_type,
            subject_ref=row["subject_ref"],
            occurred_at=row["occurred_at"],
            payload=row["payload"],
        )
