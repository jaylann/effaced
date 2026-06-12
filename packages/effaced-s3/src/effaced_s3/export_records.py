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

# The per-object export fields, as (suffix, category) pairs — the single
# source of truth: :func:`object_records` iterates this tuple to emit its
# records, and the covered-surface declarations build their globs from it,
# so emitter and declaration cannot drift by construction. The object key
# is dynamic, so a covered-surface field globs it as ``object.*.<suffix>``
# (and ``object.*.metadata.*`` for the open-ended metadata entries). The
# ``metadata`` suffix here is the *prefix* of those per-entry fields.
_OBJECT_FIELDS: tuple[tuple[str, PiiCategory], ...] = (
    ("key", PiiCategory.COMMUNICATION),
    ("size", PiiCategory.TECHNICAL),
    ("content_type", PiiCategory.TECHNICAL),
    ("last_modified", PiiCategory.TECHNICAL),
    ("metadata", PiiCategory.COMMUNICATION),
    ("content_base64", PiiCategory.COMMUNICATION),
)

_METADATA_SUFFIX = "metadata"


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
        One record per populated field, in ``_OBJECT_FIELDS`` order — the
        emitter iterates that tuple, so the emitted field set and the
        covered-surface declarations built from it cannot drift.
    """
    prefix = f"object.{key}"
    scalars: dict[str, str | int | None] = {
        "key": key,
        "size": size,
        "content_type": content_type,
        "last_modified": None if last_modified is None else last_modified.isoformat(),
        "content_base64": None if content is None else base64.b64encode(content).decode("ascii"),
    }
    records: list[ExportRecord] = []
    for suffix, category in _OBJECT_FIELDS:
        if suffix == _METADATA_SUFFIX:
            records.extend(
                ExportRecord(
                    source=source,
                    field=f"{prefix}.{suffix}.{name}",
                    category=category,
                    value=value,
                )
                for name, value in sorted(metadata.items())
            )
            continue
        value = scalars[suffix]
        if value is not None:
            records.append(
                ExportRecord(
                    source=source, field=f"{prefix}.{suffix}", category=category, value=value
                )
            )
    return tuple(records)
