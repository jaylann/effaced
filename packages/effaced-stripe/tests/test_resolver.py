"""The Stripe resolver honours the resolver contract's shape."""

from __future__ import annotations

from effaced import Resolver
from effaced_stripe import StripeResolver


def test_satisfies_resolver_protocol() -> None:
    assert isinstance(StripeResolver(api_key="rk_test_x"), Resolver)


def test_name_is_stable() -> None:
    assert StripeResolver(api_key="rk_test_x").name == "stripe"
