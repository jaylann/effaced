"""The :class:`S3Resolver` — user-generated objects under a key prefix."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from effaced.exceptions import ResolverError
from effaced.resolvers import ResolverErasure, ResolverExport
from effaced_s3.covered_surface import S3_COVERED_SURFACE
from effaced_s3.deletion import delete_in_batches
from effaced_s3.errors import (
    NONRETRYABLE_CODES,
    error_code,
    is_nonretryable,
)
from effaced_s3.export_collection import collect_object_records
from effaced_s3.listing import collect_version_identifiers
from effaced_s3.partial_erase_error import PartialEraseError
from effaced_s3.prefix_guard import checked_prefix

if TYPE_CHECKING:
    from effaced import CoveredSurface
    from effaced.annotations import SubjectRef
    from effaced_s3.object_client import S3ObjectClient


def _default_client(region_name: str | None) -> S3ObjectClient:
    """A boto3 S3 client with SDK retries off — the saga runner owns retry."""
    config = Config(retries={"max_attempts": 1, "mode": "standard"})
    return boto3.client("s3", region_name=region_name, config=config)


def _delete_versions(client: S3ObjectClient, bucket: str, prefix: str) -> tuple[int, list[str]]:
    """Delete every version under the prefix; collect per-key error codes.

    Batches keep going past per-key failures so each attempt makes
    monotonic progress — retries then converge on the survivors.
    """
    identifiers = collect_version_identifiers(client, bucket, prefix)
    codes = delete_in_batches(client, bucket, identifiers)
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

    @property
    def covered_surface(self) -> CoveredSurface:
        """The S3 object PII this resolver claims to reach (:class:`~effaced.AttestingResolver`).

        Returns:
            :data:`~effaced_s3.covered_surface.S3_COVERED_SURFACE`, built
            from the exporter's object-field tuple so it cannot drift.
        """
        return S3_COVERED_SURFACE

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
        prefix = checked_prefix(ref)
        try:
            records = await asyncio.to_thread(
                collect_object_records,
                self._client,
                self._bucket,
                prefix,
                source=self.name,
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
        prefix = checked_prefix(ref)
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
