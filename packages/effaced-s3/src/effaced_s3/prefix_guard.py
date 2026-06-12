"""The :func:`checked_prefix` guard against whole-bucket and sibling bleed."""

from __future__ import annotations

from typing import TYPE_CHECKING

from effaced.exceptions import ResolverError

if TYPE_CHECKING:
    from effaced.annotations import SubjectRef


def checked_prefix(ref: SubjectRef) -> str:
    """The ref's key prefix, validated before any object-store call.

    Object-store prefixes are literal substring matches, so a prefix that
    is not delimiter-terminated also matches sibling subjects
    (``users/4`` matches ``users/42/avatar.png``) — that is cross-subject
    bleed, the one thing a resolver must never do. Both guards run before
    any call.

    Args:
        ref: The subject reference whose ``value`` is the key prefix.

    Returns:
        The validated prefix, unchanged.

    Raises:
        ResolverError: The prefix is blank (it would address the whole
            bucket) or does not end with ``"/"`` (it would match sibling
            subjects).
    """
    if not ref.value.strip():
        raise ResolverError("subject ref prefix is blank — refusing to touch the whole bucket")
    if not ref.value.endswith("/"):
        raise ResolverError(
            "subject ref prefix must end with '/' — an unterminated prefix also "
            "matches sibling subjects ('users/4' matches 'users/42/...')"
        )
    return ref.value
