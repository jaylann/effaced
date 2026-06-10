"""The :class:`TableAccessPlan` — how one table's rows reach the subject."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from effaced.manifest.resolution.join_hop import JoinHop


class TableAccessPlan(BaseModel):
    """How one table's rows are reached from a subject identifier.

    The hop chain walks from :attr:`table` toward the subject table; an
    engine turns it into a correlated join ("select/delete this table's
    rows whose chain ends at the given subject id"). An empty chain means
    the table *is* the subject table.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    table: str = Field(min_length=1)
    hops: tuple[JoinHop, ...] = ()

    @model_validator(mode="after")
    def _chain_is_contiguous(self) -> TableAccessPlan:
        """Hops must start at :attr:`table` and link end to end."""
        source = self.table
        for hop in self.hops:
            if hop.source_table != source:
                msg = (
                    f"table {self.table!r}: hop starts at {hop.source_table!r} "
                    f"but the chain is at {source!r}"
                )
                raise ValueError(msg)
            source = hop.target_table
        return self

    @property
    def is_subject_table(self) -> bool:
        """Whether this table is the subject table itself."""
        return not self.hops
