"""The :class:`RectificationStep` model — one value-free local write target."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from effaced.categories import PiiCategory


class RectificationStep(BaseModel):
    """One local table's columns a correction will rewrite.

    Steps are deliberately **value-free**: the corrected value travels
    separately (see
    :meth:`~effaced.rectification.RectificationStepExecutor.execute`), so
    a plan of steps never carries PII and stays safely inspectable and
    loggable.

    Attributes:
        target: The table the step updates.
        category: The PII category whose correction the step applies.
        columns: The category's annotated columns on the table — every one
            receives the same corrected value (ADR 0013).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    target: str = Field(min_length=1)
    category: PiiCategory
    columns: tuple[str, ...] = Field(min_length=1)
