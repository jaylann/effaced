"""The :class:`StripeResolver` — billing PII, the one almost everyone has."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from stripe import (
    AuthenticationError,
    InvalidRequestError,
    StripeClient,
)
from stripe import (
    PermissionError as StripePermissionError,
)

from effaced import PiiCategory
from effaced.exceptions import ResolverError
from effaced.resolvers import ResolverErasure, ResolverExport, ResolverRectification
from effaced_stripe.errors import is_resource_missing
from effaced_stripe.export_records import customer_records, payment_method_records

if TYPE_CHECKING:
    from stripe import HTTPClient
    from stripe.params._customer_update_params import CustomerUpdateParams

    from effaced.annotations import Correction, SubjectRef
    from effaced.export import ExportRecord

_PAGE_SIZE = 100

# Curated single-field-per-category map (ADR 0013, issue #96). Only
# categories whose Stripe target is unambiguous from one scalar are
# rectifiable: IDENTITY is the customer name, CONTACT is the email.
# Stripe also files phone and address.* under CONTACT, but a coarse
# category-keyed scalar cannot disambiguate email vs phone vs address, so
# fanning one value across all three would manufacture fresh inaccuracy
# (anti-Art. 16). Those targets are deliberately left to a future,
# field-keyed correction vocabulary.
_CATEGORY_TO_FIELD: dict[PiiCategory, str] = {
    PiiCategory.IDENTITY: "name",
    PiiCategory.CONTACT: "email",
}


def _collect_records(client: StripeClient, customer_id: str) -> tuple[ExportRecord, ...]:
    """Fetch the customer and its payment methods; a 404 propagates."""
    customer = client.v1.customers.retrieve(customer_id).to_dict()
    if customer.get("deleted"):
        return ()
    records = list(customer_records(customer))
    methods = client.v1.customers.payment_methods.list(customer_id, {"limit": _PAGE_SIZE})
    for index, method in enumerate(methods.auto_paging_iter()):
        records.extend(payment_method_records(method.to_dict(), fallback_id=f"index-{index}"))
    return tuple(records)


class StripeResolver:
    """Exports, erases, and rectifies a subject's PII held in Stripe.

    Expects refs of kind ``"stripe"`` (refs are routed to the resolver
    whose name equals their kind — ADR 0008) whose value is the Stripe
    customer id. Erasure uses Stripe's customer deletion, which Stripe
    itself implements as a GDPR-aware redaction.

    Idempotency: a customer Stripe no longer knows yields
    ``already_absent=True`` on erasure and ``already_consistent=True`` on
    rectification — success, never an error. Rectification is convergent
    and structurally satisfies :class:`~effaced.resolvers.RectifyingResolver`
    (see :meth:`rectify_subject` for the curated category mapping).

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

    async def rectify_subject(
        self, ref: SubjectRef, corrections: tuple[Correction, ...]
    ) -> ResolverRectification:
        """Apply category-keyed corrections to the customer (Art. 16).

        Maps corrections onto Stripe ``Customer`` fields through a curated,
        single-field-per-category table: :attr:`PiiCategory.IDENTITY` to
        ``name`` and :attr:`PiiCategory.CONTACT` to ``email``. Every other
        category is ignored — a category that maps to no field is a
        complete answer, not a failure (ADR 0013).

        Deliberate limitation: Stripe also files ``phone`` and the
        ``address.*`` components under :attr:`PiiCategory.CONTACT`, but a
        coarse category-keyed scalar cannot tell email from phone from a
        postal address. Writing one value to all three would manufacture
        fresh inaccuracy, which Art. 16 exists to prevent — so only
        ``email`` is rectified for CONTACT. Field-keyed corrections are
        left to a future, finer vocabulary.

        Convergence: the customer is retrieved first; only fields that
        actually drift from their correction are written, and a customer
        Stripe no longer holds is reported ``already_consistent=True``.
        Re-applying corrections Stripe already reflects writes nothing and
        returns ``already_consistent=True`` — the rectification analogue of
        erasure's ``already_absent``, which saga retries depend on.

        Args:
            ref: ``kind="stripe"``, ``value=<customer id>``.
            corrections: Category-keyed corrected values to apply.

        Returns:
            The outcome; ``already_consistent=True`` when nothing mapped,
            the customer was absent, or Stripe already held every target
            value.

        Raises:
            ResolverError: The key is invalid, lacks a permission, or the
                request was malformed — retrying cannot succeed.
        """
        # ``name`` and ``email`` are free-text Stripe string fields; coerce
        # the correction scalar so the drift check and the written value
        # are both strings (Stripe stores everything as text). Without
        # this, an int-valued correction would compare ``"42" != 42``
        # forever and the saga would never converge.
        targets = {
            field: str(correction.value)
            for correction in corrections
            if (field := _CATEGORY_TO_FIELD.get(correction.category)) is not None
        }
        if not targets:
            return ResolverRectification(
                resolver=self.name,
                already_consistent=True,
                detail="no corrections map to a stripe field",
            )
        try:
            customer = await asyncio.to_thread(self._retrieve_customer, ref.value)
            if customer.get("deleted"):
                return ResolverRectification(
                    resolver=self.name,
                    already_consistent=True,
                    detail="customer absent in stripe",
                )
            drift = {
                field: value for field, value in targets.items() if customer.get(field) != value
            }
            if not drift:
                return ResolverRectification(
                    resolver=self.name,
                    already_consistent=True,
                    detail="stripe already reflects the corrections",
                )
            params = cast("CustomerUpdateParams", drift)
            await asyncio.to_thread(self._client.v1.customers.update, ref.value, params)
        except InvalidRequestError as error:
            if is_resource_missing(error):
                return ResolverRectification(
                    resolver=self.name,
                    already_consistent=True,
                    detail="customer absent in stripe",
                )
            raise ResolverError(
                f"stripe rejected the rectification request (code={error.code})"
            ) from error
        except (AuthenticationError, StripePermissionError) as error:
            raise ResolverError(
                "stripe key is invalid or lacks rectification permissions"
            ) from error
        return ResolverRectification(resolver=self.name, detail="customer corrected in stripe")

    def _retrieve_customer(self, customer_id: str) -> dict[str, object]:
        """Fetch the customer as a mapping; a 404 propagates."""
        return self._client.v1.customers.retrieve(customer_id).to_dict()
