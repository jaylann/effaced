"""The :class:`JoinHop` — one foreign-key step on a path to the subject."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class JoinHop(BaseModel):
    """One foreign-key hop on the path from a table toward the subject table.

    Hops are pure column-pair data: ``source_columns[i]`` on
    ``source_table`` joins to ``target_columns[i]`` on ``target_table``.
    Composite foreign keys are expressed as multiple paired columns.
    A self-referential hop (``source_table == target_table``) is valid.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_table: str = Field(min_length=1)
    source_columns: tuple[str, ...] = Field(min_length=1)
    target_table: str = Field(min_length=1)
    target_columns: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _columns_pair_up(self) -> JoinHop:
        """Source and target columns must pair one-to-one."""
        if len(self.source_columns) != len(self.target_columns):
            msg = (
                f"hop {self.source_table!r} -> {self.target_table!r}: "
                f"{len(self.source_columns)} source columns cannot pair with "
                f"{len(self.target_columns)} target columns"
            )
            raise ValueError(msg)
        return self
