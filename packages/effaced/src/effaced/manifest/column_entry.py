"""The :class:`ColumnEntry` manifest node."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from effaced.annotations import PiiSpec


class ColumnEntry(BaseModel):
    """One annotated field in the data map.

    Attributes:
        name: Field name as it exists in the store.
        spec: The personal-data declaration attached to it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    spec: PiiSpec
