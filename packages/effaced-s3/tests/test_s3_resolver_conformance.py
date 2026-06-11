"""S3Resolver passes the shared resolver conformance suite.

Acceptance for issue #45: export shape, erase idempotency (nothing under
the prefix is success), and the error taxonomy, all proven through the
suite shipped in effaced core — driven by the fake S3 backend, no live
calls.
"""

from __future__ import annotations

from botocore.exceptions import ClientError
from fake_s3_client import FakeS3Client

from effaced import SubjectRef
from effaced.testing import ResolverConformanceSuite
from effaced_s3 import S3Resolver

PRESENT_PREFIX = "users/42/"
ABSENT_PREFIX = "users/none/"
BYSTANDER_PREFIX = "users/7/"

OBJECTS: dict[str, bytes | list[bytes]] = {
    f"{PRESENT_PREFIX}avatar.png": b"\x89PNG-fake-bytes",
    f"{PRESENT_PREFIX}uploads/cv.pdf": [b"draft", b"final"],
    f"{BYSTANDER_PREFIX}avatar.png": b"someone else",
}


class TestS3ResolverConformance(ResolverConformanceSuite):
    def make_resolver(self) -> S3Resolver:
        fake = FakeS3Client(objects=dict(OBJECTS))
        return S3Resolver(bucket="user-content", client=fake)

    def make_present_ref(self) -> SubjectRef:
        return SubjectRef(kind="s3", value=PRESENT_PREFIX)

    def make_absent_ref(self) -> SubjectRef:
        return SubjectRef(kind="s3", value=ABSENT_PREFIX)

    def make_nonretryable_resolver(self) -> S3Resolver:
        fake = FakeS3Client(objects=dict(OBJECTS), error_code="AccessDenied")
        return S3Resolver(bucket="user-content", client=fake)

    def make_transient_resolver(self) -> tuple[S3Resolver, type[Exception]]:
        fake = FakeS3Client(objects=dict(OBJECTS), error_code="SlowDown")
        return (S3Resolver(bucket="user-content", client=fake), ClientError)
