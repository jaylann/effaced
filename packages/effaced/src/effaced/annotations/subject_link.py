"""The :class:`SubjectLink` model — how a data store reaches the subject."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SubjectLink(BaseModel):
    """How a table's records reach the data subject.

    A dotted relationship path from the annotated table to the subject
    table, e.g. ``"order.user"`` for an ``order_items`` table whose records
    belong to the user owning the parent order. The subject table itself
    uses the empty path ``""``.

    Attributes:
        path: Dotted relationship path; ``""`` marks the subject table.
        subject_id_column: Identifier field on the subject table that
            callers pass to export/erasure entry points. Defaults to ``"id"``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    subject_id_column: str = Field(default="id", min_length=1)

    @property
    def is_subject_table(self) -> bool:
        """Whether this link marks the subject table itself."""
        return self.path == ""
