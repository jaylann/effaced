"""A stateful in-process S3 backend satisfying ``S3ObjectClient``.

The fake models a versioned object store: every key holds a stack of
versions (newest last), delete markers included. It raises **real**
``botocore`` exceptions (``ClientError`` with genuine error bodies,
``EndpointConnectionError``) so the resolver's error taxonomy is
exercised against the same types live boto3 produces. No call ever
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
    "PermanentRedirect": 301,
    "SlowDown": 503,
    "ServiceUnavailable": 503,
    "InternalError": 500,
}


@dataclass
class FakeVersion:
    """One stored version of an object (or a delete marker)."""

    version_id: str
    body: bytes = b""
    delete_marker: bool = False
    content_type: str = "application/octet-stream"
    metadata: dict[str, str] = field(default_factory=dict)
    last_modified: datetime = FIXED_LAST_MODIFIED


class FakeS3Client:
    """Routes the five S3 operations the resolver uses against a dict store."""

    def __init__(
        self,
        objects: dict[str, bytes | list[bytes]] | None = None,
        *,
        versioned: bool = True,
        page_size: int = 1000,
        error_code: str | None = None,
        error_status: int | None = None,
        connection_error: bool = False,
        delete_errors: dict[str, str] | None = None,
        content_types: dict[str, str] | None = None,
        metadata: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.versioned = versioned
        self.page_size = page_size
        self.error_code = error_code
        self.error_status = error_status
        self.connection_error = connection_error
        self.delete_errors = dict(delete_errors or {})
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._counter = 0
        self._store: dict[str, list[FakeVersion]] = {}
        for key, bodies in (objects or {}).items():
            stack = bodies if isinstance(bodies, list) else [bodies]
            if not versioned:
                stack = stack[-1:]
            for body in stack:
                self._put(
                    key,
                    body,
                    content_type=(content_types or {}).get(key, "application/octet-stream"),
                    metadata=(metadata or {}).get(key, {}),
                )

    # -- seeding helpers ---------------------------------------------------

    def _next_version_id(self) -> str:
        if not self.versioned:
            return "null"
        self._counter += 1
        return f"v{self._counter}"

    def _put(self, key: str, body: bytes, **extra: Any) -> None:
        self._store.setdefault(key, []).append(
            FakeVersion(version_id=self._next_version_id(), body=body, **extra)
        )

    def add_delete_marker(self, key: str) -> None:
        """Append a delete marker on top of a key's version stack."""
        self._store.setdefault(key, []).append(
            FakeVersion(version_id=self._next_version_id(), delete_marker=True)
        )

    @property
    def stored_keys(self) -> set[str]:
        """Keys with at least one remaining version (markers included)."""
        return set(self._store)

    # -- error injection ---------------------------------------------------

    def _client_error(self, operation: str, code: str, status: int | None = None) -> ClientError:
        return ClientError(
            {
                "Error": {"Code": code, "Message": f"fake s3 {code}"},
                "ResponseMetadata": {
                    "HTTPStatusCode": status or _STATUS_BY_CODE.get(code, 400),
                },
            },
            operation,
        )

    def _maybe_fail(self, operation: str, kwargs: dict[str, Any]) -> None:
        self.calls.append((operation, kwargs))
        if self.connection_error:
            raise EndpointConnectionError(endpoint_url="https://s3.fake.invalid")
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
        """Page (key, version) pairs under the prefix, newest version first."""
        self._maybe_fail(
            "ListObjectVersions",
            {"Bucket": Bucket, "Prefix": Prefix, "KeyMarker": KeyMarker},
        )
        entries = [
            (key, version)
            for key in sorted(self._store)
            if key.startswith(Prefix)
            for version in reversed(self._store[key])
        ]
        start = 0
        if KeyMarker:
            marks = [
                index
                for index, (key, version) in enumerate(entries)
                if (key, version.version_id) == (KeyMarker, VersionIdMarker)
            ]
            start = marks[0] + 1 if marks else 0
        page = entries[start : start + self.page_size]
        truncated = start + self.page_size < len(entries)
        response: dict[str, Any] = {
            "Versions": [
                {"Key": key, "VersionId": version.version_id}
                for key, version in page
                if not version.delete_marker
            ],
            "DeleteMarkers": [
                {"Key": key, "VersionId": version.version_id}
                for key, version in page
                if version.delete_marker
            ],
            "IsTruncated": truncated,
        }
        if truncated and page:
            response["NextKeyMarker"] = page[-1][0]
            response["NextVersionIdMarker"] = page[-1][1].version_id
        return response

    def list_objects_v2(
        self, *, Bucket: str, Prefix: str, ContinuationToken: str = ""
    ) -> dict[str, Any]:
        """Page current (non-deleted) objects under the prefix."""
        self._maybe_fail(
            "ListObjectsV2",
            {"Bucket": Bucket, "Prefix": Prefix, "ContinuationToken": ContinuationToken},
        )
        current = [
            (key, self._store[key][-1])
            for key in sorted(self._store)
            if key.startswith(Prefix) and not self._store[key][-1].delete_marker
        ]
        if ContinuationToken:
            current = [(key, version) for key, version in current if key > ContinuationToken]
        page = current[: self.page_size]
        truncated = len(current) > self.page_size
        response: dict[str, Any] = {
            "Contents": [
                {"Key": key, "Size": len(version.body), "LastModified": version.last_modified}
                for key, version in page
            ],
            "IsTruncated": truncated,
        }
        if truncated:
            response["NextContinuationToken"] = page[-1][0]
        return response

    def delete_objects(self, *, Bucket: str, Delete: dict[str, Any]) -> dict[str, Any]:
        """Remove the named (key, version) pairs; absent pairs succeed silently."""
        self._maybe_fail("DeleteObjects", {"Bucket": Bucket, "Delete": Delete})
        errors: list[dict[str, str]] = []
        for target in Delete["Objects"]:
            key, version_id = target["Key"], target.get("VersionId", "")
            code = self.delete_errors.pop(key, None)
            if code is not None:
                errors.append(
                    {"Key": key, "VersionId": version_id, "Code": code, "Message": "fake"}
                )
                continue
            stack = self._store.get(key, [])
            self._store[key] = [v for v in stack if v.version_id != version_id]
            if key in self._store and not self._store[key]:
                del self._store[key]
        return {"Errors": errors} if errors else {}

    def _current(self, operation: str, key: str, *, code: str) -> FakeVersion:
        stack = self._store.get(key)
        if not stack or stack[-1].delete_marker:
            raise self._client_error(operation, code, 404)
        return stack[-1]

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        """Fetch the current version's body and metadata; 404 when absent."""
        self._maybe_fail("GetObject", {"Bucket": Bucket, "Key": Key})
        version = self._current("GetObject", Key, code="NoSuchKey")
        return {
            "Body": io.BytesIO(version.body),
            "ContentLength": len(version.body),
            "ContentType": version.content_type,
            "LastModified": version.last_modified,
            "Metadata": dict(version.metadata),
        }

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        """Fetch the current version's metadata; bare-status 404 when absent."""
        self._maybe_fail("HeadObject", {"Bucket": Bucket, "Key": Key})
        version = self._current("HeadObject", Key, code="404")
        return {
            "ContentLength": len(version.body),
            "ContentType": version.content_type,
            "LastModified": version.last_modified,
            "Metadata": dict(version.metadata),
        }
