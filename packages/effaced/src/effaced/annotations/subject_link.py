"""The :class:`SubjectLink` model — how a data store reaches the subject."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SubjectLink(BaseModel):
    """How a table's records reach the data subject.

    A dotted relationship path from the annotated table to the subject
    table, e.g. ``"order.user"`` for an ``order_items`` table whose records
    belong to the user owning the parent order. The subject table itself
    uses the empty path ``""``.

    Attributes:
        path: Dotted relationship path; ``""`` marks the subject table.
        subject_id_columns: Ordered identifier columns on the subject table
            that callers' :data:`~effaced.SubjectIdentifier` aligns to.
            Defaults to ``("id",)`` — one column, the single-column case. A
            multi-column tuple declares a composite subject key (ADR 0025);
            effaced always matches the *whole* ordered key, never a partial
            one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    subject_id_columns: tuple[str, ...] = Field(default=("id",), min_length=1)

    @model_validator(mode="after")
    def _columns_are_distinct_and_named(self) -> SubjectLink:
        """Subject-id columns must be non-empty and free of duplicates."""
        if any(name == "" for name in self.subject_id_columns):
            msg = "subject_id_columns must all be non-empty column names"
            raise ValueError(msg)
        if len(set(self.subject_id_columns)) != len(self.subject_id_columns):
            msg = f"subject_id_columns must be distinct: {list(self.subject_id_columns)}"
            raise ValueError(msg)
        return self

    @property
    def is_subject_table(self) -> bool:
        """Whether this link marks the subject table itself."""
        return self.path == ""
