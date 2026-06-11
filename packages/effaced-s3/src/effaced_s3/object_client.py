"""The :class:`S3ObjectClient` protocol — the five S3 calls the resolver makes.

A structural subset of boto3's S3 client: the real ``boto3.client("s3")``
satisfies it, and tests inject an in-process fake. The CamelCase keyword
names are the AWS wire API — renaming them would break structural
compatibility with boto3 clients.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from types_boto3_s3.type_defs import (
        DeleteObjectsOutputTypeDef,
        DeleteTypeDef,
        GetObjectOutputTypeDef,
        HeadObjectOutputTypeDef,
        ListObjectsV2OutputTypeDef,
        ListObjectVersionsOutputTypeDef,
    )


class S3ObjectClient(Protocol):
    """What the resolver requires of an S3 client (structural)."""

    def list_object_versions(
        self,
        *,
        Bucket: str,
        Prefix: str,
        KeyMarker: str = ...,
        VersionIdMarker: str = ...,
    ) -> ListObjectVersionsOutputTypeDef:
        """Page every object version and delete marker under a prefix."""
        ...

    def list_objects_v2(
        self,
        *,
        Bucket: str,
        Prefix: str,
        ContinuationToken: str = ...,
    ) -> ListObjectsV2OutputTypeDef:
        """Page the current objects under a prefix."""
        ...

    def delete_objects(
        self,
        *,
        Bucket: str,
        Delete: DeleteTypeDef,
    ) -> DeleteObjectsOutputTypeDef:
        """Batch-delete up to 1000 (key, version) pairs."""
        ...

    def get_object(self, *, Bucket: str, Key: str) -> GetObjectOutputTypeDef:
        """Fetch one object's body and metadata."""
        ...

    def head_object(self, *, Bucket: str, Key: str) -> HeadObjectOutputTypeDef:
        """Fetch one object's metadata without the body."""
        ...
