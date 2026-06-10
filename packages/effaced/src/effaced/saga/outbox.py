"""The :class:`Outbox` — durable storage for pending external calls."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import Table
    from sqlalchemy.orm import Session, sessionmaker

    from effaced.saga.outbox_entry import OutboxEntry


class Outbox:
    """Transactional outbox in the application's own database.

    Entries are enqueued inside the caller's transaction (atomically with
    the local erasure) and claimed by the saga runner afterwards.
    :meth:`enqueue` uses the caller's session; :meth:`claim_batch` runs
    outside any caller transaction and uses the factory (ADR 0006).
    """

    def __init__(
        self,
        session_factory: sessionmaker,  # type: ignore[type-arg]  # sessionmaker generic unbound here
        outbox: Table,
    ) -> None:
        """Wire the outbox to the application's session factory and table.

        Args:
            session_factory: Factory producing sessions on the database
                holding the outbox table; used only by :meth:`claim_batch`.
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

    def claim_batch(self, limit: int = 50) -> Sequence[OutboxEntry]:
        """Atomically claim due entries for execution.

        Claimed entries move to ``IN_FLIGHT`` so concurrent runners never
        execute the same entry twice.

        Args:
            limit: Maximum entries to claim in one batch.

        Returns:
            The claimed entries, oldest first.
        """
        raise NotImplementedError


def _row(entry: OutboxEntry) -> dict[str, object]:
    """Flatten one entry into the outbox table's column values."""
    return {
        "entry_id": entry.entry_id,
        "resolver": entry.resolver,
        "ref_kind": entry.ref.kind,
        "ref_value": entry.ref.value,
        "ref_extra": dict(entry.ref.extra),
        "status": entry.status.value,
        "attempts": entry.attempts,
        "enqueued_at": entry.enqueued_at,
        "last_attempt_at": entry.last_attempt_at,
        "last_error": entry.last_error,
    }
