"""The :class:`VerifyingResolver` capability sub-protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from effaced.resolvers.base import Resolver

if TYPE_CHECKING:
    from effaced.annotations import SubjectRef
    from effaced.resolvers.verification import ResolverVerification


@runtime_checkable
class VerifyingResolver(Resolver, Protocol):
    """A :class:`~effaced.resolvers.Resolver` that can re-confirm absence (ADR 0027).

    Verification is an *optional* capability: the base ``Resolver``
    protocol stays additive-only, so implementing this sub-protocol is
    never required. A resolver that implements it offers an **independent
    read-back** — after the saga runner erases a subject and marks the
    entry succeeded, it narrows with ``isinstance`` and awaits
    :meth:`verify_absent`, recording the verdict. A registered resolver
    without ``verify_absent`` is skipped, never an error, exactly as with
    :class:`~effaced.RectifyingResolver` and
    :class:`~effaced.AttestingResolver`.

    The verdict is recorded but never acted on automatically: a
    :class:`~effaced.ResolverVerification` with ``confirmed_absent=False``
    is audited loudly (``ERASURE_EXTERNAL_VERIFICATION_FAILED``) as a
    discrepancy for an operator, but does not revert the erase or re-open
    the settled outbox entry (ADR 0027).

    Verification is a mechanism for an independent confirmation that the
    resolver's *own* erasure took effect — it re-queries the same surface
    the resolver reaches. It can never prove the external system holds no
    personal data the resolver does not reach, and is never a compliance
    determination.
    """

    async def verify_absent(self, ref: SubjectRef) -> ResolverVerification:
        """Re-query the external system and confirm the subject is gone.

        Called by the saga runner only after a successful on-demand erasure
        of the same subject. MUST be a read-back only — it never mutates
        the external system; it re-queries and reports.

        Args:
            ref: Opaque subject reference in this resolver's namespace.

        Returns:
            The read-back verdict; ``confirmed_absent=True`` means the
            subject's data is verifiably gone, ``False`` records a
            discrepancy with the erase's reported success.
        """
        ...
