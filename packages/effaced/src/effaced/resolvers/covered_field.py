"""The :class:`CoveredField` — one PII field a resolver claims to reach."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from effaced.categories import PiiCategory


class CoveredField(BaseModel):
    """One field, with its category, that a resolver claims to cover.

    A covered field declares a single PII-bearing
    :class:`~effaced.ExportRecord` field the resolver reaches for export
    and erasure. The ``field`` is an :func:`fnmatch.fnmatch` glob matched
    against :attr:`ExportRecord.field <effaced.ExportRecord.field>`, so a
    dynamic surface — an S3 object key, a per-object metadata entry, a
    payment-method id — is declared once rather than enumerated. For
    example ``object.*.metadata.*`` matches every user-metadata entry of
    every object the resolver lists.

    The glob is matched case-sensitively against the whole field path:
    ``*`` spans any run of characters (including the ``.`` separators, so
    a single ``*`` covers a multi-segment key), ``?`` matches one
    character, and ``[seq]`` a character class. A plain literal with no
    wildcard matches exactly one field.

    Attributes:
        field: The :func:`fnmatch.fnmatch` glob the covered field path
            must match.
        category: The :class:`~effaced.PiiCategory` the matched field is
            declared to hold; the conformance suite checks a covered
            record carries the *same* category.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str = Field(min_length=1)
    category: PiiCategory
