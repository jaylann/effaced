"""Mapping of Stripe API objects to :class:`~effaced.ExportRecord` rows.

The exported field set is behaviour under widened SemVer: adding fields
is MINOR, removing or recategorising any is MAJOR. Full card numbers are
never exported — Stripe does not expose them over the API. Customer
``metadata`` is caller-defined and unknowable, so it is not exported.

``legal_basis``, ``purpose`` and ``retention_reason`` stay ``None`` on
every record: a resolver cannot know why the application holds the data;
that metadata belongs to the manifest-declared local data map.
"""

from __future__ import annotations

from collections.abc import Mapping

from effaced import ExportRecord, PiiCategory

_SOURCE = "stripe"

_ADDRESS_FIELDS = tuple(
    (key, PiiCategory.CONTACT)
    for key in ("line1", "line2", "city", "postal_code", "state", "country")
)

_CUSTOMER_FIELDS = (
    ("email", PiiCategory.CONTACT),
    ("name", PiiCategory.IDENTITY),
    ("phone", PiiCategory.CONTACT),
)

_SHIPPING_FIELDS = (
    ("name", PiiCategory.IDENTITY),
    ("phone", PiiCategory.CONTACT),
)

_CARD_FIELDS = (
    ("brand", PiiCategory.FINANCIAL),
    ("last4", PiiCategory.FINANCIAL),
    ("exp_month", PiiCategory.FINANCIAL),
    ("exp_year", PiiCategory.FINANCIAL),
)

_BILLING_DETAILS_FIELDS = (
    ("name", PiiCategory.IDENTITY),
    ("email", PiiCategory.CONTACT),
    ("phone", PiiCategory.CONTACT),
)


def _scalar(source: Mapping[str, object] | None, key: str) -> str | int | None:
    """Fetch a loggable scalar; absent, null, or odd-typed values drop."""
    value = None if source is None else source.get(key)
    if isinstance(value, bool) or not isinstance(value, str | int):
        return None
    return value


def _mapping(source: Mapping[str, object] | None, key: str) -> Mapping[str, object] | None:
    """Fetch a nested object; anything that is not a mapping drops."""
    value = None if source is None else source.get(key)
    return value if isinstance(value, Mapping) else None


def _records(
    source: Mapping[str, object] | None,
    prefix: str,
    fields: tuple[tuple[str, PiiCategory], ...],
) -> list[ExportRecord]:
    """One record per field present on ``source``; ``None`` values skip."""
    return [
        ExportRecord(source=_SOURCE, field=f"{prefix}.{key}", category=category, value=value)
        for key, category in fields
        if (value := _scalar(source, key)) is not None
    ]


def _address_records(prefix: str, address: Mapping[str, object] | None) -> list[ExportRecord]:
    """Postal-address components, all :attr:`PiiCategory.CONTACT`."""
    return _records(address, prefix, _ADDRESS_FIELDS)


def customer_records(customer: Mapping[str, object]) -> tuple[ExportRecord, ...]:
    """Map a Stripe customer's profile, address, and shipping contact.

    Args:
        customer: A Stripe ``Customer`` API object (mapping view).

    Returns:
        One record per populated field; nothing for absent/null fields.
    """
    shipping = _mapping(customer, "shipping")
    return tuple(
        _records(customer, "customer", _CUSTOMER_FIELDS)
        + _address_records("customer.address", _mapping(customer, "address"))
        + _records(shipping, "customer.shipping", _SHIPPING_FIELDS)
        + _address_records("customer.shipping.address", _mapping(shipping, "address"))
    )


def payment_method_records(method: Mapping[str, object]) -> tuple[ExportRecord, ...]:
    """Map one payment method's metadata — never a full card number.

    Stripe's API only exposes card metadata (brand, last4, expiry); full
    PANs are not retrievable and therefore can never appear in an export.

    Args:
        method: A Stripe ``PaymentMethod`` API object (mapping view).

    Returns:
        Records for the method's type, card metadata, and billing details.
    """
    prefix = f"payment_method.{_scalar(method, 'id')}"
    billing = _mapping(method, "billing_details")
    return tuple(
        _records(method, prefix, (("type", PiiCategory.FINANCIAL),))
        + _records(_mapping(method, "card"), f"{prefix}.card", _CARD_FIELDS)
        + _records(billing, f"{prefix}.billing_details", _BILLING_DETAILS_FIELDS)
        + _address_records(f"{prefix}.billing_details.address", _mapping(billing, "address"))
    )
