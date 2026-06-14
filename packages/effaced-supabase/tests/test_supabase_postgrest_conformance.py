"""SupabasePostgrestResolver passes the shared resolver conformance suite.

Acceptance for issue #70: export shape, erase idempotency (an empty
representation across every declared table is success), the error
taxonomy, and the covered-surface contract, all proven through the suite
shipped in effaced core — driven by the fake PostgREST backend, no live
calls.
"""

from __future__ import annotations

import httpx
from fake_postgrest_transport import FakePostgrestTransport

from effaced import PiiCategory, SubjectRef
from effaced.testing import ResolverConformanceSuite
from effaced_supabase import PostgrestColumn, PostgrestTable, SupabasePostgrestResolver

PRESENT = "00000000-0000-4000-8000-000000000001"
ABSENT = "00000000-0000-4000-8000-00000000000f"

BASE_URL = "https://project.supabase.co"
KEY = "service-role-test-key"

PROFILES = PostgrestTable(
    name="profiles",
    subject_column="user_id",
    columns=(
        PostgrestColumn(name="full_name", category=PiiCategory.IDENTITY),
        PostgrestColumn(name="email", category=PiiCategory.CONTACT),
    ),
)
ADDRESSES = PostgrestTable(
    name="addresses",
    subject_column="owner_id",
    columns=(PostgrestColumn(name="line1", category=PiiCategory.CONTACT),),
)
TABLES = (PROFILES, ADDRESSES)


def _seed() -> FakePostgrestTransport:
    return FakePostgrestTransport(
        tables={
            "profiles": [
                {"user_id": PRESENT, "full_name": "Grace Hopper", "email": "g@example.com"}
            ],
            "addresses": [{"owner_id": PRESENT, "line1": "1 Navy Yard"}],
        }
    )


class TestSupabasePostgrestResolverConformance(ResolverConformanceSuite):
    def make_resolver(self) -> SupabasePostgrestResolver:
        return SupabasePostgrestResolver(BASE_URL, KEY, TABLES, transport=_seed())

    def make_fully_populated_resolver(self) -> SupabasePostgrestResolver:
        # The seed row populates every declared column of every table.
        return SupabasePostgrestResolver(BASE_URL, KEY, TABLES, transport=_seed())

    def make_present_ref(self) -> SubjectRef:
        return SubjectRef(kind="supabase_postgrest", value=PRESENT)

    def make_absent_ref(self) -> SubjectRef:
        return SubjectRef(kind="supabase_postgrest", value=ABSENT)

    def make_nonretryable_resolver(self) -> SupabasePostgrestResolver:
        fake = FakePostgrestTransport(error_status=401)
        return SupabasePostgrestResolver(BASE_URL, KEY, TABLES, transport=fake)

    def make_transient_resolver(self) -> tuple[SupabasePostgrestResolver, type[Exception]]:
        fake = FakePostgrestTransport(error_status=429)
        return (
            SupabasePostgrestResolver(BASE_URL, KEY, TABLES, transport=fake),
            httpx.HTTPStatusError,
        )
