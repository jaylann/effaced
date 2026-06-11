"""Pagination helpers over the S3 listing APIs.

Both helpers walk every page; callers never see truncation. They are
synchronous on purpose — the resolver drives them through
``asyncio.to_thread`` (ADR 0006).
"""

from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from types_boto3_s3.type_defs import ObjectIdentifierTypeDef, ObjectTypeDef

    from effaced_s3.object_client import S3ObjectClient

DELETE_BATCH_SIZE = 1000
"""S3's hard cap on entries per ``DeleteObjects`` request."""


def collect_version_identifiers(
    client: S3ObjectClient, bucket: str, prefix: str
) -> list[ObjectIdentifierTypeDef]:
    """Every (key, version) pair under the prefix — delete markers included.

    Args:
        client: The S3 client to list with.
        bucket: The bucket holding the subject's objects.
        prefix: The subject's key prefix.

    Returns:
        Identifiers for all object versions and delete markers, in
        listing order, ready for ``delete_objects`` batches.
    """
    identifiers: list[ObjectIdentifierTypeDef] = []
    key_marker = ""
    version_marker = ""
    while True:
        if key_marker:
            page = client.list_object_versions(
                Bucket=bucket,
                Prefix=prefix,
                KeyMarker=key_marker,
                VersionIdMarker=version_marker,
            )
        else:
            page = client.list_object_versions(Bucket=bucket, Prefix=prefix)
        for entry in chain(page.get("Versions", []), page.get("DeleteMarkers", [])):
            identifier: ObjectIdentifierTypeDef = {"Key": entry["Key"]}
            if "VersionId" in entry:
                identifier["VersionId"] = entry["VersionId"]
            identifiers.append(identifier)
        if not page.get("IsTruncated"):
            return identifiers
        key_marker = page.get("NextKeyMarker", "")
        version_marker = page.get("NextVersionIdMarker", "")


def iter_current_objects(
    client: S3ObjectClient, bucket: str, prefix: str
) -> Iterator[ObjectTypeDef]:
    """The current (non-deleted) objects under the prefix, page by page.

    Args:
        client: The S3 client to list with.
        bucket: The bucket holding the subject's objects.
        prefix: The subject's key prefix.

    Yields:
        One listing entry per current object.
    """
    token = ""
    while True:
        if token:
            page = client.list_objects_v2(Bucket=bucket, Prefix=prefix, ContinuationToken=token)
        else:
            page = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
        yield from page.get("Contents", [])
        if not page.get("IsTruncated"):
            return
        token = page.get("NextContinuationToken", "")
