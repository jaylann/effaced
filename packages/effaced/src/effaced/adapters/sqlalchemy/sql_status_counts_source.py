"""The :class:`SqlStatusCountsSource` — SQL-side outbox status aggregation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from effaced.saga.outbox_status import OutboxStatus

if TYPE_CHECKING:
    from sqlalchemy import Table
    from sqlalchemy.orm import sessionmaker


class SqlStatusCountsSource:
    """Counts outbox entries per status with a single ``GROUP BY`` query.

    The SQLAlchemy implementation of
    :class:`~effaced.StatusCountsSource`: it aggregates in the database
    (``SELECT status, count(*) ... GROUP BY status``) rather than streaming
    every row back to Python, so a large outbox costs one cheap query.
    Stateless — share one instance freely.

    The result is zero-filled over every :class:`~effaced.OutboxStatus`
    member, so it is byte-for-byte interchangeable with the Python-side
    count :meth:`~effaced.Outbox.status_counts` falls back to.
    """

    def status_counts(
        self,
        outbox: Table,
        session_factory: sessionmaker,  # type: ignore[type-arg]  # sessionmaker generic unbound here
    ) -> dict[OutboxStatus, int]:
        """Count outbox entries per lifecycle status, SQL-side.

        Args:
            outbox: The ``effaced_outbox`` table handle to count over.
            session_factory: Factory producing sessions on the database
                holding that table; a short-lived read session is opened.

        Returns:
            A mapping with one entry per :class:`~effaced.OutboxStatus`,
            zero-filled where no rows exist for a status.
        """
        query = select(outbox.c.status, func.count()).group_by(outbox.c.status)
        counts = dict.fromkeys(OutboxStatus, 0)
        with session_factory() as session:
            for status, count in session.execute(query):
                counts[OutboxStatus(status)] = count
        return counts
