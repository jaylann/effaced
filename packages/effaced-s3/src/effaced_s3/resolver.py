"""The :class:`S3Resolver` — user-generated objects under a key prefix."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from effaced.exceptions import ResolverError
from effaced.resolvers import ResolverErasure, ResolverExport
from effaced_s3.errors import (
    NONRETRYABLE_CODES,
    error_code,
    is_absent_object,
    is_nonretryable,
)
from effaced_s3.export_records import object_records
from effaced_s3.listing import (
    DELETE_BATCH_SIZE,
    collect_version_identifiers,
    iter_current_objects,
)
from effaced_s3.partial_erase_error import PartialEraseError

if TYPE_CHECKING:
    from effaced.annotations import SubjectRef
    from effaced.export import ExportRecord
    from effaced_s3.object_client import S3ObjectClient


def _default_client(region_name: str | None) -> S3ObjectClient:
    """A boto3 S3 client with SDK retries off — the saga runner owns retry."""
    config = Config(retries={"max_attempts": 1, "mode": "standard"})
    return boto3.client("s3", region_name=region_name, config=config)


def _checked_prefix(ref: SubjectRef) -> str:
    """The ref's key prefix, validated before any S3 call.

    S3 prefixes are literal substring matches, so a prefix that is not
    delimiter-terminated also matches sibling subjects (``users/4``
    matches ``users/42/avatar.png``) — that is cross-subject bleed, the
    one thing a resolver must never do. Both guards run before any call.
    """
    if not ref.value.strip():
        raise ResolverError("subject ref prefix is blank — refusing to touch the whole bucket")
    if not ref.value.endswith("/"):
        raise ResolverError(
            "subject ref prefix must end with '/' — an unterminated prefix also "
            "matches sibling subjects ('users/4' matches 'users/42/...')"
        )
    return ref.value


def _fetch_records(
    client: S3ObjectClient, bucket: str, key: str, *, include_content: bool
) -> tuple[ExportRecord, ...]:
    """One object's records, via GET (with body) or HEAD (metadata only)."""
    if include_content:
        fetched = client.get_object(Bucket=bucket, Key=key)
        return object_records(
            key,
            size=fetched.get("ContentLength", 0),
            content_type=fetched.get("ContentType"),
            last_modified=fetched.get("LastModified"),
            metadata=fetched.get("Metadata", {}),
            content=fetched["Body"].read(),
        )
    head = client.head_object(Bucket=bucket, Key=key)
    return object_records(
        key,
        size=head.get("ContentLength", 0),
        content_type=head.get("ContentType"),
        last_modified=head.get("LastModified"),
        metadata=head.get("Metadata", {}),
        content=None,
    )


def _collect_records(
    client: S3ObjectClient,
    bucket: str,
    prefix: str,
    *,
    include_content: bool,
    max_object_bytes: int | None,
) -> tuple[ExportRecord, ...]:
    """Map every current object under the prefix; the size cap fails loudly."""
    records: list[ExportRecord] = []
    for entry in iter_current_objects(client, bucket, prefix):
        size = entry.get("Size", 0)
        if max_object_bytes is not None and size > max_object_bytes:
            raise ResolverError(
                "an object under the prefix exceeds max_object_bytes "
                f"(size={size}, cap={max_object_bytes})"
            )
        try:
            records.extend(
                _fetch_records(client, bucket, entry["Key"], include_content=include_content)
            )
        except ClientError as error:
            # Vanished between list and fetch: the system no longer holds it.
            if not is_absent_object(error):
                raise
    return tuple(records)


def _delete_versions(client: S3ObjectClient, bucket: str, prefix: str) -> tuple[int, list[str]]:
    """Delete every version under the prefix; collect per-key error codes.

    Batches keep going past per-key failures so each attempt makes
    monotonic progress — retries then converge on the survivors.
    """
    identifiers = collect_version_identifiers(client, bucket, prefix)
    codes: list[str] = []
    for start in range(0, len(identifiers), DELETE_BATCH_SIZE):
        batch = identifiers[start : start + DELETE_BATCH_SIZE]
        response = client.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})
        codes.extend(item.get("Code", "") for item in response.get("Errors", []))
    return len(identifiers), codes


