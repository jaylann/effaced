"""The :class:`~effaced.CoveredSurface` :data:`STRIPE_COVERED_SURFACE` declares.

The covered fields are built directly from the ``_*_FIELDS`` tuples in
:mod:`effaced_stripe.export_records`, so the declaration and the exporter
cannot drift: adding a field to the exporter automatically widens the
declared surface, and the conformance suite proves every exported record
stays within it.

This is a declaration of *claimed* reach, never a compliance
determination: it makes explicit what the Stripe resolver's export and
erasure cover and — through the exclusions — what they knowingly do not.
"""

from __future__ import annotations

from effaced import CoveredField, CoveredSurface, PiiCategory, SurfaceExclusion
from effaced_stripe.export_records import (
    _ADDRESS_FIELDS,
    _BILLING_DETAILS_FIELDS,
    _CARD_FIELDS,
    _CUSTOMER_FIELDS,
    _PAYMENT_METHOD_FIELDS,
    _SHIPPING_FIELDS,
)


def _fields(prefix: str, pairs: tuple[tuple[str, PiiCategory], ...]) -> list[CoveredField]:
    """One covered field per exporter field, under the given glob prefix."""
    return [CoveredField(field=f"{prefix}.{key}", category=category) for key, category in pairs]


# Payment-method ids are dynamic, so their prefix is a single ``*`` glob —
# the exporter writes ``payment_method.<id>.card.brand`` and friends.
_PM = "payment_method.*"

_COVERED_FIELDS: tuple[CoveredField, ...] = tuple(
    _fields("customer", _CUSTOMER_FIELDS)
    + _fields("customer.address", _ADDRESS_FIELDS)
    + _fields("customer.shipping", _SHIPPING_FIELDS)
    + _fields("customer.shipping.address", _ADDRESS_FIELDS)
    + _fields(_PM, _PAYMENT_METHOD_FIELDS)
    + _fields(f"{_PM}.card", _CARD_FIELDS)
    + _fields(f"{_PM}.billing_details", _BILLING_DETAILS_FIELDS)
    + _fields(f"{_PM}.billing_details.address", _ADDRESS_FIELDS)
)

STRIPE_COVERED_SURFACE = CoveredSurface(
    resolver="stripe",
    fields=_COVERED_FIELDS,
    exclusions=(
        SurfaceExclusion(
            field="customer.metadata.*",
            reason="Customer metadata is caller-defined and unknowable; "
            "PII stashed there belongs to the application's own data map.",
        ),
        SurfaceExclusion(
            field="event.*",
            reason="Stripe retains event payloads beyond the customer-deletion "
            "API; they are outside this resolver's reach.",
        ),
        SurfaceExclusion(
            field="*.card.number",
            reason="Full card numbers are never exposed by the Stripe API, so "
            "they can neither be exported nor independently erased here.",
        ),
    ),
    notes=(
        "Export covers what the Stripe API exposes (profile, addresses, "
        "payment-method metadata); erasure deletes the customer object. "
        "Full PANs do not exist via the API and are never implied.",
    ),
)
"""Stripe's declared covered surface; see :class:`~effaced.CoveredSurface`."""
