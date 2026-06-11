"""The :class:`CompletenessFinding` lint result."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CompletenessFinding(BaseModel):
    """One place where the data map may be silently incomplete.

    Emitted by completeness linters such as
    :func:`effaced.adapters.sqlalchemy.lint_completeness`. A finding is a
    question, not a verdict: it marks data the manifest does not cover so a
    human can either annotate it or consciously exempt it. effaced never
    decides on your behalf that data is not personal.

    Attributes:
        table: Name of the store the finding points at.
        column: The unannotated field, or ``None`` when the whole store
            carries no effaced annotations at all.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    table: str = Field(min_length=1)
    column: str | None = Field(default=None, min_length=1)

    @property
    def message(self) -> str:
        """A human-readable one-liner, written for CI logs."""
        if self.column is None:
            return (
                f"table {self.table!r} carries no effaced annotations — its data "
                "is invisible to export and erasure"
            )
        return (
            f"column {self.table}.{self.column} is not annotated and is neither "
            "a primary nor a foreign key"
        )