class S3Resolver:
    """Exports and erases a subject's objects held under an S3 key prefix.

    Expects refs of kind ``"s3"`` (refs are routed to the resolver whose
    name equals their kind — ADR 0008) whose value is the subject's key
    prefix, e.g. ``"users/42/"``; the bucket is fixed at construction.
    The prefix must be non-blank and end with ``"/"`` — anything else
    raises :class:`~effaced.exceptions.ResolverError` before any S3 call,
    because an unterminated prefix also matches sibling subjects
    (``users/4`` matches ``users/42/...``) and a blank one is the whole
    bucket.

    Erasure deletes **every object version and delete marker** under the
    prefix: a plain delete on a versioned bucket only hides data behind a
    delete marker, which is not erasure. Unversioned buckets take the
    same path (S3 reports their versions as ``"null"``). Exports cover
    current versions and, by default, include each object's content
    base64-encoded — for user-generated objects the bytes usually *are*
    the personal data. ``include_content=False`` is appropriate only when
    the controller provides the files through another complete channel.

    Idempotency: a prefix S3 holds nothing under yields
    ``already_absent=True`` — success, never an error. A partially failed
    batch delete keeps deleting the rest, then raises
    :class:`~effaced_s3.PartialEraseError` so the saga retries; re-deletes
    are no-ops, so retries converge.

    Error taxonomy (see :mod:`effaced_s3.errors`): credential,
    permission, missing-bucket, and wrong-endpoint failures raise
    :class:`~effaced.exceptions.ResolverError`; throttling, connection
    faults, S3-side errors, and unknown codes propagate so the saga
    runner retries. SDK-internal retries are disabled — the saga runner
    owns retry and backoff (ADR 0010).
    """

    def __init__(
        self,
        bucket: str,
        *,
        client: S3ObjectClient | None = None,
        region_name: str | None = None,
        include_content: bool = True,
        max_object_bytes: int | None = None,
    ) -> None:
        """Wire the resolver to one bucket.

        Args:
            bucket: The bucket holding subject-owned objects.
            client: Optional client override — custom endpoints, scoped
                sessions, or the test fake. Defaults to ``boto3.client("s3")``
                with credentials from the standard AWS chain.
            region_name: Region for the default client; ignored when
                ``client`` is given.
            include_content: Export each object's body (base64) — the
                default, because the bytes usually are the personal data.
                Disable only when the files reach the subject through
                another complete, retainable channel.
            max_object_bytes: Refuse (loudly) to export any object larger
                than this; ``None`` means no cap. An oversized object
                fails the whole export with
                :class:`~effaced.exceptions.ResolverError` — never a
                silently thinned bundle.
        """
        self._bucket = bucket
        self._include_content = include_content
        self._max_object_bytes = max_object_bytes
        self._client = client if client is not None else _default_client(region_name)

    @property
    def name(self) -> str:
        """Stable resolver name recorded in manifests and audits."""
        return "s3"

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        """Collect the objects held under the subject's prefix (Art. 15).

        Args:
            ref: ``kind="s3"``, ``value=<key prefix>``.

        Returns:
            Per object: key, size, content type, last-modified, user
            metadata, and (unless disabled) the base64-encoded body.
            Empty when nothing lives under the prefix.

        Raises:
            ResolverError: The credentials are invalid or lack a
                permission, the bucket does not exist, the prefix is
                blank or not ``"/"``-terminated, or an object exceeds
                ``max_object_bytes`` — retrying cannot succeed.
        """
        prefix = _checked_prefix(ref)
        try:
            records = await asyncio.to_thread(
                _collect_records,
                self._client,
                self._bucket,
                prefix,
                include_content=self._include_content,
                max_object_bytes=self._max_object_bytes,
            )
        except ClientError as error:
            if is_nonretryable(error):
                raise ResolverError(
                    f"s3 rejected the export request (code={error_code(error) or 'none'})"
                ) from error
            raise
        return ResolverExport(resolver=self.name, records=records)

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        """Delete every object version under the subject's prefix (Art. 17).

        Args:
            ref: ``kind="s3"``, ``value=<key prefix>``.

        Returns:
            The outcome; ``already_absent=True`` if S3 already held
            nothing under the prefix.

        Raises:
            ResolverError: The credentials are invalid or lack a
                permission, the bucket does not exist, the prefix is
                blank or not ``"/"``-terminated, or S3 refused every
                failed deletion for non-retryable reasons — retrying
                cannot succeed.
            PartialEraseError: Some versions failed transiently this
                attempt; propagates so the saga retries to convergence.
        """
        prefix = _checked_prefix(ref)
        try:
            total, codes = await asyncio.to_thread(
                _delete_versions, self._client, self._bucket, prefix
            )
        except ClientError as error:
            if is_nonretryable(error):
                raise ResolverError(
                    f"s3 rejected the erasure request (code={error_code(error) or 'none'})"
                ) from error
            raise
        if total == 0:
            return ResolverErasure(
                resolver=self.name,
                already_absent=True,
                detail="nothing held under the prefix",
            )
        if codes:
            summary = f"{len(codes)} of {total} object versions (codes: {sorted(set(codes))})"
            if all(code in NONRETRYABLE_CODES for code in codes):
                raise ResolverError(f"s3 refused deletion for {summary}")
            raise PartialEraseError(f"s3 deletion incomplete for {summary}")
        return ResolverErasure(resolver=self.name, detail=f"deleted {total} object versions")
