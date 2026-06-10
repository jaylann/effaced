"""The :class:`SagaRunner` — fans out enqueued external calls."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from effaced.audit.event import AuditEvent
from effaced.audit.event_type import AuditEventType
from effaced.categories import ErasureStrategy
from effaced.exceptions import ResolverError
from effaced.resolvers import ResolverErasure
from effaced.saga.backoff_policy import BackoffPolicy

if TYPE_CHECKING:
    from effaced.audit import AuditSink
    from effaced.resolvers import ResolverRegistry
    from effaced.saga.outbox import Outbox
    from effaced.saga.outbox_entry import OutboxEntry


class SagaRunner:
    """Executes outbox entries with retries, backoff, and idempotency.

    Designed to be driven by whatever the application already has — a
    background task, a worker process, a cron job. One call to
    :meth:`run_once` processes one batch; the runner owns no event loop.

    Failure taxonomy (ADR 0010): :class:`~effaced.ResolverError` — raised
    by a resolver for a non-retryable failure, or by the registry for an
    unknown resolver name — abandons the entry immediately; any other
    exception is treated as transient and retried with exponential backoff
    until ``max_attempts``, then abandoned. Every terminal outcome is
    audited; an abandonment is never silent.
    """

    def __init__(
        self,
        registry: ResolverRegistry,
        outbox: Outbox,
        audit: AuditSink,
        *,
        max_attempts: int = 8,
        batch_size: int = 50,
        backoff: BackoffPolicy | None = None,
    ) -> None:
        """Wire the runner to its collaborators.

        Args:
            registry: Source of resolver implementations.
            outbox: The durable queue to drain.
            audit: Trail that records every outcome, including abandonment.
            max_attempts: Claims before an entry is ABANDONED (and loudly
                audited) rather than retried forever.
            batch_size: Maximum entries claimed per :meth:`run_once` call.
            backoff: Retry schedule and claim lease; defaults to
                :class:`~effaced.BackoffPolicy`'s defaults (30s doubling to
                1h, 5min lease).
        """
        self._registry = registry
        self._outbox = outbox
        self._audit = audit
        self._max_attempts = max_attempts
        self._batch_size = batch_size
        self._backoff = backoff if backoff is not None else BackoffPolicy()

    async def run_once(self) -> int:
        """Claim and execute one batch of due entries.

        Each entry's resolver call is idempotent (the entry id is the
        idempotency key), so a crash between execution and bookkeeping is
        safe — the entry stays ``IN_FLIGHT``, its claim lease expires, and
        the retry converges on the same outcome.

        Per entry: success appends ``ERASURE_STEP_SUCCEEDED`` and — when
        the subject's last entry lands — ``ERASURE_COMPLETED``; a terminal
        failure appends ``ERASURE_STEP_FAILED`` before the entry is marked
        ``ABANDONED``. The audit append always precedes the status change,
        so no recorded outcome lacks its audit record; if the sink is down
        the entry stays claimed and the lease heals it. Transient failures
        are not audited — the row's ``last_error`` carries the exception
        class name and the entry retries on the backoff schedule.

        Awaits resolver calls concurrently but makes blocking database
        calls (claiming, audit appends) between awaits — run it in a
        worker, cron job, or background task, never on a serving event
        loop (ADR 0006).

        Returns:
            Number of entries processed in this batch.
        """
        entries = self._outbox.claim_batch(self._batch_size, lease=self._backoff.lease)
        if not entries:
            return 0
        outcomes = await asyncio.gather(
            *(self._execute(entry) for entry in entries), return_exceptions=True
        )
        for entry, outcome in zip(entries, outcomes, strict=True):
            self._settle(entry, outcome)
        return len(entries)

    async def _execute(self, entry: OutboxEntry) -> ResolverErasure:
        """One resolver call; exceptions are captured by the gather."""
        resolver = self._registry.get(entry.resolver)
        return await resolver.erase_subject(entry.ref)

    def _settle(self, entry: OutboxEntry, outcome: ResolverErasure | BaseException) -> None:
        """Book one entry's outcome: succeed, retry with backoff, or abandon."""
        if isinstance(outcome, ResolverErasure):
            self._succeed(entry, outcome)
        elif isinstance(outcome, ResolverError):
            self._abandon(entry, outcome)
        elif isinstance(outcome, Exception):
            if entry.attempts >= self._max_attempts:
                self._abandon(entry, outcome)
            else:
                self._retry(entry, outcome)
        else:
            # Cancellation and other non-Exception signals are not outcomes;
            # the entry stays IN_FLIGHT and the lease heals it.
            raise outcome

    def _succeed(self, entry: OutboxEntry, erasure: ResolverErasure) -> None:
        """Audit the success, then mark it; completion fires on the last entry."""
        self._audit.append(
            _event(
                AuditEventType.ERASURE_STEP_SUCCEEDED,
                entry.subject_id,
                {
                    "target": entry.resolver,
                    "strategy": ErasureStrategy.DELETE.value,
                    "external": True,
                    "already_absent": erasure.already_absent,
                    "attempts": entry.attempts,
                },
            )
        )
        self._outbox.mark_succeeded(
            entry,
            on_subject_complete=lambda: self._audit.append(
                _event(AuditEventType.ERASURE_COMPLETED, entry.subject_id, {})
            ),
        )

    def _abandon(self, entry: OutboxEntry, exc: BaseException) -> None:
        """Audit the abandonment loudly, then mark the entry terminal."""
        self._audit.append(
            _event(
                AuditEventType.ERASURE_STEP_FAILED,
                entry.subject_id,
                {
                    "target": entry.resolver,
                    "strategy": ErasureStrategy.DELETE.value,
                    "external": True,
                    "error": type(exc).__name__,
                    "attempts": entry.attempts,
                    "abandoned": True,
                },
            )
        )
        self._outbox.mark_abandoned(entry, error=type(exc).__name__)

    def _retry(self, entry: OutboxEntry, exc: BaseException) -> None:
        """Schedule the next attempt on the backoff curve; not audited."""
        self._outbox.mark_failed(
            entry,
            error=type(exc).__name__,
            next_attempt_at=datetime.now(UTC) + self._backoff.delay(entry.attempts),
        )


def _event(
    event_type: AuditEventType,
    subject_id: str,
    payload: dict[str, str | int | bool],
) -> AuditEvent:
    """One audit event for this saga step, stamped now (UTC).

    Payloads carry exception class names only, never messages — provider
    errors embed identifiers, and the trail must stay PII-free.
    """
    return AuditEvent(
        event_id=uuid4(),
        event_type=event_type,
        subject_ref=subject_id,
        occurred_at=datetime.now(UTC),
        payload=payload,
    )
