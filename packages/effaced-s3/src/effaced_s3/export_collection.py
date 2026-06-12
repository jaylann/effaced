"""Collect a subject's objects under a prefix into :class:`~effaced.ExportRecord` rows.

Walks the current objects under a prefix and maps each — via GET (with
body) or HEAD (metadata only) — to export records labelled with the
caller's ``source``. The oversized-object cap fails the whole export
loudly; an object that vanishes between list and fetch is skipped, since
the system no longer holds it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from botocore.exceptions import ClientError

from effaced.exceptions import ResolverError
from effaced_s3.errors import is_absent_object
from effaced_s3.export_records import object_records
from effaced_s3.listing import iter_current_objects

if TYPE_CHECKING:
    from effaced.export import ExportRecord
    from effaced_s3.object_client import S3ObjectClient


def _fetch_records(
    client: S3ObjectClient, bucket: str, key: str, *, source: str, include_content: bool
) -> tuple[ExportRecord, ...]:
    """One object's records, via GET (with body) or HEAD (metadata only)."""
    if include_content:
        fetched = client.get_object(Bucket=bucket, Key=key)
        return object_records(
            key,
            source=source,
            size=fetched.get("ContentLength", 0),
            content_type=fetched.get("ContentType"),
            last_modified=fetched.get("LastModified"),
            metadata=fetched.get("Metadata", {}),
            content=fetched["Body"].read(),
        )
    head = client.head_object(Bucket=bucket, Key=key)
    return object_records(
        key,
        source=source,
        size=head.get("ContentLength", 0),
        content_type=head.get("ContentType"),
        last_modified=head.get("LastModified"),
        metadata=head.get("Metadata", {}),
        content=None,
    )


def collect_object_records(
    client: S3ObjectClient,
    bucket: str,
    prefix: str,
    *,
    source: str,
    include_content: bool,
    max_object_bytes: int | None,
) -> tuple[ExportRecord, ...]:
    """Map every current object under the prefix; the size cap fails loudly.

    Args:
        client: The object-store client to list and fetch with.
        bucket: The bucket holding the subject's objects.
        prefix: The subject's key prefix.
        source: The ``ExportRecord.source`` label every produced record
            carries — the resolver's name.
        include_content: Fetch each object's body (GET) or only its
            metadata (HEAD).
        max_object_bytes: Refuse (loudly) to export any object larger
            than this; ``None`` means no cap.

    Returns:
        The records for every current object under the prefix, in listing
        order. Empty when nothing lives under the prefix.

    Raises:
        ResolverError: An object under the prefix exceeds
            ``max_object_bytes`` — the export fails whole, never a
            silently thinned bundle.
    """
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
                _fetch_records(
                    client, bucket, entry["Key"], source=source, include_content=include_content
                )
            )
        except ClientError as error:
            # Vanished between list and fetch: the system no longer holds it.
            if not is_absent_object(error):
                raise
    return tuple(records)
