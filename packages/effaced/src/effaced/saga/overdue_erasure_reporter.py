"""The :class:`OverdueErasureReporter` — read-only stuck-erasure detection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from effaced.saga.overdue_erasure import OverdueErasure
from effaced.saga.overdue_erasure_report import OverdueErasureReport

if TYPE_CHECKING:
    from sqlalchemy import Table
    from sqlalchemy.engine import RowMapping
    from sqlalchemy.orm import sessionmaker


class OverdueErasureReporter:
    """Reports subjects whose local erasure was requested but never completed.

    The read half of the ``effaced_subject_erasures`` tombstone (ADR 0026),
    mirroring :class:`effaced.Outbox`'s operator surface: it builds nothing but
    a single ``SELECT``, writes no rows, and takes no locks. A stuck or overdue
    erasure — a first attempt that crashed before committing, or a re-erasure
    that re-opened the row and stalled — is exactly a tombstone whose
    ``requested_at`` predates the cutoff and whose ``erased_at`` is still
    ``NULL``. A re-erasure clears ``erased_at`` back to ``NULL``, so the same
    predicate finds a stuck re-run, never masked by the prior completion.

    The reporter holds no database connection; like
    :class:`effaced.Outbox`'s read methods it opens a short read-only session
    from the factory per call. It lives in core and reads through the passed
    ``Table`` handle, so core takes no runtime SQLAlchemy import.
    """

    def __init__(
        self,
        session_factory: sessionmaker,  # type: ignore[type-arg]  # sessionmaker generic unbound here
        subject_erasures: Table,
    ) -> None:
        """Wire the reporter to a session factory and the tombstone table.

        Args:
            session_factory: Factory producing sessions on the database
                holding the tombstone table; one short read-only session is
                opened per :meth:`report` call.
            subject_erasures: The ``effaced_subject_erasures`` table handle
                from :func:`effaced.bind_tables`
                (:attr:`effaced.EffacedTables.subject_erasures`).
        """
        self._session_factory = session_factory
        self._subject_erasures = subject_erasures

    def report(
        self,
        older_than: timedelta,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> OverdueErasureReport:
        """List subjects whose erasure has been outstanding past ``older_than``.

        Selects tombstone rows where ``requested_at < now - older_than`` and
        ``erased_at IS NULL`` — requested, never completed, older than the
        threshold — ordered most-overdue first. Read-only: a plain ``SELECT``,
        no writes, no locks (so the guarantee holds on every dialect, unlike
        the lock-bearing claim path).

        Whether any reported subject is a genuine fault is the operator's
        determination — see :class:`OverdueErasureReport`. The external-call
        half of "stuck erasure" is :meth:`effaced.Outbox.list_abandoned`;
        consult both for a full picture.

        Args:
            older_than: The minimum age an outstanding request must exceed to
                be reported. ``timedelta(0)`` reports every incomplete erasure.
            now: The cutoff instant the whole report is evaluated against;
                defaults to the current UTC time. Pass it explicitly for
                determinism.
            limit: Maximum subjects to return, most-overdue first.

        Returns:
            The report, one entry per overdue subject (most-overdue first).
        """
        moment = now if now is not None else datetime.now(UTC)
        cutoff = moment - older_than
        columns = self._subject_erasures.c
        query = (
            self._subject_erasures.select()
            .where(columns.erased_at.is_(None), columns.requested_at < cutoff)
            .order_by(columns.requested_at, columns.subject_ref)
            .limit(limit)
        )
        with self._session_factory() as session:
            rows = session.execute(query).mappings().all()
        entries = tuple(self._entry(row, moment) for row in rows)
        return OverdueErasureReport(reported_at=moment, older_than=older_than, entries=entries)

    @staticmethod
    def _entry(row: RowMapping, moment: datetime) -> OverdueErasure:
        """Build one overdue entry, normalizing the request time to UTC.

        SQLite hands timestamps back tz-naive; they are stored as UTC, so the
        offset is reattached before the age is computed against the
        (tz-aware) cutoff instant.
        """
        requested_at = _as_utc(row["requested_at"])
        return OverdueErasure(
            subject_ref=row["subject_ref"],
            requested_at=requested_at,
            age=moment - requested_at,
        )


def _as_utc(value: datetime) -> datetime:
    """Attach UTC to a naive timestamp; tz-aware values pass through."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
