"""The :class:`StripeResolver` — billing PII, the one almost everyone has."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from stripe import (
    AuthenticationError,
    InvalidRequestError,
    StripeClient,
)
from stripe import (
    PermissionError as StripePermissionError,
)

from effaced.exceptions import ResolverError
from effaced.resolvers import ResolverErasure, ResolverExport
from effaced_stripe.errors import is_resource_missing
from effaced_stripe.export_records import customer_records, payment_method_records

if TYPE_CHECKING:
    from stripe import HTTPClient

    from effaced.annotations import SubjectRef
    from effaced.export import ExportRecord

_PAGE_SIZE = 100


def _collect_records(client: StripeClient, customer_id: str) -> tuple[ExportRecord, ...]:
    """Fetch the customer and its payment methods; a 404 propagates."""
    customer = client.v1.customers.retrieve(customer_id).to_dict()
    if customer.get("deleted"):
        return ()
    records = list(customer_records(customer))
    methods = client.v1.customers.payment_methods.list(customer_id, {"limit": _PAGE_SIZE})
    for method in methods.auto_paging_iter():
        records.extend(payment_method_records(method.to_dict()))
    return tuple(records)


class StripeResolver:
    """Exports and erases a subject's PII held in Stripe.

    Expects refs of kind ``"stripe"`` (refs are routed to the resolver
    whose name equals their kind — ADR 0008) whose value is the Stripe
    customer id. Erasure uses Stripe's customer deletion, which Stripe
    itself implements as a GDPR-aware redaction.

    Idempotency: a customer Stripe no longer knows yields
    ``already_absent=True`` — success, never an error.

    Error taxonomy (see :mod:`effaced_stripe.errors`): authentication,
    permission, and malformed-request failures raise
    :class:`~effaced.exceptions.ResolverError`; rate limits, connection
    faults, and Stripe-side errors propagate so the saga runner retries.
    SDK-internal retries are disabled (``max_network_retries=0``) — the
    saga runner owns retry and backoff (ADR 0010).
    """

    def __init__(self, api_key: str, *, http_client: HTTPClient | None = None) -> None:
        """Wire the resolver to a Stripe account.

        Args:
            api_key: A restricted Stripe API key with customer read/write.
                Prefer a restricted key over a full secret key.
            http_client: Optional transport override; tests inject a fake
                here so no call ever leaves the process.
        """
        self._client = StripeClient(api_key, http_client=http_client, max_network_retries=0)

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
            never full card numbers (Stripe does not expose them). Empty
            when Stripe holds no such customer.

        Raises:
            ResolverError: The key is invalid, lacks a permission, or the
                request was malformed — retrying cannot succeed.
        """
        try:
            records = await asyncio.to_thread(_collect_records, self._client, ref.value)
        except InvalidRequestError as error:
            if is_resource_missing(error):
                return ResolverExport(resolver=self.name)
            raise ResolverError(
                f"stripe rejected the export request (code={error.code})"
            ) from error
        except (AuthenticationError, StripePermissionError) as error:
            raise ResolverError("stripe key is invalid or lacks export permissions") from error
        return ResolverExport(resolver=self.name, records=records)

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        """Delete the customer in Stripe (Art. 17).

        Args:
            ref: ``kind="stripe"``, ``value=<customer id>``.

        Returns:
            The outcome; ``already_absent=True`` if Stripe already had no
            such customer.

        Raises:
            ResolverError: The key is invalid, lacks a permission, or the
                request was malformed — retrying cannot succeed.
        """
        try:
            await asyncio.to_thread(self._client.v1.customers.delete, ref.value)
        except InvalidRequestError as error:
            if is_resource_missing(error):
                return ResolverErasure(
                    resolver=self.name,
                    already_absent=True,
                    detail="customer already absent in stripe",
                )
            raise ResolverError(
                f"stripe rejected the erasure request (code={error.code})"
            ) from error
        except (AuthenticationError, StripePermissionError) as error:
            raise ResolverError("stripe key is invalid or lacks erasure permissions") from error
        return ResolverErasure(resolver=self.name, detail="customer deleted in stripe")
