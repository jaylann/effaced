"""The :class:`~effaced.CoveredSurface` :data:`S3_COVERED_SURFACE` declares.

The covered fields are built from ``_OBJECT_FIELDS`` in
:mod:`effaced_s3.export_records` — the same tuple :func:`object_records`
emits from — so the declaration and the exporter cannot drift. Object
keys (and metadata entry names) are dynamic, so each field is a glob:
``object.*.<suffix>`` and ``object.*.metadata.*`` for the open-ended user
metadata.

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

S3_COVERED_SURFACE = CoveredSurface(
    resolver="s3",
    fields=_COVERED_FIELDS,
    notes=(
        "Versions asymmetry (deliberate, documented): export covers the "
        "current object versions only, while erasure removes ALL versions "
        "and delete markers under the subject's prefix. The exported "
        "content is the object body, base64-encoded.",
    ),
)
"""S3's declared covered surface; see :class:`~effaced.CoveredSurface`."""
