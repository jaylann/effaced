"""The :class:`Correction` model — one category-keyed corrected value."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from effaced.categories import PiiCategory


class Correction(BaseModel):
    """One Art. 16 correction: a category and the value it should hold.

    Corrections are keyed by :class:`~effaced.PiiCategory`, never by column
    (ADR 0013): the category is the only vocabulary shared with external
    resolvers, and a category-wide write keeps denormalized copies of the
    same fact consistent. Values are JSON scalars so a correction
    round-trips losslessly through the outbox payload.

    The value **is** personal data. It lives transiently in outbox rows
    while external rectification is in flight — cleared the moment the
    entry reaches a terminal status — and never appears in any audit
    event.

    Attributes:
        category: Which kind of personal data the correction targets.
        value: The corrected value, applied to every matching field.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: PiiCategory
    value: str | int | float | bool
