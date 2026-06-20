"""The now-public object-store helpers, exercised directly.

These parts are promoted public API (so S3-compatible stores can ride
them): the export collector's ``source`` labelling, and the batched
delete's bound and error accumulation.
"""

from __future__ import annotations

import pytest
from fake_s3_client import FakeS3Client

from effaced.exceptions import ResolverError
from effaced_s3 import (
    collect_object_records,
    collect_version_identifiers,
    delete_in_batches,
    iter_object_records,
)

PREFIX = "users/42/"


def test_collect_object_records_labels_records_with_the_given_source() -> None:
    fake = FakeS3Client(objects={f"{PREFIX}a.txt": b"hello"})
    records = collect_object_records(
        fake,
        "bucket",
        PREFIX,
        source="supabase_storage",
        include_content=True,
        max_object_bytes=None,
    )
    assert records
    assert {record.source for record in records} == {"supabase_storage"}


def test_collect_object_records_head_path_skips_get() -> None:
    fake = FakeS3Client(objects={f"{PREFIX}a.txt": b"hello"})
    records = collect_object_records(
        fake, "bucket", PREFIX, source="s3", include_content=False, max_object_bytes=None
    )
    assert records
    assert "GetObject" not in {operation for operation, _ in fake.calls}


def test_streaming_yields_records_identical_to_the_materialized_collector() -> None:
    """Acceptance gate: iter_object_records == collect_object_records, same order."""
    objects = {f"{PREFIX}f{index:03}.txt": f"body-{index}".encode() for index in range(25)}
    materialized = collect_object_records(
        FakeS3Client(objects=dict(objects)),
        "bucket",
        PREFIX,
        source="s3",
        include_content=True,
        max_object_bytes=None,
    )
    streamed = tuple(
        iter_object_records(
            FakeS3Client(objects=dict(objects)),
            "bucket",
            PREFIX,
            source="s3",
            include_content=True,
            max_object_bytes=None,
        )
    )
    assert streamed == materialized


def test_streaming_fetches_one_object_body_at_a_time() -> None:
    """Bounded memory: pulling the first record GETs only the first object.

    The materializing collector reads every body before returning; the
    streaming path must fetch lazily, so peak resident bodies stay at one.
    """
    objects = {f"{PREFIX}f{index:03}.txt": f"body-{index}".encode() for index in range(10)}
    fake = FakeS3Client(objects=dict(objects))
    stream = iter_object_records(
        fake, "bucket", PREFIX, source="s3", include_content=True, max_object_bytes=None
    )
    next(stream)  # pull the first object's first record
    gets = [operation for operation, _ in fake.calls if operation == "GetObject"]
    assert len(gets) == 1  # only the first body has been read so far
    remaining = list(stream)
    assert remaining  # the rest still stream out
    all_gets = [operation for operation, _ in fake.calls if operation == "GetObject"]
    assert len(all_gets) == len(objects)


def test_streaming_oversized_object_fails_loudly() -> None:
    """The size cap fails the stream before the body is ever read."""
    fake = FakeS3Client(objects={f"{PREFIX}big.bin": b"x" * 64})
    stream = iter_object_records(
        fake, "bucket", PREFIX, source="s3", include_content=True, max_object_bytes=8
    )
    with pytest.raises(ResolverError, match="max_object_bytes"):
        next(stream)
    assert "GetObject" not in {operation for operation, _ in fake.calls}


def test_delete_in_batches_stays_within_the_thousand_cap() -> None:
    keys = {f"{PREFIX}f{index:04}": b"x" for index in range(1500)}
    fake = FakeS3Client(objects=dict(keys))
    identifiers = collect_version_identifiers(fake, "bucket", PREFIX)
    codes = delete_in_batches(fake, "bucket", identifiers)
    assert codes == []
    batches = [
        len(kwargs["Delete"]["Objects"])
        for operation, kwargs in fake.calls
        if operation == "DeleteObjects"
    ]
    assert len(batches) >= 2
    assert all(size <= 1000 for size in batches)
    assert sum(batches) == 1500
    assert fake.stored_keys == set()


def test_delete_in_batches_accumulates_codes_and_keeps_deleting() -> None:
    keys = {f"{PREFIX}keep": b"x", f"{PREFIX}stuck": b"y"}
    fake = FakeS3Client(objects=dict(keys), delete_errors={f"{PREFIX}stuck": "InternalError"})
    identifiers = collect_version_identifiers(fake, "bucket", PREFIX)
    codes = delete_in_batches(fake, "bucket", identifiers)
    assert codes == ["InternalError"]
    # the non-failing key was still deleted despite the sibling's failure
    assert fake.stored_keys == {f"{PREFIX}stuck"}


def test_delete_in_batches_empty_input_makes_no_calls() -> None:
    fake = FakeS3Client(objects={})
    codes = delete_in_batches(fake, "bucket", [])
    assert codes == []
    assert fake.calls == []
