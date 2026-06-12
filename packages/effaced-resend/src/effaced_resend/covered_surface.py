"""The :class:`~effaced.CoveredSurface` :data:`RESEND_COVERED_SURFACE` declares.

The covered fields are built from the ``_STRING_FIELDS`` and
``_BOOL_FIELDS`` tuples in :mod:`effaced_resend.export_records` — the
same tuples :func:`contact_records` iterates to emit its records — so
declaration and exporter cannot drift by construction.

This is a declaration of *claimed* reach, never a compliance
determination.
"""

from __future__ import annotations

from effaced import CoveredField, CoveredSurface, SurfaceExclusion
from effaced_resend.export_records import _BOOL_FIELDS, _STRING_FIELDS

_COVERED_FIELDS: tuple[CoveredField, ...] = tuple(
    CoveredField(field=f"contact.{key}", category=category)
    for key, category in (*_STRING_FIELDS, *_BOOL_FIELDS)
)

RESEND_COVERED_SURFACE = CoveredSurface(
    resolver="resend",
    fields=_COVERED_FIELDS,
    exclusions=(
        SurfaceExclusion(
            field="contact.properties.*",
            reason="The contact properties blob is caller-defined and "
            "unknowable; PII pushed there belongs to the application's own "
            "data map.",
        ),
    ),
    notes=(
        "Erasure deletes the Resend contact by email; it does not reach "
        "send history (Resend exposes no deletion API for it) and does not "
        "preserve the unsubscribed flag — documented in the README.",
    ),
)
"""Resend's declared covered surface; see :class:`~effaced.CoveredSurface`."""
