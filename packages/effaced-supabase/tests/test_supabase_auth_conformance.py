"""SupabaseAuthResolver passes the shared resolver conformance suite.

Acceptance for issue #56: export shape, erase idempotency (404-on-delete
is success), and the error taxonomy, all proven through the suite shipped
in effaced core — driven by the fake GoTrue backend, no live calls.
"""

from __future__ import annotations

import httpx
from fake_gotrue_transport import FakeGoTrueTransport

from effaced import SubjectRef
from effaced.testing import ResolverConformanceSuite
from effaced_supabase import SupabaseAuthResolver

PRESENT = "00000000-0000-4000-8000-000000000001"
ABSENT = "00000000-0000-4000-8000-00000000000f"

USER = {"email": "subject@example.com", "phone": "4915112345678"}

BASE_URL = "https://project.supabase.co"
KEY = "service-role-test-key"


class TestSupabaseAuthResolverConformance(ResolverConformanceSuite):
    def make_resolver(self) -> SupabaseAuthResolver:
        fake = FakeGoTrueTransport(users={PRESENT: USER})
        return SupabaseAuthResolver(BASE_URL, KEY, transport=fake)

    def make_present_ref(self) -> SubjectRef:
        return SubjectRef(kind="supabase_auth", value=PRESENT)

    def make_absent_ref(self) -> SubjectRef:
        return SubjectRef(kind="supabase_auth", value=ABSENT)

    def make_nonretryable_resolver(self) -> SupabaseAuthResolver:
        fake = FakeGoTrueTransport(error_status=401)
        return SupabaseAuthResolver(BASE_URL, KEY, transport=fake)

    def make_transient_resolver(self) -> tuple[SupabaseAuthResolver, type[Exception]]:
        fake = FakeGoTrueTransport(error_status=429)
        return (
            SupabaseAuthResolver(BASE_URL, KEY, transport=fake),
            httpx.HTTPStatusError,
        )
