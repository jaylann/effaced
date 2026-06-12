"""A stateful in-process Supabase Storage backend satisfying ``S3ObjectClient``.

Models the Supabase Storage S3 gateway: an **unversioned** object store
(every key holds exactly one body) that supports ``ListObjectsV2``,
``GetObject``, ``HeadObject``, and ``DeleteObjects`` — and whose
``ListObjectVersions`` raises a ``NotImplemented`` ``ClientError``,
mirroring the gateway and proving the resolver never touches versioning.

It raises **real** ``botocore`` exceptions (``ClientError`` with genuine
error bodies, ``EndpointConnectionError``) so the resolver's error
taxonomy runs against the same types live boto3 produces. No call ever
leaves the process.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import ClientError, EndpointConnectionError

FIXED_LAST_MODIFIED = datetime(2026, 1, 1, tzinfo=UTC)

_STATUS_BY_CODE = {
    "AccessDenied": 403,
    "InvalidAccessKeyId": 403,
    "SignatureDoesNotMatch": 403,
    "NoSuchBucket": 404,
    "SlowDown": 503,
    "ServiceUnavailable": 503,
    "InternalError": 500,
    "NotImplemented": 501,
}


@dataclass
class FakeObject:
    """One stored object — the gateway keeps no version history."""

    body: bytes = b""
    content_type: str = "application/octet-stream"
    metadata: dict[str, str] = field(default_factory=dict)
    last_modified: datetime = FIXED_LAST_MODIFIED


class FakeSupabaseStorageClient:
    """Routes the gateway's S3 operations against a flat dict store."""

    def __init__(
        self,
        objects: dict[str, bytes] | None = None,
        *,
        page_size: int = 1000,
        error_code: str | None = None,
        error_status: int | None = None,
        connection_error: bool = False,
        delete_errors: dict[str, str] | None = None,
        content_types: dict[str, str] | None = None,
        metadata: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.page_size = page_size
        self.error_code = error_code
        self.error_status = error_status
        self.connection_error = connection_error
        self.delete_errors = dict(delete_errors or {})
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._store: dict[str, FakeObject] = {}
        for key, body in (objects or {}).items():
            self._store[key] = FakeObject(
                body=body,
                content_type=(content_types or {}).get(key, "application/octet-stream"),
                metadata=(metadata or {}).get(key, {}),
            )

    @property
    def stored_keys(self) -> set[str]:
        """Keys with a remaining object."""
        return set(self._store)

    # -- error injection ---------------------------------------------------

    def _client_error(self, operation: str, code: str, status: int | None = None) -> ClientError:
        return ClientError(
            {
                "Error": {"Code": code, "Message": f"fake supabase storage {code}"},
                "ResponseMetadata": {
                    "HTTPStatusCode": status or _STATUS_BY_CODE.get(code, 400),
                },
            },
            operation,
        )

    def _maybe_fail(self, operation: str, kwargs: dict[str, Any]) -> None:
        self.calls.append((operation, kwargs))
        if self.connection_error:
            raise EndpointConnectionError(endpoint_url="https://fake.storage.supabase.co")
        if self.error_code is not None:
            raise self._client_error(operation, self.error_code, self.error_status)

    # -- S3ObjectClient operations ------------------------------------------

    def list_object_versions(
        self,
        *,
        Bucket: str,
        Prefix: str,
        KeyMarker: str = "",
        VersionIdMarker: str = "",
    ) -> dict[str, Any]:
        """The gateway does not implement versioning — always a hard 501."""
        self.calls.append(
            ("ListObjectVersions", {"Bucket": Bucket, "Prefix": Prefix, "KeyMarker": KeyMarker})
        )
        raise self._client_error("ListObjectVersions", "NotImplemented", 501)

    def list_objects_v2(
        self, *, Bucket: str, Prefix: str, ContinuationToken: str = ""
    ) -> dict[str, Any]:
        """Page current objects under the prefix."""
        self._maybe_fail(
            "ListObjectsV2",
            {"Bucket": Bucket, "Prefix": Prefix, "ContinuationToken": ContinuationToken},
        )
        current = [(key, self._store[key]) for key in sorted(self._store) if key.startswith(Prefix)]
        if ContinuationToken:
            current = [(key, obj) for key, obj in current if key > ContinuationToken]
        page = current[: self.page_size]
        truncated = len(current) > self.page_size
        response: dict[str, Any] = {
            "Contents": [
                {"Key": key, "Size": len(obj.body), "LastModified": obj.last_modified}
                for key, obj in page
            ],
            "IsTruncated": truncated,
        }
        if truncated:
            response["NextContinuationToken"] = page[-1][0]
        return response

    def delete_objects(self, *, Bucket: str, Delete: dict[str, Any]) -> dict[str, Any]:
        """Remove the named keys; absent keys succeed silently."""
        self._maybe_fail("DeleteObjects", {"Bucket": Bucket, "Delete": Delete})
        errors: list[dict[str, str]] = []
        for target in Delete["Objects"]:
            key = target["Key"]
            code = self.delete_errors.pop(key, None)
            if code is not None:
                errors.append({"Key": key, "Code": code, "Message": "fake"})
                continue
            self._store.pop(key, None)
        return {"Errors": errors} if errors else {}

    def _current(self, operation: str, key: str, *, code: str) -> FakeObject:
        obj = self._store.get(key)
        if obj is None:
            raise self._client_error(operation, code, 404)
        return obj

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        """Fetch an object's body and metadata; 404 when absent."""
        self._maybe_fail("GetObject", {"Bucket": Bucket, "Key": Key})
        obj = self._current("GetObject", Key, code="NoSuchKey")
        return {
            "Body": io.BytesIO(obj.body),
            "ContentLength": len(obj.body),
            "ContentType": obj.content_type,
            "LastModified": obj.last_modified,
            "Metadata": dict(obj.metadata),
        }

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        """Fetch an object's metadata; bare-status 404 when absent."""
        self._maybe_fail("HeadObject", {"Bucket": Bucket, "Key": Key})
        obj = self._current("HeadObject", Key, code="404")
        return {
            "ContentLength": len(obj.body),
            "ContentType": obj.content_type,
            "LastModified": obj.last_modified,
            "Metadata": dict(obj.metadata),
        }
