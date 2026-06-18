"""The :data:`SubjectIdentifier` alias and its canonical serialization.

A subject is identified either by a bare ``str`` (the single-column case) or
by a :class:`~effaced.CompositeSubjectId` (the multi-column case). Both
collapse to one deterministic, collision-free canonical string for storage
and the audit trail — a bare ``str`` is its own canonical form, so single-
column schemas stay byte-identical (ADR 0025).
"""

from __future__ import annotations

from effaced.annotations.composite_subject_id import CompositeSubjectId

SubjectIdentifier = str | CompositeSubjectId
"""What every engine entry point accepts to name a data subject.

A bare ``str`` for a single-column subject, or a
:class:`~effaced.CompositeSubjectId` for a multi-column one. The bare-``str``
form is the one-element canonical case, so passing a string behaves exactly
as it always has (ADR 0025).
"""

_SEPARATOR = "\x1f"
"""ASCII unit separator joining the escaped elements of a composite key."""

_ESCAPE = "\x1b"
"""ASCII escape prefixing a literal separator or escape inside an element."""


def _escape_element(value: str) -> str:
    """Escape one element so no value can forge a separator boundary.

    The escape character is doubled first, then literal separators are
    escaped — order matters so an escaped separator is never re-escaped.
    """
    return value.replace(_ESCAPE, _ESCAPE + _ESCAPE).replace(_SEPARATOR, _ESCAPE + _SEPARATOR)


def canonical_subject_id(identifier: SubjectIdentifier) -> str:
    """Serialize a subject identifier to its canonical storage string.

    A bare ``str`` is returned **unchanged** — it is already the one-element
    canonical form, so single-column subjects are byte-identical in storage,
    audit references, and SQL to how they were before composite keys existed.

    A :class:`~effaced.CompositeSubjectId` joins its escaped element values on
    a reserved separator. The escaping guarantees the result is collision-
    free: distinct keys (including keys that differ only in where a separator-
    like character falls, such as ``("a", "b:c")`` versus ``("a:b", "c")``)
    always serialize to distinct strings. Saga completion-grouping and cross-
    subject isolation both depend on that distinctness.

    Args:
        identifier: A single-column ``str`` or a multi-column
            :class:`~effaced.CompositeSubjectId`.

    Returns:
        The canonical string. Round-trips through :func:`parse_canonical`
        for the composite case; a bare ``str`` parses back to itself.
    """
    if isinstance(identifier, str):
        return identifier
    return _SEPARATOR.join(_escape_element(value) for value in identifier.values)


def normalize_subject_id(value: object) -> object:
    """Field-validator hook collapsing a composite identifier to its string.

    Domain/storage models key a subject by one canonical ``str`` column (no
    owned-table DDL change, ADR 0025). Used as a ``mode="before"``
    ``field_validator`` so those models accept a
    :class:`~effaced.CompositeSubjectId` and store its canonical string; any
    other value is passed through untouched for the field's own validation
    (a non-string then fails the field's ``str`` constraint as before).

    Args:
        value: The raw field input — a ``str``, a
            :class:`~effaced.CompositeSubjectId`, or anything else.

    Returns:
        The canonical string for a composite identifier; the input
        unchanged otherwise.
    """
    if isinstance(value, CompositeSubjectId):
        return canonical_subject_id(value)
    return value


def parse_canonical(serialized: str) -> CompositeSubjectId:
    """Parse a canonical composite string back into its ordered values.

    The exact inverse of :func:`canonical_subject_id` for the composite case:
    it splits on unescaped separators and unescapes each element, so a value
    that itself contained the separator or escape character is restored
    intact.

    Args:
        serialized: A canonical string produced by
            :func:`canonical_subject_id` from a
            :class:`~effaced.CompositeSubjectId`.

    Returns:
        The reconstructed :class:`~effaced.CompositeSubjectId`.
    """
    elements: list[str] = []
    current: list[str] = []
    escaped = False
    for char in serialized:
        if escaped:
            current.append(char)
            escaped = False
        elif char == _ESCAPE:
            escaped = True
        elif char == _SEPARATOR:
            elements.append("".join(current))
            current = []
        else:
            current.append(char)
    elements.append("".join(current))
    return CompositeSubjectId(values=tuple(elements))
