"""The :class:`Outbox` — durable storage for pending external calls."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session, sessionmaker

    from effaced.saga.outbox_entry import OutboxEntry


class Outbox:
    """Transactional outbox in the application's own database.

    Entries are enqueued inside the caller's transaction (atomically with
    the local erasure) and claimed by the saga runner afterwards.
    :meth:`enqueue` uses the caller's session; :meth:`claim_batch` runs
    outside any caller transaction and uses the factory (ADR 0006).
    """

    def __init__(self, session_factory: sessionmaker) -> None:  # type: ignore[type-arg]
        """Wire the outbox to the application's session factory.

        Args:
            session_factory: Factory producing sessions on the database
                holding the outbox table; used only by :meth:`claim_batch`.
        """
        self._session_factory = session_factory

    def enqueue(self, session: Session, entries: Sequence[OutboxEntry]) -> None:
        """Persist entries inside the caller's open transaction.

        Args:
            session: The SAME session the local erasure runs in — that is
                the whole point; the entries commit or roll back with it.
            entries: The external calls to record.
        """
        raise NotImplementedError

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
