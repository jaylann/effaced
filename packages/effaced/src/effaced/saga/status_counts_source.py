"""The :class:`StatusCountsSource` protocol — how the outbox counts by status."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sqlalchemy import Table
    from sqlalchemy.orm import sessionmaker

    from effaced.saga.outbox_status import OutboxStatus


@runtime_checkable
class StatusCountsSource(Protocol):
    """Computes the outbox's per-status entry counts.

    Lets :meth:`~effaced.Outbox.status_counts` push the aggregation into
    the database instead of materializing every row in Python. Core stays
    storage-agnostic — it never imports SQLAlchemy at runtime — so the
    actual ``GROUP BY`` lives in an adapter implementation
    (:class:`~effaced.SqlStatusCountsSource`) injected at construction.

    This protocol is public API. It is extended additively only — a custom
    source must never break on upgrade.
    """

    def status_counts(
        self,
        outbox: Table,
        session_factory: sessionmaker,  # type: ignore[type-arg]  # sessionmaker generic unbound here
    ) -> dict[OutboxStatus, int]:
        """Count outbox entries per lifecycle status.

        Contract: the returned mapping is zero-filled over **every**
        :class:`~effaced.OutboxStatus` member — a drained outbox reports
        explicit zeros, never missing keys — so the result is a drop-in
        replacement for the Python-side count.

        Args:
            outbox: The ``effaced_outbox`` table handle to count over.
            session_factory: Factory producing sessions on the database
                holding that table; the source opens its own short-lived
                read session.

        Returns:
            A mapping with exactly one entry per status.
        """
        ...
