"""The :class:`DatabaseAuditSink` — default sink, your own database."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import sessionmaker

    from effaced.audit.event import AuditEvent


class DatabaseAuditSink:
    """Append-only audit storage in the application's own database.

    The default sink: zero data leaves the user's system in OSS mode. Rows
    are insert-only; the table carries no update path and the sink exposes
    none.
    """

    def __init__(self, session_factory: sessionmaker) -> None:  # type: ignore[type-arg]
        """Wire the sink to the application's session factory.

        Args:
            session_factory: Factory producing sessions on the database
                that should hold the trail.
        """
        self._session_factory = session_factory

    def append(self, event: AuditEvent) -> None:
        """Durably append one event (insert-only).

        Args:
            event: The event to persist.
        """
        raise NotImplementedError

    def read(self, subject_ref: str) -> Sequence[AuditEvent]:
        """Read one subject's trail, oldest first.

        Args:
            subject_ref: The opaque subject reference to filter by.

        Returns:
            All events recorded for the subject.
        """
        raise NotImplementedError
