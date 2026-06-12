"""The :class:`SagaRunner` — fans out enqueued external calls."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from effaced.audit.event import AuditEvent
from effaced.audit.event_type import AuditEventType
from effaced.categories import ErasureStrategy
from effaced.exceptions import ResolverError
from effaced.resolvers import (
    RectifyingResolver,
    ResolverErasure,
    ResolverRectification,
    ResolverScheduledErasure,
    RetentionOnlyResolver,
)
from effaced.saga.abandoned_signal import AbandonedSignal
from effaced.saga.backoff_policy import BackoffPolicy
from effaced.saga.outbox_operation import OutboxOperation

if TYPE_CHECKING:
    from effaced.audit import AuditSink
    from effaced.resolvers import ResolverRegistry
    from effaced.saga.abandoned_hook import AbandonedHook
    from effaced.saga.outbox import Outbox
    from effaced.saga.outbox_entry import OutboxEntry


class SagaRunner:
    """Executes outbox entries with retries, backoff, and idempotency.

    Designed to be driven by whatever the application already has — a
    background task, a worker process, a cron job. One call to
    :meth:`run_once` processes one batch; the runner owns no event loop.

    Entries are dispatched by their ``operation``: erase entries call
    ``Resolver.erase_subject`` — or ``RetentionOnlyResolver.schedule_erasure``
    when the resolver can only schedule expiry (ADR 0018) — and rectify
    entries call ``RectifyingResolver.rectify_subject`` with the entry's
    corrections. A scheduled erasure parks its entry until the retention
    horizon, then re-verifies; it never counts as a completed erasure.

    Failure taxonomy (ADR 0010): :class:`~effaced.ResolverError` — raised
    by a resolver for a non-retryable failure, by the registry for an
    unknown resolver name, or here for a rectify entry whose resolver does
    not implement ``rectify_subject`` — abandons the entry immediately; any
    other exception is treated as transient and retried with exponential
    backoff until ``max_attempts``, then abandoned. Every terminal outcome
    is audited; an abandonment is never silent.
    """

    def __init__(  # noqa: PLR0913 — six collaborators plus the optional alerting hook
        self,
        registry: ResolverRegistry,
        outbox: Outbox,
        audit: AuditSink,
        *,
        max_attempts: int = 8,
        batch_size: int = 50,
        backoff: BackoffPolicy | None = None,
        on_abandoned: AbandonedHook | None = None,
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
            on_abandoned: Optional :class:`~effaced.AbandonedHook` fired
                after each entry is abandoned — proactive notice for paging
                or metrics. Side-effect-isolated: it runs after the durable
                transition and its audit event, and whatever it raises is
                swallowed, so it can never corrupt or block either.
        """
        self._registry = registry
        self._outbox = outbox
        self._audit = audit
        self._max_attempts = max_attempts
        self._batch_size = batch_size
        self._backoff = backoff if backoff is not None else BackoffPolicy()
        self._on_abandoned = on_abandoned

    async def run_once(self) -> int:
        """Claim and execute one batch of due entries.

        Each entry's resolver call is idempotent (the entry id is the
        idempotency key), so a crash between execution and bookkeeping is
        safe — the entry stays ``IN_FLIGHT``, its claim lease expires, and
        the retry converges on the same outcome.

        Per entry: success appends ``ERASURE_STEP_SUCCEEDED`` (erase) or
        ``RECTIFICATION_STEP_SUCCEEDED`` (rectify) and — when the subject's
        last entry *of that operation* lands — ``ERASURE_COMPLETED`` /
        ``RECTIFICATION_COMPLETED``; a terminal failure appends the
        matching step-failed event before the entry is marked
        ``ABANDONED``. The audit append always precedes the status change,
        so no recorded outcome lacks its audit record; if the sink is down
        the entry stays claimed and the lease heals it. Transient failures
        are not audited — the row's ``last_error`` carries the exception
        class name and the entry retries on the backoff schedule (a failed
        rectify row keeps its corrections payload; terminal rows never do).

        A retention-only resolver reporting a future horizon appends
        ``ERASURE_EXPIRY_SCHEDULED`` and parks the entry ``SCHEDULED``
        until the horizon (ADR 0018); the parked entry blocks
        ``ERASURE_COMPLETED`` until a later claim verifies the data gone
        (``already_absent=True``), which succeeds the step with
        ``verified_expiry``. A vendor that keeps reporting fresh horizons
        re-parks loudly each time — every slip is audited, never silent.

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

    async def _execute(
        self, entry: OutboxEntry
    ) -> ResolverErasure | ResolverRectification | ResolverScheduledErasure:
        """One resolver call, dispatched by operation; the gather captures raises.

        A rectify entry routed to a resolver without ``rectify_subject``
        raises :class:`~effaced.ResolverError`: the enqueuer never creates
        such an entry, but registries can change between enqueue and run,
        and a loud immediate abandonment is the same taxonomy as an
        unknown resolver name. An erase entry routed to a
        :class:`~effaced.RetentionOnlyResolver` calls ``schedule_erasure``
        instead of ``erase_subject`` — such a resolver cannot delete on
        demand (ADR 0018).
        """
        resolver = self._registry.get(entry.resolver)
        if entry.operation is OutboxOperation.RECTIFY:
            if not isinstance(resolver, RectifyingResolver):
                msg = f"resolver {entry.resolver!r} does not implement rectify_subject"
                raise ResolverError(msg)
            return await resolver.rectify_subject(entry.ref, entry.corrections)
        if isinstance(resolver, RetentionOnlyResolver):
            return await resolver.schedule_erasure(entry.ref)
        return await resolver.erase_subject(entry.ref)

    def _settle(
        self,
        entry: OutboxEntry,
        outcome: ResolverErasure | ResolverRectification | ResolverScheduledErasure | BaseException,
    ) -> None:
        """Book one entry's outcome: succeed, park until horizon, retry, or abandon."""
        if isinstance(outcome, ResolverScheduledErasure) and outcome.expires_at is not None:
            self._park(entry, expires_at=outcome.expires_at)
        elif isinstance(
            outcome, ResolverErasure | ResolverRectification | ResolverScheduledErasure
        ):
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

    def _succeed(
        self,
        entry: OutboxEntry,
        outcome: ResolverErasure | ResolverRectification | ResolverScheduledErasure,
    ) -> None:
        """Audit the success, then mark it; completion fires on the operation's last entry."""
        if isinstance(outcome, ResolverRectification):
            step_payload: dict[str, str | int | bool] = {
                "target": entry.resolver,
                "external": True,
                "already_consistent": outcome.already_consistent,
                "attempts": entry.attempts,
            }
            step_type = AuditEventType.RECTIFICATION_STEP_SUCCEEDED
            completed_type = AuditEventType.RECTIFICATION_COMPLETED
        elif isinstance(outcome, ResolverScheduledErasure):
            # Verified expiry: nothing was deleted by us, so no "strategy" key.
            step_payload = {
                "target": entry.resolver,
                "external": True,
                "verified_expiry": True,
                "already_absent": True,
                "attempts": entry.attempts,
            }
            step_type = AuditEventType.ERASURE_STEP_SUCCEEDED
            completed_type = AuditEventType.ERASURE_COMPLETED
        else:
            step_payload = {
                "target": entry.resolver,
                "strategy": ErasureStrategy.DELETE.value,
                "external": True,
                "already_absent": outcome.already_absent,
                "attempts": entry.attempts,
            }
            step_type = AuditEventType.ERASURE_STEP_SUCCEEDED
            completed_type = AuditEventType.ERASURE_COMPLETED
        self._audit.append(_event(step_type, entry.subject_id, step_payload))
        self._outbox.mark_succeeded(
            entry,
            on_subject_complete=lambda: self._audit.append(
                _event(completed_type, entry.subject_id, {})
            ),
        )

    def _park(self, entry: OutboxEntry, *, expires_at: datetime) -> None:
        """Audit the scheduled expiry, then park the entry until the horizon.

        The append precedes the park (ADR 0010's ordering rule), and the
        resume gate is clamped to at least one backoff step from now — a
        stale or past horizon must not hot-loop the entry. ``attempts``
        resets at the park (the ADR 0015 requeue precedent); the prior
        struggle is preserved here as ``prior_attempts``.
        """
        payload: dict[str, str | int | bool] = {
            "target": entry.resolver,
            "external": True,
            "expires_at": expires_at.astimezone(UTC).isoformat(),
            "prior_attempts": entry.attempts,
        }
        self._audit.append(
            _event(AuditEventType.ERASURE_EXPIRY_SCHEDULED, entry.subject_id, payload)
        )
        resume_at = max(expires_at, datetime.now(UTC) + self._backoff.delay(entry.attempts))
        self._outbox.mark_scheduled(entry, resume_at=resume_at)

    def _abandon(self, entry: OutboxEntry, exc: BaseException) -> None:
        """Audit the abandonment loudly, then mark the entry terminal."""
        if entry.operation is OutboxOperation.RECTIFY:
            payload: dict[str, str | int | bool] = {
                "target": entry.resolver,
                "external": True,
                "error": type(exc).__name__,
                "attempts": entry.attempts,
                "abandoned": True,
            }
            event_type = AuditEventType.RECTIFICATION_STEP_FAILED
        else:
            payload = {
                "target": entry.resolver,
                "strategy": ErasureStrategy.DELETE.value,
                "external": True,
                "error": type(exc).__name__,
                "attempts": entry.attempts,
                "abandoned": True,
            }
            event_type = AuditEventType.ERASURE_STEP_FAILED
        self._audit.append(_event(event_type, entry.subject_id, payload))
        self._outbox.mark_abandoned(entry, error=type(exc).__name__)
        self._notify_abandoned(entry, exc)

    def _notify_abandoned(self, entry: OutboxEntry, exc: BaseException) -> None:
        """Fire the abandonment hook, isolated from the transition it follows.

        Runs only after the entry is durably ``ABANDONED`` and its event is
        written, and swallows any ``Exception`` the hook raises — a slow or
        failing alerting backend must never corrupt or block the state
        transition or the audit trail. ``BaseException`` (cancellation) still
        propagates, matching the runner's outcome taxonomy.
        """
        if self._on_abandoned is None:
            return
        signal = AbandonedSignal(
            entry_id=entry.entry_id,
            subject_id=entry.subject_id,
            resolver=entry.resolver,
            operation=entry.operation,
            attempts=entry.attempts,
            error=type(exc).__name__,
        )
        with contextlib.suppress(Exception):
            self._on_abandoned.on_abandoned(signal)

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
    errors embed identifiers, and the trail must stay PII-free. Corrected
    values never appear in any event.
    """
    return AuditEvent(
        event_id=uuid4(),
        event_type=event_type,
        subject_ref=subject_id,
        occurred_at=datetime.now(UTC),
        payload=payload,
    )
