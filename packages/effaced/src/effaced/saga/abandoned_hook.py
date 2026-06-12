"""The :class:`AbandonedHook` protocol — proactive notice of abandonment."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from effaced.saga.abandoned_signal import AbandonedSignal


@runtime_checkable
class AbandonedHook(Protocol):
    """A host callback fired when an outbox entry is abandoned.

    Re-driving abandoned work is already a pull (``Outbox.list_abandoned`` /
    ``Outbox.requeue``); this is the push. Wire one in to page or emit a
    metric the instant a subject request stalls, instead of polling.

    This protocol is public API, extended additively only. Sync by design
    (ADR 0006) — the hook runs on the runner's thread, between blocking
    bookkeeping calls.
    """

    def on_abandoned(self, signal: AbandonedSignal) -> None:
        """React to one entry's abandonment.

        Called **after** the entry's transition to ``ABANDONED`` is durable
        and its audit event is written, so the hook is side-effect-isolated
        from both: the runner swallows whatever this raises and does not
        wait on it beyond the call returning. A slow or failing alerting
        backend therefore cannot corrupt or block the state transition or
        the audit trail — keeping this fast and resilient is the host's job.

        Args:
            signal: The PII-free summary of the abandoned entry.
        """
        ...
