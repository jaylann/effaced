"""The :class:`RectifyingResolver` capability sub-protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from effaced.resolvers.base import Resolver

if TYPE_CHECKING:
    from effaced.annotations import Correction, SubjectRef
    from effaced.resolvers.rectification import ResolverRectification


@runtime_checkable
class RectifyingResolver(Resolver, Protocol):
    """A :class:`~effaced.resolvers.Resolver` that can also rectify (Art. 16).

    Rectification is an *optional* capability: the base ``Resolver``
    protocol stays additive-only, so implementing this sub-protocol is
    never required. Call sites narrow with ``isinstance`` — a registered
    resolver without ``rectify_subject`` is skipped and recorded, never an
    error (ADR 0013).

    Implementations MUST be convergent: re-applying corrections the
    external system already reflects returns success with
    ``already_consistent=True``, the rectification analogue of erasure's
    ``already_absent``. The same error taxonomy applies —
    :class:`~effaced.exceptions.ResolverError` only for non-retryable
    failures; transient errors propagate untranslated for the saga runner
    to retry.
    """

    async def rectify_subject(
        self, ref: SubjectRef, corrections: tuple[Correction, ...]
    ) -> ResolverRectification:
        """Apply the corrections to the subject in the external system.

        Args:
            ref: Opaque subject reference in this resolver's namespace.
            corrections: Category-keyed corrected values to apply.

        Returns:
            The outcome; ``already_consistent=True`` when the system
            already reflected every correction — that is success, not an
            error.
        """
        ...
