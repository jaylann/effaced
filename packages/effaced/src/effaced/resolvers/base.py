"""The :class:`Resolver` protocol — effaced's reach beyond your database.

This protocol is **public API** with the strictest stability promise in the
library: it is extended additively only (optional methods with default
implementations). Custom resolvers written against v0.x must keep working
across every release in their major version.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from effaced.annotations import SubjectRef
    from effaced.resolvers.results import ResolverErasure, ResolverExport


@runtime_checkable
class Resolver(Protocol):
    """One external system that holds personal data (Stripe, S3, a CRM).

    Implementations MUST be idempotent: erasing a subject that is already
    gone returns success (``already_absent=True``), never an error. The
    saga runner retries failed calls; a non-idempotent resolver would turn
    retries into errors or double-effects.

    Implementations MUST raise
    :class:`~effaced.exceptions.ResolverError` only for non-retryable
    failures; transient errors (timeouts, rate limits) should raise the
    underlying exception and let the runner retry.

    Implementations MUST NOT bind event-loop-affine resources (async HTTP
    clients) at construction — create them inside the call. Resolver
    methods may be driven from different event loops: the exporter's
    per-call loop and the saga runner's loop (ADR 0006).

    Resolvers that can also correct a subject's data (Art. 16) implement
    the optional :class:`~effaced.RectifyingResolver` capability
    sub-protocol on top — this base protocol itself only ever grows
    additively.
    """

    @property
    def name(self) -> str:
        """Stable, unique resolver name (``"stripe"``); recorded in audits."""
        ...

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        """Collect what the external system holds on the subject (Art. 15).

        Args:
            ref: Opaque subject reference in this resolver's namespace.

        Returns:
            The system's records for the subject; empty when it holds none.
        """
        ...

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        """Erase the subject from the external system (Art. 17).

        Args:
            ref: Opaque subject reference in this resolver's namespace.

        Returns:
            The outcome; ``already_absent=True`` when there was nothing to
            erase — that is success, not an error.
        """
        ...
