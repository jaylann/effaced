"""The :class:`~effaced.CoveredSurface` :data:`RESEND_COVERED_SURFACE` declares.

The string contact fields are built from ``_STRING_FIELDS`` in
:mod:`effaced_resend.export_records`; ``contact.unsubscribed`` is added
explicitly because :func:`contact_records` emits it from a separate
boolean branch. Declaration and exporter share that tuple and cannot
drift.

This is a declaration of *claimed* reach, never a compliance
determination.
"""

from __future__ import annotations

from effaced import CoveredField, CoveredSurface, PiiCategory, SurfaceExclusion
from effaced_resend.export_records import _STRING_FIELDS

_COVERED_FIELDS: tuple[CoveredField, ...] = (
    *(CoveredField(field=f"contact.{key}", category=category) for key, category in _STRING_FIELDS),
    CoveredField(field="contact.unsubscribed", category=PiiCategory.BEHAVIORAL),
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
