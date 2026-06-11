"""Stripe-specific resolver behavior: mapping, pagination, error taxonomy."""

from __future__ import annotations

import asyncio
import re

import pytest
from fake_stripe_client import FakeStripeHTTPClient
from stripe import APIConnectionError, APIError, AuthenticationError, RateLimitError

from effaced import PiiCategory, Resolver, ResolverError, SubjectRef
from effaced_stripe import StripeResolver

CUSTOMER_ID = "cus_full"

FULL_CUSTOMER = {
    "email": "ada@example.com",
    "name": "Ada Lovelace",
    "phone": "+44 20 7946 0000",
    "address": {
        "line1": "1 Analytical Way",
        "line2": "Floor 2",
        "city": "London",
        "postal_code": "EC1A 1BB",
        "state": "London",
        "country": "GB",
    },
    "shipping": {
        "name": "Ada Lovelace",
        "phone": "+44 20 7946 0001",
        "address": {"line1": "2 Engine House", "city": "London", "country": "GB"},
    },
}

CARD_PM = {
    "id": "pm_1",
    "type": "card",
    "card": {"brand": "visa", "last4": "4242", "exp_month": 12, "exp_year": 2030},
    "billing_details": {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "phone": "+44 20 7946 0000",
        "address": {"line1": "1 Analytical Way", "country": "GB"},
    },
}

EXPECTED_FULL_EXPORT = {
    "customer.email": ("ada@example.com", PiiCategory.CONTACT),
    "customer.name": ("Ada Lovelace", PiiCategory.IDENTITY),
    "customer.phone": ("+44 20 7946 0000", PiiCategory.CONTACT),
    "customer.address.line1": ("1 Analytical Way", PiiCategory.CONTACT),
    "customer.address.line2": ("Floor 2", PiiCategory.CONTACT),
    "customer.address.city": ("London", PiiCategory.CONTACT),
    "customer.address.postal_code": ("EC1A 1BB", PiiCategory.CONTACT),
    "customer.address.state": ("London", PiiCategory.CONTACT),
    "customer.address.country": ("GB", PiiCategory.CONTACT),
    "customer.shipping.name": ("Ada Lovelace", PiiCategory.IDENTITY),
    "customer.shipping.phone": ("+44 20 7946 0001", PiiCategory.CONTACT),
    "customer.shipping.address.line1": ("2 Engine House", PiiCategory.CONTACT),
    "customer.shipping.address.city": ("London", PiiCategory.CONTACT),
    "customer.shipping.address.country": ("GB", PiiCategory.CONTACT),
    "payment_method.pm_1.type": ("card", PiiCategory.FINANCIAL),
    "payment_method.pm_1.card.brand": ("visa", PiiCategory.FINANCIAL),
    "payment_method.pm_1.card.last4": ("4242", PiiCategory.FINANCIAL),
    "payment_method.pm_1.card.exp_month": (12, PiiCategory.FINANCIAL),
    "payment_method.pm_1.card.exp_year": (2030, PiiCategory.FINANCIAL),
    "payment_method.pm_1.billing_details.name": ("Ada Lovelace", PiiCategory.IDENTITY),
    "payment_method.pm_1.billing_details.email": (
        "ada@example.com",
        PiiCategory.CONTACT,
    ),
    "payment_method.pm_1.billing_details.phone": (
        "+44 20 7946 0000",
        PiiCategory.CONTACT,
    ),
    "payment_method.pm_1.billing_details.address.line1": (
        "1 Analytical Way",
        PiiCategory.CONTACT,
    ),
    "payment_method.pm_1.billing_details.address.country": (
        "GB",
        PiiCategory.CONTACT,
    ),
}


def make_resolver(fake: FakeStripeHTTPClient) -> StripeResolver:
    return StripeResolver(api_key="rk_test_x", http_client=fake)


def export(resolver: StripeResolver, customer_id: str = CUSTOMER_ID):
    return asyncio.run(resolver.export_subject(SubjectRef(kind="stripe", value=customer_id)))


def erase(resolver: StripeResolver, customer_id: str = CUSTOMER_ID):
    return asyncio.run(resolver.erase_subject(SubjectRef(kind="stripe", value=customer_id)))


def test_satisfies_resolver_protocol():
    assert isinstance(StripeResolver(api_key="rk_test_x"), Resolver)


def test_name_is_stable():
    assert StripeResolver(api_key="rk_test_x").name == "stripe"


def test_export_maps_customer_profile_addresses_and_card_metadata():
    fake = FakeStripeHTTPClient(
        customers={CUSTOMER_ID: FULL_CUSTOMER},
        payment_methods={CUSTOMER_ID: [CARD_PM]},
    )
    bundle = export(make_resolver(fake))
    assert {
        record.field: (record.value, record.category) for record in bundle.records
    } == EXPECTED_FULL_EXPORT
    assert all(record.source == "stripe" for record in bundle.records)
    assert all(record.legal_basis is None for record in bundle.records)


def test_export_skips_absent_and_null_fields():
    fake = FakeStripeHTTPClient(customers={CUSTOMER_ID: {"email": "ada@example.com", "name": None}})
    bundle = export(make_resolver(fake))
    assert [(r.field, r.value) for r in bundle.records] == [("customer.email", "ada@example.com")]


def test_export_of_deleted_customer_is_empty():
    fake = FakeStripeHTTPClient(customers={CUSTOMER_ID: FULL_CUSTOMER})
    resolver = make_resolver(fake)
    erase(resolver)
    assert export(resolver).records == ()


