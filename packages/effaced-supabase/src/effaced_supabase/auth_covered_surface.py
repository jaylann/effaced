"""The :class:`~effaced.CoveredSurface` the Supabase Auth resolver declares.

The covered fields are built from ``_USER_FIELDS`` in
:mod:`effaced_supabase.auth_export_records`, so the declaration and the
exporter cannot drift. The exclusions name the GoTrue surfaces the
resolver knowingly does not export, with reasons taken from that module's
docstring.

This is a declaration of *claimed* reach, never a compliance
determination.
"""

from __future__ import annotations

from effaced import CoveredField, CoveredSurface, SurfaceExclusion
from effaced_supabase.auth_export_records import _USER_FIELDS

_COVERED_FIELDS: tuple[CoveredField, ...] = tuple(
    CoveredField(field=f"user.{key}", category=category) for key, category in _USER_FIELDS
)

AUTH_COVERED_SURFACE = CoveredSurface(
    resolver="supabase_auth",
    fields=_COVERED_FIELDS,
    exclusions=(
        SurfaceExclusion(
            field="user.user_metadata*",
            reason="user_metadata is caller-defined and unknowable; PII stored "
            "there belongs to the application's own data map.",
        ),
        SurfaceExclusion(
            field="user.app_metadata*",
            reason="app_metadata is caller-defined and unknowable; PII stored "
            "there belongs to the application's own data map.",
        ),
        SurfaceExclusion(
            field="user.identities*",
            reason="identities is provider-shaped and duplicates the top-level "
            "contact fields, so it is not exported separately.",
        ),
    ),
    notes=(
        "Erasure deletes the GoTrue user, which removes the whole record "
        "including the excluded blobs; the exclusions describe the export "
        "surface, not what deletion reaches.",
    ),
)
"""Supabase Auth's declared covered surface; see :class:`~effaced.CoveredSurface`."""
