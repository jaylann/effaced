"""Mapping of Resend contact objects to :class:`~effaced.ExportRecord` rows.

The exported field set is behaviour under widened SemVer: adding fields
is MINOR, removing or recategorising any is MAJOR. The top-level
``email``, ``first_name``, ``last_name``, and ``unsubscribed`` are
exported. The ``properties`` blob is caller-defined and unknowable, so
it is never exported — PII pushed into contact properties belongs to the
application's own data map.

``legal_basis``, ``purpose`` and ``retention_reason`` stay ``None`` on
every record: a resolver cannot know why the application holds the data;
that metadata belongs to the manifest-declared local data map.
"""

from __future__ import annotations

from collections.abc import Mapping

from effaced import ExportRecord, PiiCategory

_SOURCE = "resend"

_STRING_FIELDS = (
    ("email", PiiCategory.CONTACT),
    ("first_name", PiiCategory.IDENTITY),
    ("last_name", PiiCategory.IDENTITY),
)


def _contact_scalar(contact: Mapping[str, object], key: str) -> str | None:
    """Fetch a string scalar; absent, empty, or odd-typed values drop.

    Resend stores an unset name field as ``""`` or ``null`` — both mean
    "not held" and are dropped rather than exported as noise.
    """
    value = contact.get(key)
    if not isinstance(value, str) or not value:
        return None
    return value


def contact_records(contact: object) -> tuple[ExportRecord, ...]:
    """Map a Resend contact object's PII fields.

    Args:
        contact: The decoded ``GET /contacts/{email}`` body; anything
            that is not a JSON object yields no records.

    Returns:
        One record per populated field; nothing for absent, null,
        empty, or odd-typed fields. ``unsubscribed`` is exported only
        when it is a real boolean — it is the subject's opt-out
        preference, worth carrying into a suppression list before
        erasure.
    """
    if not isinstance(contact, Mapping):
        return ()
    records = [
        ExportRecord(source=_SOURCE, field=f"contact.{key}", category=category, value=value)
        for key, category in _STRING_FIELDS
        if (value := _contact_scalar(contact, key)) is not None
    ]
    unsubscribed = contact.get("unsubscribed")
    if isinstance(unsubscribed, bool):
        records.append(
            ExportRecord(
                source=_SOURCE,
                field="contact.unsubscribed",
                category=PiiCategory.BEHAVIORAL,
                value=unsubscribed,
            )
        )
    return tuple(records)
