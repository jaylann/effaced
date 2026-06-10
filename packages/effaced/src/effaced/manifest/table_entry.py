"""The :class:`TableEntry` manifest node."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from effaced.annotations import SubjectLink
from effaced.manifest.column_entry import ColumnEntry


class TableEntry(BaseModel):
    """One data store (table/collection) that holds personal data.

    Attributes:
        name: Store name.
        subject_link: How records reach the data subject. ``None`` until the
            store declares one — the completeness check flags this loudly.
        columns: The annotated fields, in declaration order.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    subject_link: SubjectLink | None = None
    columns: tuple[ColumnEntry, ...] = ()