def test_export_paginates_payment_methods():
    methods = [{"id": f"pm_{n}", "type": "card", "card": {"last4": f"{n:04d}"}} for n in range(7)]
    fake = FakeStripeHTTPClient(
        customers={CUSTOMER_ID: {"email": "ada@example.com"}},
        payment_methods={CUSTOMER_ID: methods},
        page_limit=3,
    )
    bundle = export(make_resolver(fake))
    last4s = {r.value for r in bundle.records if r.field.endswith("card.last4")}
    assert last4s == {f"{n:04d}" for n in range(7)}
    list_requests = [query for _, query in fake.requests if "payment_methods" in query]
    assert len(list_requests) >= 2
    assert any("starting_after=" in query for query in list_requests)


def test_export_never_contains_pan_like_values():
    fake = FakeStripeHTTPClient(
        customers={CUSTOMER_ID: FULL_CUSTOMER},
        payment_methods={CUSTOMER_ID: [CARD_PM]},
    )
    bundle = export(make_resolver(fake))
    assert not any(record.field.endswith(".number") for record in bundle.records)
    for record in bundle.records:
        assert not re.fullmatch(r"\d{13,19}", str(record.value))


def test_erase_deletes_customer_and_reports_detail():
    fake = FakeStripeHTTPClient(customers={CUSTOMER_ID: FULL_CUSTOMER})
    outcome = erase(make_resolver(fake))
    assert outcome.already_absent is False
    assert outcome.detail == "customer deleted in stripe"
    assert CUSTOMER_ID in fake.deleted


def test_erase_of_unknown_customer_is_already_absent():
    outcome = erase(make_resolver(FakeStripeHTTPClient()))
    assert outcome.already_absent is True
    assert outcome.detail == "customer already absent in stripe"


def test_erase_touches_only_the_requested_customer():
    fake = FakeStripeHTTPClient(
        customers={CUSTOMER_ID: FULL_CUSTOMER, "cus_other": {"email": "bob@example.com"}},
        payment_methods={"cus_other": [dict(CARD_PM, id="pm_other")]},
    )
    resolver = make_resolver(fake)
    erase(resolver)
    other = export(resolver, "cus_other")
    assert ("customer.email", "bob@example.com") in [(r.field, r.value) for r in other.records]
    assert any(r.field.startswith("payment_method.pm_other.") for r in other.records)
    assert fake.deleted == {CUSTOMER_ID}


def test_connection_fault_propagates_for_saga_retry():
    resolver = make_resolver(FakeStripeHTTPClient(connection_error=True))
    with pytest.raises(APIConnectionError):
        export(resolver)
    with pytest.raises(APIConnectionError):
        erase(resolver)


def test_rate_limit_propagates_for_saga_retry():
    resolver = make_resolver(FakeStripeHTTPClient(error_status=429))
    with pytest.raises(RateLimitError):
        export(resolver)
    with pytest.raises(RateLimitError):
        erase(resolver)


def test_stripe_outage_propagates_for_saga_retry():
    resolver = make_resolver(FakeStripeHTTPClient(error_status=500))
    with pytest.raises(APIError):
        export(resolver)
    with pytest.raises(APIError):
        erase(resolver)


def test_bad_key_raises_resolver_error():
    resolver = make_resolver(FakeStripeHTTPClient(error_status=401))
    with pytest.raises(ResolverError) as excinfo:
        export(resolver)
    assert isinstance(excinfo.value.__cause__, AuthenticationError)
    with pytest.raises(ResolverError):
        erase(resolver)


def test_missing_permission_raises_resolver_error():
    resolver = make_resolver(FakeStripeHTTPClient(error_status=403))
    with pytest.raises(ResolverError):
        export(resolver)
    with pytest.raises(ResolverError):
        erase(resolver)


def test_malformed_request_raises_resolver_error():
    resolver = make_resolver(FakeStripeHTTPClient(error_status=400))
    with pytest.raises(ResolverError):
        export(resolver)
    with pytest.raises(ResolverError):
        erase(resolver)


def test_resolver_error_messages_never_leak_the_subject_ref():
    """The customer id is a subject reference — it must not reach the message.

    The translation interpolates only Stripe's error code, never the ref;
    this pins that, so a future regression piping ``ref.value`` into a
    ResolverError is caught (semgrep's no-PII gate watches audit payloads,
    not exception strings).
    """
    sensitive_id = "cus_SENSITIVE_123"
    for status in (401, 400):
        resolver = make_resolver(FakeStripeHTTPClient(error_status=status))
        with pytest.raises(ResolverError) as export_error:
            export(resolver, sensitive_id)
        with pytest.raises(ResolverError) as erase_error:
            erase(resolver, sensitive_id)
        assert sensitive_id not in str(export_error.value)
        assert sensitive_id not in str(erase_error.value)


def test_export_uses_fallback_prefix_for_a_payment_method_without_id():
    """Live Stripe always sends an id; the fallback keeps prefixes unique anyway."""
    fake = FakeStripeHTTPClient(
        customers={CUSTOMER_ID: {"email": "ada@example.com"}},
        payment_methods={CUSTOMER_ID: [{"type": "card", "card": {"last4": "4242"}}]},
    )
    bundle = export(make_resolver(fake))
    assert any(record.field.startswith("payment_method.index-0.") for record in bundle.records)


def test_export_drops_boolean_valued_fields():
    """A boolean where a scalar is expected is not loggable PII — it is dropped."""
    fake = FakeStripeHTTPClient(customers={CUSTOMER_ID: {"email": "ada@example.com", "name": True}})
    bundle = export(make_resolver(fake))
    fields = {record.field for record in bundle.records}
    assert "customer.email" in fields
    assert "customer.name" not in fields
