"""The :class:`~effaced.CoveredSurface` the Supabase Storage resolver declares.

Supabase Storage rides effaced-s3's object machinery (ADR 0016), so the
covered fields are built from the same ``_OBJECT_FIELDS`` tuple in
:mod:`effaced_s3.export_records` that :func:`object_records` emits — the
declaration and the exporter share one source and cannot drift. This
module is part of the optional ``storage`` extra, so importing it without
``effaced-s3`` installed fails on the import below, exactly as the
resolver module does.

This is a declaration of *claimed* reach, never a compliance
determination.
"""

from __future__ import annotations

from effaced import CoveredField, CoveredSurface, PiiCategory
from effaced_s3.export_records import _OBJECT_FIELDS


def _covered_field(suffix: str, category: PiiCategory) -> CoveredField:
    """A glob over the dynamic object key for one export suffix."""
    glob = "object.*.metadata.*" if suffix == "metadata" else f"object.*.{suffix}"
    return CoveredField(field=glob, category=category)


_COVERED_FIELDS: tuple[CoveredField, ...] = tuple(
    _covered_field(suffix, category) for suffix, category in _OBJECT_FIELDS
)

STORAGE_COVERED_SURFACE = CoveredSurface(
    resolver="supabase_storage",
    fields=_COVERED_FIELDS,
    notes=(
        "Supabase Storage has no object versioning (ADR 0016): the gateway "
        "implements no ListObjectVersions, so deleting the current objects "
        "under the subject's prefix IS complete erasure — there is no "
        "version history to sweep. Export and erasure reach the same set.",
    ),
)
"""Supabase Storage's declared covered surface; see :class:`~effaced.CoveredSurface`."""
