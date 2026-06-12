"""Mapping of one S3 object to :class:`~effaced.ExportRecord` rows.

The exported field set is behaviour under widened SemVer: adding fields
is MINOR, removing or recategorising any is MAJOR. Object content is a
faithful copy of the body, base64-encoded so the bundle stays
JSON-serializable; user metadata (``x-amz-meta-*``) is exported entry by
entry because applications routinely stash subject-related context
there.

``legal_basis``, ``purpose`` and ``retention_reason`` stay ``None`` on
every record: a resolver cannot know why the application holds the data;
that metadata belongs to the manifest-declared local data map.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

from effaced import ExportRecord, PiiCategory

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime


def object_records(
    key: str,
    *,
    source: str,
    size: int,
    content_type: str | None,
    last_modified: datetime | None,
    metadata: Mapping[str, str],
    content: bytes | None,
) -> tuple[ExportRecord, ...]:
    """Map one object's listing entry, metadata, and optional body.

    The object key is user-chosen (filenames, paths) and the body is the
    user's own upload, so both are
    :attr:`~effaced.PiiCategory.COMMUNICATION`; size, content type, and
    timestamp are :attr:`~effaced.PiiCategory.TECHNICAL`.

    Args:
        key: The object's full key.
        source: The ``ExportRecord.source`` label every produced record
            carries — the resolver's name (``"s3"``,
            ``"supabase_storage"``), so the bundle records which system
            held the object.
        size: Body size in bytes.
        content_type: The stored MIME type, when S3 reports one.
        last_modified: Upload timestamp, when S3 reports one.
        metadata: The object's user metadata entries.
        content: The body to export verbatim, or ``None`` for a
            metadata-only record set.

    Returns:
        One record per populated field.
    """
    prefix = f"object.{key}"
    records = [
        ExportRecord(
            source=source, field=f"{prefix}.key", category=PiiCategory.COMMUNICATION, value=key
        ),
        ExportRecord(
            source=source, field=f"{prefix}.size", category=PiiCategory.TECHNICAL, value=size
        ),
    ]
    if content_type is not None:
        records.append(
            ExportRecord(
                source=source,
                field=f"{prefix}.content_type",
                category=PiiCategory.TECHNICAL,
                value=content_type,
            )
        )
    if last_modified is not None:
        records.append(
            ExportRecord(
                source=source,
                field=f"{prefix}.last_modified",
                category=PiiCategory.TECHNICAL,
                value=last_modified.isoformat(),
            )
        )
    records.extend(
        ExportRecord(
            source=source,
            field=f"{prefix}.metadata.{name}",
            category=PiiCategory.COMMUNICATION,
            value=value,
        )
        for name, value in sorted(metadata.items())
    )
    if content is not None:
        records.append(
            ExportRecord(
                source=source,
                field=f"{prefix}.content_base64",
                category=PiiCategory.COMMUNICATION,
                value=base64.b64encode(content).decode("ascii"),
            )
        )
    return tuple(records)
