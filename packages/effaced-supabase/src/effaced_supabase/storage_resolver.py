"""The :class:`SupabaseStorageResolver` — a subject's objects in Supabase Storage.

Rides :mod:`effaced_s3`'s S3-compatible machinery (ADR 0016): Supabase
Storage exposes an S3 gateway, so the prefix guard, export collector,
batched delete, and error taxonomy are shared rather than forked. The
machinery is an optional extra — install with
``pip install "effaced-supabase[storage]"``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError

    from effaced_s3 import (
        NONRETRYABLE_CODES,
        S3ObjectClient,
        checked_prefix,
        collect_object_records,
        delete_in_batches,
        error_code,
        is_nonretryable,
        iter_current_objects,
    )
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "SupabaseStorageResolver needs the 'storage' extra "
        '(effaced-s3 + boto3). Install with: pip install "effaced-supabase[storage]"'
    ) from exc

from effaced.exceptions import ConfigurationError, ResolverError
from effaced.resolvers import ResolverErasure, ResolverExport
from effaced_supabase.partial_storage_erase_error import PartialStorageEraseError
from effaced_supabase.storage_covered_surface import STORAGE_COVERED_SURFACE

if TYPE_CHECKING:
    from types_boto3_s3.type_defs import ObjectIdentifierTypeDef

    from effaced import CoveredSurface
    from effaced.annotations import SubjectRef


def _default_client(
    *,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    region: str,
) -> S3ObjectClient:
    """A boto3 S3 client wired to the Supabase Storage gateway.

    SDK retries are off — the saga runner owns retry and backoff (ADR
    0010). Path-style addressing is mandatory: the gateway does not serve
    virtual-host style ``<bucket>.endpoint`` URLs.
    """
    config = Config(
        retries={"max_attempts": 1, "mode": "standard"},
        s3={"addressing_style": "path"},
    )
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name=region,
        config=config,
    )


class SupabaseStorageResolver:
    """Exports and erases a subject's objects held in a Supabase Storage bucket.

    Expects refs of kind ``"supabase_storage"`` (refs are routed to the
    resolver whose name equals their kind — ADR 0008) whose value is the
    subject's key prefix, e.g. ``"users/42/"``; the bucket is fixed at
    construction. The prefix must be non-blank and end with ``"/"`` —
    anything else raises :class:`~effaced.exceptions.ResolverError` before
    any gateway call, because an unterminated prefix also matches sibling
    subjects (``users/4`` matches ``users/42/...``) and a blank one is the
    whole bucket.

    **No versioning, so current-object deletion IS complete erasure.**
    The Supabase Storage S3 gateway does not implement
    ``ListObjectVersions`` and the store has no object versions or delete
    markers. Erasure therefore lists the current objects under the prefix
    and deletes them — there is nothing else to sweep. The resolver never
    calls ``list_object_versions``. (This is the one behavioural
    difference from :class:`~effaced_s3.S3Resolver`, whose buckets may be
    versioned.) Exports cover the current objects and, by default,
    include each object's content base64-encoded — for user-generated
    objects the bytes usually *are* the personal data;
    ``include_content=False`` is appropriate only when the controller
    provides the files through another complete channel.

    Security posture: authentication is a dashboard-issued S3 access key
    (access-key id + secret) using SigV4. Treat it as a **root
    credential** — it is server-side only and must never reach a client.

    Idempotency: a prefix the store holds nothing under yields
    ``already_absent=True`` — success, never an error. A partially failed
    batch delete keeps deleting the rest, then raises
    :class:`~effaced_supabase.PartialStorageEraseError` so the saga
    retries; re-deletes are no-ops, so retries converge.

    Error taxonomy (shared with :mod:`effaced_s3.errors`): credential,
    permission, and missing-bucket failures raise
    :class:`~effaced.exceptions.ResolverError`; throttling, connection
    faults, gateway-side errors, and unknown codes propagate so the saga
    runner retries. SDK-internal retries are disabled; the sync boto3
    client is driven via ``asyncio.to_thread`` and is not loop-bound, so
    holding it on the instance is safe (ADR 0006, mirroring
    :class:`~effaced_s3.S3Resolver`).

    Messages and details carry counts and error codes only — never keys,
    prefixes, or bucket names, which are user content.
    """

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        region: str | None = None,
        client: S3ObjectClient | None = None,
        include_content: bool = True,
        max_object_bytes: int | None = None,
    ) -> None:
        """Wire the resolver to one Supabase Storage bucket.

        Args:
            bucket: The Storage bucket holding subject-owned objects.
            endpoint_url: The project's S3 gateway origin, e.g.
                ``https://<project_ref>.storage.supabase.co/storage/v1/s3``
                (local dev: ``http://127.0.0.1:54321/storage/v1/s3``).
            access_key_id: The dashboard-issued S3 access-key id. A root
                credential — server-side only.
            secret_access_key: The S3 secret access key paired with it.
            region: The gateway region — pass the project's (``local`` for
                local dev). SigV4 needs one; leaving it to boto3's ambient
                resolution would sign for whatever region the host happens
                to be configured with.
            client: Optional client override — custom sessions or the test
                fake. When given, the four connection params are ignored.
            include_content: Export each object's body (base64) — the
                default, because the bytes usually are the personal data.
                Disable only when the files reach the subject through
                another complete, retainable channel.
            max_object_bytes: Refuse (loudly) to export any object larger
                than this; ``None`` means no cap. An oversized object
                fails the whole export with
                :class:`~effaced.exceptions.ResolverError` — never a
                silently thinned bundle.

        Raises:
            ConfigurationError: No ``client`` was given and one of
                ``endpoint_url``, ``access_key_id``, ``secret_access_key``,
                ``region`` is missing — the resolver refuses to degrade
                into a misdirected or unauthenticated client.
        """
        self._bucket = bucket
        self._include_content = include_content
        self._max_object_bytes = max_object_bytes
        if client is not None:
            self._client = client
            return
        if (
            endpoint_url is None
            or access_key_id is None
            or secret_access_key is None
            or region is None
        ):
            raise ConfigurationError(
                "SupabaseStorageResolver needs endpoint_url, access_key_id, "
                "secret_access_key, and region (or an explicit client) — refusing "
                "to build a misdirected or unauthenticated client"
            )
        self._client = _default_client(
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            region=region,
        )

    @property
    def name(self) -> str:
        """Stable resolver name recorded in manifests and audits."""
        return "supabase_storage"

    @property
    def covered_surface(self) -> CoveredSurface:
        """The Storage PII this resolver claims to reach (:class:`~effaced.AttestingResolver`).

        Returns:
            :data:`~effaced_supabase.storage_covered_surface.STORAGE_COVERED_SURFACE`,
            built from effaced-s3's object-field tuple so it cannot drift.
        """
        return STORAGE_COVERED_SURFACE

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        """Collect the objects held under the subject's prefix (Art. 15).

        Args:
            ref: ``kind="supabase_storage"``, ``value=<key prefix>``.

        Returns:
            Per object: key, size, content type, last-modified, user
            metadata, and (unless disabled) the base64-encoded body.
            Empty when nothing lives under the prefix.

        Raises:
            ResolverError: The credentials are invalid or lack a
                permission, the bucket does not exist, the prefix is blank
                or not ``"/"``-terminated, or an object exceeds
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
                    "supabase storage rejected the export request "
                    f"(code={error_code(error) or 'none'})"
                ) from error
            raise
        return ResolverExport(resolver=self.name, records=records)

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        """Delete every current object under the subject's prefix (Art. 17).

        Supabase Storage has no versioning, so the current objects under
        the prefix are the whole of the subject's data — there is no
        version history to sweep. ``list_object_versions`` is never
        called.

        Args:
            ref: ``kind="supabase_storage"``, ``value=<key prefix>``.

        Returns:
            The outcome; ``already_absent=True`` if the store already held
            nothing under the prefix.

        Raises:
            ResolverError: The credentials are invalid or lack a
                permission, the bucket does not exist, the prefix is blank
                or not ``"/"``-terminated, or the gateway refused every
                failed deletion for non-retryable reasons — retrying
                cannot succeed.
            PartialStorageEraseError: Some objects failed transiently this
                attempt; propagates so the saga retries to convergence.
        """
        prefix = checked_prefix(ref)
        try:
            total, codes = await asyncio.to_thread(self._delete_current, prefix)
        except ClientError as error:
            if is_nonretryable(error):
                raise ResolverError(
                    "supabase storage rejected the erasure request "
                    f"(code={error_code(error) or 'none'})"
                ) from error
            raise
        if total == 0:
            return ResolverErasure(
                resolver=self.name,
                already_absent=True,
                detail="nothing held under the prefix",
            )
        if codes:
            summary = f"{len(codes)} of {total} objects (codes: {sorted(set(codes))})"
            if all(code in NONRETRYABLE_CODES for code in codes):
                raise ResolverError(f"supabase storage refused deletion for {summary}")
            raise PartialStorageEraseError(f"supabase storage deletion incomplete for {summary}")
        return ResolverErasure(resolver=self.name, detail=f"deleted {total} objects")

    def _delete_current(self, prefix: str) -> tuple[int, list[str]]:
        """List the current objects under the prefix and batch-delete them."""
        identifiers: list[ObjectIdentifierTypeDef] = [
            {"Key": entry["Key"]}
            for entry in iter_current_objects(self._client, self._bucket, prefix)
        ]
        codes = delete_in_batches(self._client, self._bucket, identifiers)
        return len(identifiers), codes
