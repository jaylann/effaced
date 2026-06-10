"""The :class:`AuditSink` protocol — where the trail is written.

The protocol is append-only **by construction**: there is no update or
delete method, and there never will be. Custom sinks implement exactly
this surface; the default sink writes to the user's own database, so zero
data leaves their system in OSS mode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from effaced.audit.event import AuditEvent


@runtime_checkable
class AuditSink(Protocol):
    """Anything that can durably append and read back audit events.

    This protocol is public API. It is extended additively only (new
    optional methods with default implementations) — existing custom sinks
    must never break on upgrade.
    """

    def append(self, event: AuditEvent) -> None:
        """Durably append one event.

        Must be atomic per event and must never overwrite anything.
        Sync by design — appends run inside the erasure/consent
        transaction path (ADR 0006); an async external sink would be an
        additive separate adapter, never a change to this protocol.

        Args:
            event: The event to persist.
        """
        ...

    def read(self, subject_ref: str) -> Sequence[AuditEvent]:
        """Read all events for one subject, oldest first.

        Args:
            subject_ref: The opaque subject reference to filter by.

        Returns:
            The subject's full trail — what a regulator asks for first.
        """
        ...
