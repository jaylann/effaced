"""The :class:`OutboxStatus` vocabulary."""

from __future__ import annotations

from enum import StrEnum


class OutboxStatus(StrEnum):
    """Lifecycle of one durable external-call entry."""

    PENDING = "pending"
    """Enqueued, not yet attempted."""

    IN_FLIGHT = "in_flight"
    """Claimed by a runner; being attempted right now."""

    SUCCEEDED = "succeeded"
    """The external call completed (including "already gone")."""

    FAILED = "failed"
    """Last attempt failed; will be retried with backoff."""

    SCHEDULED = "scheduled"
    """Erasure scheduled to expire externally (ADR 0022); parked until
    the retention horizon, then re-claimed to verify the data is gone."""

    ABANDONED = "abandoned"
    """Retries exhausted; surfaced loudly for operator action — never
    silently dropped, the audit trail records the abandonment."""
