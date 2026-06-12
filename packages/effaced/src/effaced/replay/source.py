"""The :class:`ReplaySource` capability protocol — where a surviving trail is read."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from effaced.audit.event import AuditEvent


@runtime_checkable
class ReplaySource(Protocol):
    """Anything that can read the whole trail from an instant onward.

    :meth:`ReplayPlan.derive <effaced.ReplayPlan.derive>` consumes plain
    event sequences, so any surviving record works — this protocol is the
    convenience shape for the common case of pointing at a database that
    still holds the post-backup window.

    It is deliberately *not* part of :class:`~effaced.AuditSink`: adding a
    required method there would break every existing custom sink's
    ``isinstance`` check. This is a standalone capability (the
    :class:`~effaced.RectifyingResolver` pattern, looser still — a dump-file
    loader can be a replay source without being a sink at all).
    :class:`~effaced.DatabaseAuditSink` implements it.
    """

    def read_since(self, since: datetime) -> Sequence[AuditEvent]:
        """Read every subject's events from ``since`` onward, oldest first.

        The boundary is inclusive (``occurred_at >= since``), matching the
        replay window rule (ADR 0018); ordering ties in ``occurred_at``
        resolve by ``event_id`` so repeated reads agree. ``since`` must be
        timezone-aware — implementations reject a naive bound rather than
        let it silently shift the window.

        Args:
            since: The instant to read from, inclusive (timezone-aware).

        Returns:
            All events at or after ``since``, across all subjects.
        """
        ...
