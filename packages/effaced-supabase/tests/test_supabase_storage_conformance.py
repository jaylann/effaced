"""SupabaseStorageResolver passes the shared resolver conformance suite.

Export shape, erase idempotency (nothing under the prefix is success),
and the error taxonomy — all proven through the suite shipped in effaced
core, driven by the fake Supabase Storage gateway, no live calls.
"""

from __future__ import annotations

from botocore.exceptions import EndpointConnectionError
from fake_supabase_storage_client import FakeSupabaseStorageClient

from effaced import SubjectRef
from effaced.testing import ResolverConformanceSuite
from effaced_supabase.storage_resolver import SupabaseStorageResolver

PRESENT_PREFIX = "users/42/"
ABSENT_PREFIX = "users/none/"
BYSTANDER_PREFIX = "users/7/"

OBJECTS: dict[str, bytes] = {
    f"{PRESENT_PREFIX}avatar.png": b"\x89PNG-fake-bytes",
    f"{PRESENT_PREFIX}uploads/cv.pdf": b"final",
    f"{BYSTANDER_PREFIX}avatar.png": b"someone else",
}


class TestSupabaseStorageConformance(ResolverConformanceSuite):
    def make_resolver(self) -> SupabaseStorageResolver:
        fake = FakeSupabaseStorageClient(objects=dict(OBJECTS))
        return SupabaseStorageResolver(bucket="user-content", client=fake)

    def make_present_ref(self) -> SubjectRef:
        return SubjectRef(kind="supabase_storage", value=PRESENT_PREFIX)

    def make_absent_ref(self) -> SubjectRef:
        return SubjectRef(kind="supabase_storage", value=ABSENT_PREFIX)

    def make_nonretryable_resolver(self) -> SupabaseStorageResolver:
        fake = FakeSupabaseStorageClient(objects=dict(OBJECTS), error_code="InvalidAccessKeyId")
        return SupabaseStorageResolver(bucket="user-content", client=fake)

    def make_transient_resolver(self) -> tuple[SupabaseStorageResolver, type[Exception]]:
        fake = FakeSupabaseStorageClient(objects=dict(OBJECTS), connection_error=True)

        return (
            SupabaseStorageResolver(bucket="user-content", client=fake),
            EndpointConnectionError,
        )
