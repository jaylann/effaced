"""Mapping of Intercom contacts and conversations to :class:`~effaced.ExportRecord` rows.

The exported field set is behaviour under widened SemVer: adding fields
is MINOR, removing or recategorising any is MAJOR. A contact's top-level
``email``, ``name``, and ``phone`` are exported, plus per-conversation
metadata (``created_at``, ``updated_at``, ``state``) keyed by conversation
id. Message bodies (``conversation_parts``, ``source.body``) and the
caller-defined ``custom_attributes`` blob are never exported — the former
is deep COMMUNICATION content the export deliberately leaves untouched,
the latter is unknowable to this resolver and belongs to the
application's own data map.

``legal_basis``, ``purpose`` and ``retention_reason`` stay ``None`` on
every record: a resolver cannot know why the application holds the data;
that metadata belongs to the manifest-declared local data map.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from effaced import ExportRecord, PiiCategory

_SOURCE = "intercom"

_CONTACT_FIELDS = (
    ("email", PiiCategory.CONTACT),
    ("name", PiiCategory.IDENTITY),
    ("phone", PiiCategory.CONTACT),
)

# Conversation metadata only — interaction logs, never message bodies. The
# ids are dynamic, so the covered surface globs them (``conversation.*.*``)
# while the exporter emits one concrete id per held conversation.
_CONVERSATION_FIELDS = (
    ("created_at", PiiCategory.BEHAVIORAL),
    ("updated_at", PiiCategory.BEHAVIORAL),
    ("state", PiiCategory.BEHAVIORAL),
)


def _contact_scalar(contact: Mapping[str, object], key: str) -> str | None:
    """Fetch a string scalar; absent, empty, or odd-typed values drop.

    Intercom stores an unset profile field as ``""`` or ``null`` — both
    mean "not held" and are dropped rather than exported as noise.
    """
    value = contact.get(key)
    if not isinstance(value, str) or not value:
        return None
    return value


def _conversation_scalar(conversation: Mapping[str, object], key: str) -> str | int | None:
    """Fetch a conversation-metadata scalar; drop absent or odd-typed values.

    ``created_at``/``updated_at`` are unix-second integers and ``state``
    is a short string; booleans (never these fields, but guarded anyway)
    and everything else drop.
    """
    value = conversation.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value:
        return value
    return None


def contact_records(contact: object) -> tuple[ExportRecord, ...]:
    """Map an Intercom contact object's PII fields.

    Args:
        contact: The decoded ``GET /contacts/{id}`` body; anything that
            is not a JSON object yields no records.

    Returns:
        One record per populated field; nothing for absent, null,
        empty, or odd-typed fields.
    """
    if not isinstance(contact, Mapping):
        return ()
    return tuple(
        ExportRecord(source=_SOURCE, field=f"contact.{key}", category=category, value=value)
        for key, category in _CONTACT_FIELDS
        if (value := _contact_scalar(contact, key)) is not None
    )


def conversation_records(conversation: object) -> tuple[ExportRecord, ...]:
    """Map one conversation object's metadata, keyed by its id.

    Args:
        conversation: One decoded conversation from the search payload;
            anything that is not a JSON object, or that carries no usable
            id, yields no records.

    Returns:
        One record per populated metadata field, each ``field`` namespaced
        as ``conversation.{id}.{key}``; never any message content.
    """
    if not isinstance(conversation, Mapping):
        return ()
    conversation_id = conversation.get("id")
    if isinstance(conversation_id, bool) or not isinstance(conversation_id, str | int):
        return ()
    prefix = f"conversation.{conversation_id}"
    return tuple(
        ExportRecord(source=_SOURCE, field=f"{prefix}.{key}", category=category, value=value)
        for key, category in _CONVERSATION_FIELDS
        if (value := _conversation_scalar(conversation, key)) is not None
    )


def conversations_records(conversations: Iterable[object]) -> tuple[ExportRecord, ...]:
    """Flatten :func:`conversation_records` across every searched conversation."""
    return tuple(
        record for conversation in conversations for record in conversation_records(conversation)
    )
