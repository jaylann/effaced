"""Property proofs for S3Resolver over arbitrary object sets.

Three guarantees the resolver must hold for *any* subject data shape:
erasure converges and never bleeds across the prefix boundary, exports
are invariant under listing page size, and partial delete failures are
retried to convergence.
"""

from __future__ import annotations

import asyncio

import pytest
from fake_s3_client import FakeS3Client
from hypothesis import given, settings
from hypothesis import strategies as st

from effaced import SubjectRef
from effaced_s3 import PartialEraseError, S3Resolver

pytestmark = pytest.mark.property

# Each example runs several full export/erase cycles; under coverage tracing
# that can trip the dev profile's 200ms per-example deadline (CI already runs
# deadline=None for the same reason).
no_deadline = settings(deadline=None)

# Deliberate sibling stems: the subject's stem is a literal string-prefix of
# the bystander's, so any unterminated-prefix matching bleeds — the property
# is structurally sensitive to the exact failure S3's substring Prefix invites.
SUBJECT_PREFIX = "users/4/"
BYSTANDER_PREFIX = "users/42/"

_key_suffixes = st.sets(
    st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789-._/",
        min_size=1,
        max_size=20,
    ),
    min_size=1,
    max_size=8,
)
_bodies = st.binary(max_size=64)


@st.composite
def _object_sets(draw: st.DrawFn, prefix: str) -> dict[str, list[bytes]]:
    suffixes = draw(_key_suffixes)
    return {
        f"{prefix}{suffix}": draw(st.lists(_bodies, min_size=1, max_size=3)) for suffix in suffixes
    }


def _export(resolver: S3Resolver, prefix: str):
    return asyncio.run(resolver.export_subject(SubjectRef(kind="s3", value=prefix)))


def _erase(resolver: S3Resolver, prefix: str):
    return asyncio.run(resolver.erase_subject(SubjectRef(kind="s3", value=prefix)))


@no_deadline
@given(
    subject=_object_sets(SUBJECT_PREFIX),
    bystander=_object_sets(BYSTANDER_PREFIX),
)
def test_erase_converges_and_never_bleeds(
    subject: dict[str, list[bytes]], bystander: dict[str, list[bytes]]
) -> None:
    fake = FakeS3Client(objects={**subject, **bystander})
    resolver = S3Resolver(bucket="b", client=fake)
    before = _export(resolver, BYSTANDER_PREFIX).records

    erasure = _erase(resolver, SUBJECT_PREFIX)

    assert erasure.already_absent is False
    assert _export(resolver, SUBJECT_PREFIX).records == ()
    assert _erase(resolver, SUBJECT_PREFIX).already_absent is True
    assert fake.stored_keys == set(bystander)
    assert _export(resolver, BYSTANDER_PREFIX).records == before


@no_deadline
@given(subject=_object_sets(SUBJECT_PREFIX), page_size=st.sampled_from([1, 3, 1000]))
def test_export_is_invariant_under_page_size(
    subject: dict[str, list[bytes]], page_size: int
) -> None:
    def records_at(size: int) -> list[tuple[str, object]]:
        fake = FakeS3Client(objects=dict(subject), page_size=size)
        export = _export(S3Resolver(bucket="b", client=fake), SUBJECT_PREFIX)
        return sorted((record.field, record.value) for record in export.records)

    assert records_at(page_size) == records_at(1000)


@no_deadline
@given(subject=_object_sets(SUBJECT_PREFIX), data=st.data())
def test_partial_failure_retries_to_convergence(
    subject: dict[str, list[bytes]], data: st.DataObject
) -> None:
    failing = data.draw(st.sets(st.sampled_from(sorted(subject)), min_size=1), label="failing keys")
    fake = FakeS3Client(
        objects=dict(subject),
        delete_errors=dict.fromkeys(failing, "InternalError"),
    )
    resolver = S3Resolver(bucket="b", client=fake)

    with pytest.raises(PartialEraseError):
        _erase(resolver, SUBJECT_PREFIX)

    erasure = _erase(resolver, SUBJECT_PREFIX)
    assert erasure.already_absent is False
    assert fake.stored_keys == set()
    assert _export(resolver, SUBJECT_PREFIX).records == ()
