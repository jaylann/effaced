"""ResendResolver passes the shared resolver conformance suite.

Acceptance for issue #60: export shape, erase idempotency (404-on-delete
is success), and the error taxonomy, all proven through the suite shipped
in effaced core — driven by the fake Resend backend, no live calls.
ResendResolver has no ``rectify_subject`` (Resend cannot update a
contact's email), so the rectification section skips by design.
"""

from __future__ import annotations

import httpx
from fake_resend_transport import FakeResendTransport

from effaced import SubjectRef
from effaced.testing import ResolverConformanceSuite
from effaced_resend import ResendResolver

PRESENT = "subject@example.com"
ABSENT = "ghost@example.com"

CONTACT = {
    "id": "e169aa45-1ecf-4183-9955-b1499d5701d3",
    "email": PRESENT,
    "first_name": "Ada",
    "last_name": "Lovelace",
    "unsubscribed": False,
}

KEY = "re_test_key"


class TestResendResolverConformance(ResolverConformanceSuite):
    def make_resolver(self) -> ResendResolver:
        fake = FakeResendTransport(contacts={PRESENT: CONTACT})
        return ResendResolver(KEY, transport=fake)

    def make_present_ref(self) -> SubjectRef:
        return SubjectRef(kind="resend", value=PRESENT)

    def make_absent_ref(self) -> SubjectRef:
        return SubjectRef(kind="resend", value=ABSENT)

    def make_nonretryable_resolver(self) -> ResendResolver:
        fake = FakeResendTransport(error_status=401)
        return ResendResolver(KEY, transport=fake)

    def make_transient_resolver(self) -> tuple[ResendResolver, type[Exception]]:
        fake = FakeResendTransport(error_status=429)
        return (ResendResolver(KEY, transport=fake), httpx.HTTPStatusError)
