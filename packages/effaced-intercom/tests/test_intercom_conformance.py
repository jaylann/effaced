"""IntercomResolver passes the shared resolver conformance suite.

Acceptance for issue #61: export shape (contact profile + conversation
metadata), erase idempotency (404-on-delete is success), the error
taxonomy, and the declared covered surface, all proven through the suite
shipped in effaced core — driven by the fake Intercom backend, no live
calls. IntercomResolver has no ``rectify_subject``, so the rectification
section skips by design.
"""

from __future__ import annotations

import httpx
from fake_intercom_transport import FakeIntercomTransport

from effaced import SubjectRef
from effaced.testing import ResolverConformanceSuite
from effaced_intercom import IntercomResolver

PRESENT = "5f7f0d217ef88b001234abcd"
ABSENT = "0000000000000000deadbeef"

CONTACT = {
    "type": "contact",
    "id": PRESENT,
    "email": "ada@example.com",
    "name": "Ada Lovelace",
    "phone": "+44 20 7946 0000",
}

CONVERSATIONS = [
    {
        "type": "conversation",
        "id": "conv-1",
        "created_at": 1700000000,
        "updated_at": 1700000600,
        "state": "closed",
    },
]

BEARER = "dG9rOnRlc3Q="


class TestIntercomResolverConformance(ResolverConformanceSuite):
    def make_resolver(self) -> IntercomResolver:
        fake = FakeIntercomTransport(
            contacts={PRESENT: CONTACT}, conversations={PRESENT: CONVERSATIONS}
        )
        return IntercomResolver(BEARER, transport=fake)

    def make_fully_populated_resolver(self) -> IntercomResolver:
        # CONTACT populates every covered profile field, and the seeded
        # conversation exercises every conversation glob (created_at,
        # updated_at, state).
        fake = FakeIntercomTransport(
            contacts={PRESENT: CONTACT}, conversations={PRESENT: CONVERSATIONS}
        )
        return IntercomResolver(BEARER, transport=fake)

    def make_present_ref(self) -> SubjectRef:
        return SubjectRef(kind="intercom", value=PRESENT)

    def make_absent_ref(self) -> SubjectRef:
        return SubjectRef(kind="intercom", value=ABSENT)

    def make_nonretryable_resolver(self) -> IntercomResolver:
        fake = FakeIntercomTransport(error_status=401)
        return IntercomResolver(BEARER, transport=fake)

    def make_transient_resolver(self) -> tuple[IntercomResolver, type[Exception]]:
        fake = FakeIntercomTransport(error_status=429)
        return (IntercomResolver(BEARER, transport=fake), httpx.HTTPStatusError)
