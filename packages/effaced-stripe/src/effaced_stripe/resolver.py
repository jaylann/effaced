"""The :class:`StripeResolver` — billing PII, the one almost everyone has."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from effaced.annotations import SubjectRef
    from effaced.resolvers import ResolverErasure, ResolverExport


class StripeResolver:
    """Exports and erases a subject's PII held in Stripe.

    Expects refs of kind ``"stripe"`` (refs are routed to the resolver
    whose name equals their kind — ADR 0008) whose value is the Stripe
    customer id. Erasure uses Stripe's customer deletion, which Stripe
    itself implements as a GDPR-aware redaction.

    Idempotency: a customer Stripe no longer knows yields
    ``already_absent=True`` — success, never an error.
    """

    def __init__(self, api_key: str) -> None:
        """Wire the resolver to a Stripe account.

        Args:
            api_key: A restricted Stripe API key with customer read/write.
                Prefer a restricted key over a full secret key.
        """
        self._api_key = api_key

    @property
    def name(self) -> str:
        """Stable resolver name recorded in manifests and audits."""
        return "stripe"

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        """Collect the customer's Stripe-held PII (Art. 15).

        Args:
            ref: ``kind="stripe"``, ``value=<customer id>``.

        Returns:
            Customer profile, addresses, and payment-method metadata —
            never full card numbers (Stripe does not expose them).
        """
        raise NotImplementedError

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        """Delete the customer in Stripe (Art. 17).

        Args:
            ref: ``kind="stripe"``, ``value=<customer id>``.

        Returns:
            The outcome; ``already_absent=True`` if Stripe already had no
            such customer.
        """
        raise NotImplementedError
