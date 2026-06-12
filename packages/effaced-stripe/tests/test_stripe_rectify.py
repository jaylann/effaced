"""Stripe-specific rectify behavior: curated mapping, drift, taxonomy.

Issue #96. The shared conformance suite proves the convergence contract
(present succeeds, idempotent second call, absent is consistent success);
these pin the Stripe-only specifics the suite cannot: the curated
single-field-per-category map, that only drifted fields are written, and
the error taxonomy on the modify path.
"""

from __future__ import annotations

import asyncio

import pytest
from fake_stripe_client import FakeStripeHTTPClient
from stripe import RateLimitError

from effaced import (
    Correction,
    PiiCategory,
    RectifyingResolver,
    ResolverError,
    ResolverRectification,
    SubjectRef,
)
from effaced_stripe import StripeResolver

CUSTOMER_ID = "cus_rect"
CUSTOMER = {"email": "ada@example.com", "name": "Ada Lovelace"}

IDENTITY_FIX = Correction(category=PiiCategory.IDENTITY, value="Grace Hopper")
CONTACT_FIX = Correction(category=PiiCategory.CONTACT, value="grace@example.com")


def make_resolver(fake: FakeStripeHTTPClient) -> StripeResolver:
    return StripeResolver(api_key="rk_test_x", http_client=fake)


def rectify(
    resolver: StripeResolver,
    corrections: tuple[Correction, ...],
    customer_id: str = CUSTOMER_ID,
) -> ResolverRectification:
    return asyncio.run(
        resolver.rectify_subject(SubjectRef(kind="stripe", value=customer_id), corrections)
    )


def test_resolver_structurally_satisfies_rectifying_resolver():
    assert isinstance(StripeResolver(api_key="rk_test_x"), RectifyingResolver)


def test_unmapped_category_is_a_noop_consistent_success():
    """A correction whose category maps to no Stripe field writes nothing.

    The customer is never even retrieved — nothing to converge to.
    """
    fake = FakeStripeHTTPClient(customers={CUSTOMER_ID: dict(CUSTOMER)})
    outcome = rectify(make_resolver(fake), (Correction(category=PiiCategory.FINANCIAL, value="x"),))
    assert outcome.already_consistent is True
    assert fake.requests == []


def test_partial_drift_writes_only_the_drifted_field():
    """Only the email drifts; name already matches → only email is sent."""
    fake = FakeStripeHTTPClient(
        customers={CUSTOMER_ID: {"email": "ada@example.com", "name": "Grace Hopper"}}
    )
    outcome = rectify(make_resolver(fake), (IDENTITY_FIX, CONTACT_FIX))
    assert outcome.already_consistent is False
    assert fake.customers[CUSTOMER_ID] == {
        "email": "grace@example.com",
        "name": "Grace Hopper",
    }
    posts = [query for method, query in fake.requests if method == "post"]
    assert len(posts) == 1


def test_second_identical_call_is_already_consistent():
    """Convergence: re-applying corrections Stripe already reflects no-ops."""
    fake = FakeStripeHTTPClient(customers={CUSTOMER_ID: dict(CUSTOMER)})
    resolver = make_resolver(fake)
    first = rectify(resolver, (IDENTITY_FIX, CONTACT_FIX))
    second = rectify(resolver, (IDENTITY_FIX, CONTACT_FIX))
    assert first.already_consistent is False
    assert second.already_consistent is True
    posts = [query for method, query in fake.requests if method == "post"]
    assert len(posts) == 1


def test_contact_writes_only_email_never_phone_or_address():
    """The curated CONTACT→email boundary (MAJOR if it ever widens).

    Stripe also files phone and address.* under CONTACT, but a single
    coarse scalar must never fan across them — that would manufacture
    inaccuracy. A CONTACT correction touches ``email`` and nothing else.
    """
    fake = FakeStripeHTTPClient(
        customers={
            CUSTOMER_ID: {
                "email": "ada@example.com",
                "phone": "+1 555 0100",
                "address": {"line1": "1 Analytical Way"},
            }
        }
    )
    rectify(make_resolver(fake), (CONTACT_FIX,))
    customer = fake.customers[CUSTOMER_ID]
    assert customer["email"] == "grace@example.com"
    assert customer["phone"] == "+1 555 0100"
    assert customer["address"] == {"line1": "1 Analytical Way"}


def test_non_string_correction_value_converges():
    """An int/float/bool scalar is coerced so drift detection terminates.

    Without coercion ``"7" != 7`` would drift forever and the saga would
    abandon a perfectly applicable correction.
    """
    fake = FakeStripeHTTPClient(customers={CUSTOMER_ID: {"name": "Ada Lovelace"}})
    resolver = make_resolver(fake)
    numeric = (Correction(category=PiiCategory.IDENTITY, value=7),)
    first = rectify(resolver, numeric)
    second = rectify(resolver, numeric)
    assert first.already_consistent is False
    assert second.already_consistent is True
    assert fake.customers[CUSTOMER_ID]["name"] == "7"


def test_absent_customer_is_consistent_success():
    outcome = rectify(make_resolver(FakeStripeHTTPClient()), (IDENTITY_FIX,))
    assert outcome.already_consistent is True
    assert outcome.detail == "customer absent in stripe"


def test_soft_deleted_customer_is_consistent_success():
    """A deleted-stub (HTTP 200, ``deleted: true``) short-circuits, no write."""
    fake = FakeStripeHTTPClient(customers={CUSTOMER_ID: dict(CUSTOMER)})
    resolver = make_resolver(fake)
    asyncio.run(resolver.erase_subject(SubjectRef(kind="stripe", value=CUSTOMER_ID)))
    outcome = rectify(resolver, (IDENTITY_FIX,))
    assert outcome.already_consistent is True
    assert outcome.detail == "customer absent in stripe"
    assert not any(method == "post" for method, _ in fake.requests)


def test_bad_key_on_modify_raises_resolver_error():
    resolver = make_resolver(FakeStripeHTTPClient(error_status=401))
    with pytest.raises(ResolverError):
        rectify(resolver, (IDENTITY_FIX,))


def test_rate_limit_on_modify_propagates_for_saga_retry():
    resolver = make_resolver(FakeStripeHTTPClient(error_status=429))
    with pytest.raises(RateLimitError):
        rectify(resolver, (IDENTITY_FIX,))


def test_rectify_error_message_never_leaks_the_subject_ref():
    sensitive_id = "cus_SENSITIVE_123"
    resolver = make_resolver(FakeStripeHTTPClient(error_status=400))
    with pytest.raises(ResolverError) as excinfo:
        rectify(resolver, (IDENTITY_FIX,), sensitive_id)
    assert sensitive_id not in str(excinfo.value)
