"""Mapping of GoTrue admin user objects to :class:`~effaced.ExportRecord` rows.

The exported field set is behaviour under widened SemVer: adding fields
is MINOR, removing or recategorising any is MAJOR. Only the top-level
``email`` and ``phone`` are exported. ``user_metadata`` and
``app_metadata`` are caller-defined and unknowable, so they are never
exported; ``identities`` is provider-shaped and duplicates the top-level
contact fields, so it is likewise skipped.

``legal_basis``, ``purpose`` and ``retention_reason`` stay ``None`` on
every record: a resolver cannot know why the application holds the data;
that metadata belongs to the manifest-declared local data map.
"""

from __future__ import annotations

from collections.abc import Mapping

from effaced import ExportRecord, PiiCategory

_SOURCE = "supabase_auth"

_USER_FIELDS = (
    ("email", PiiCategory.CONTACT),
    ("phone", PiiCategory.CONTACT),
)


def _contact_scalar(user: Mapping[str, object], key: str) -> str | None:
    """Fetch a contact scalar; absent, empty, or odd-typed values drop.

    GoTrue stores an unset ``phone`` (and ``email`` on phone-only users)
    as ``""`` rather than null — an empty string means "not held" and is
    dropped rather than exported as noise.
    """
    value = user.get(key)
    if not isinstance(value, str) or not value:
        return None
    return value


def user_records(user: object) -> tuple[ExportRecord, ...]:
    """Map a GoTrue admin user object's contact fields.

    Args:
        user: The decoded ``GET /auth/v1/admin/users/{id}`` body;
            anything that is not a JSON object yields no records.

    Returns:
        One record per populated contact field; nothing for absent,
        null, empty, or odd-typed fields.
    """
    if not isinstance(user, Mapping):
        return ()
    return tuple(
        ExportRecord(source=_SOURCE, field=f"user.{key}", category=category, value=value)
        for key, category in _USER_FIELDS
        if (value := _contact_scalar(user, key)) is not None
    )
