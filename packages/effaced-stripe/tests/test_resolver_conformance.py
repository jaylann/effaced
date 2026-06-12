"""StripeResolver passes the shared resolver conformance suite.

Acceptance for issues #17 and #96: export shape, erase idempotency
(404-on-delete is success), the error taxonomy, and rectify convergence
(``make_corrections`` activates the suite's rectify section), all proven
through the suite shipped in effaced core — driven by the fake Stripe
backend, no live calls.
"""

from __future__ import annotations

from fake_stripe_client import FakeStripeHTTPClient
from stripe import RateLimitError

from effaced import Correction, PiiCategory, SubjectRef
from effaced.testing import ResolverConformanceSuite
from effaced_stripe import StripeResolver

PRESENT = "cus_present"
ABSENT = "cus_absent"

CUSTOMER = {"email": "subject@example.com", "name": "Ada Lovelace"}


class TestStripeResolverConformance(ResolverConformanceSuite):
    def make_resolver(self) -> StripeResolver:
        fake = FakeStripeHTTPClient(customers={PRESENT: CUSTOMER})
        return StripeResolver(api_key="rk_test_x", http_client=fake)

    def make_present_ref(self) -> SubjectRef:
        return SubjectRef(kind="stripe", value=PRESENT)

    def make_absent_ref(self) -> SubjectRef:
        return SubjectRef(kind="stripe", value=ABSENT)

    def make_nonretryable_resolver(self) -> StripeResolver:
        fake = FakeStripeHTTPClient(error_status=401)
        return StripeResolver(api_key="rk_test_bad", http_client=fake)

    def make_transient_resolver(self) -> tuple[StripeResolver, type[Exception]]:
        fake = FakeStripeHTTPClient(error_status=429)
        return (
            StripeResolver(api_key="rk_test_x", http_client=fake),
            RateLimitError,
        )

    def make_corrections(self) -> tuple[Correction, ...]:
        return (
            Correction(category=PiiCategory.IDENTITY, value="Grace Hopper"),
            Correction(category=PiiCategory.CONTACT, value="grace@example.com"),
        )
