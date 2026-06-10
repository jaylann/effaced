"""The :class:`SagaRunner` — fans out enqueued external calls."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from effaced.audit import AuditSink
    from effaced.resolvers import ResolverRegistry
    from effaced.saga.outbox import Outbox


class SagaRunner:
    """Executes outbox entries with retries, backoff, and idempotency.

    Designed to be driven by whatever the application already has — a
    background task, a worker process, a cron job. One call to
    :meth:`run_once` processes one batch; the runner owns no event loop.
    """

    def __init__(
        self,
        registry: ResolverRegistry,
        outbox: Outbox,
        audit: AuditSink,
        *,
        max_attempts: int = 8,
    ) -> None:
        """Wire the runner to its collaborators.

        Args:
            registry: Source of resolver implementations.
            outbox: The durable queue to drain.
            audit: Trail that records every outcome, including abandonment.
            max_attempts: Tries before an entry is ABANDONED (and loudly
                audited) rather than retried forever.
        """
        self._registry = registry
        self._outbox = outbox
        self._audit = audit
        self._max_attempts = max_attempts

    async def run_once(self) -> int:
        """Claim and execute one batch of due entries.

        Each entry's resolver call is idempotent (the entry id is the
        idempotency key), so a crash between execution and bookkeeping is
        safe — the retry converges on the same outcome.

        Returns:
            Number of entries processed in this batch.
        """
        raise NotImplementedError
