"""The :class:`RetentionOnlyResolver` capability sub-protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from effaced.resolvers.base import Resolver

if TYPE_CHECKING:
    from effaced.annotations import SubjectRef
    from effaced.resolvers.scheduled_erasure import ResolverScheduledErasure


@runtime_checkable
class RetentionOnlyResolver(Resolver, Protocol):
    """A :class:`~effaced.resolvers.Resolver` that can only *schedule* erasure (ADR 0018).

    For external systems with no per-subject delete — call recordings,
    transcripts, vendors with fixed retention windows — the only honest
    erasure outcome is a **retention horizon**: "guaranteed gone by T".
    Implementing this sub-protocol routes the saga runner's erase entries
    to :meth:`schedule_erasure`; the entry parks until the horizon and is
    then re-verified, and ``ERASURE_COMPLETED`` fires only once the data
    is verifiably gone. A schedule is recorded as
    ``ERASURE_EXPIRY_SCHEDULED`` — never as a completed erasure.

    The structurally-required ``erase_subject`` MUST raise
    :class:`~effaced.exceptions.ResolverError`: returning a fabricated
    success would record a deletion that did not happen. The saga never
    calls it for these resolvers; the raise protects direct callers. A
    vendor that can delete some data on demand but only expire the rest is
    modeled as two resolvers with two ref kinds (ADR 0008 routing).

    The error taxonomy is unchanged:
    :class:`~effaced.exceptions.ResolverError` only for non-retryable
    failures; transient errors propagate untranslated for the saga runner
    to retry.
    """

    async def schedule_erasure(self, ref: SubjectRef) -> ResolverScheduledErasure:
        """Schedule the subject's erasure and report the retention horizon.

        Performs whatever marking the external system supports (a
        tombstone, a lifecycle tag, nothing at all) and reports the
        instant by which the data is guaranteed to expire. MUST be
        convergent: a subject the system no longer holds returns success
        with ``already_absent=True``; re-scheduling reports the
        same-or-later horizon, never an error.

        Args:
            ref: Opaque subject reference in this resolver's namespace.

        Returns:
            The schedule outcome; ``already_absent=True`` means verified
            expiry — that is success, not an error.
        """
        ...
