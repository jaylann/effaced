"""The :class:`AttestingResolver` capability sub-protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from effaced.resolvers.base import Resolver

if TYPE_CHECKING:
    from effaced.resolvers.covered_surface import CoveredSurface


@runtime_checkable
class AttestingResolver(Resolver, Protocol):
    """A :class:`~effaced.resolvers.Resolver` that declares its covered surface.

    Attesting is an *optional* capability: the base ``Resolver`` protocol
    stays additive-only, so implementing this sub-protocol is never
    required. A resolver that implements it publishes a
    :class:`~effaced.CoveredSurface` — the PII-bearing fields its export
    and erasure claim to reach, the fields it knowingly does not, and any
    asymmetries. Call sites narrow with ``isinstance``; a registered
    resolver without ``covered_surface`` is skipped, never an error,
    exactly as with :class:`~effaced.RectifyingResolver`.

    The declaration is a mechanism for making *claimed* coverage explicit
    and testable (the conformance suite checks exports stay within it and
    exclusions stay absent). It can never prove the external system holds
    no personal data the resolver does not reach, and is never a
    compliance determination.
    """

    @property
    def covered_surface(self) -> CoveredSurface:
        """The PII this resolver claims to reach, plus its declared gaps.

        Returns:
            The :class:`~effaced.CoveredSurface` whose ``resolver`` equals
            this resolver's :attr:`~effaced.Resolver.name`.
        """
        ...
