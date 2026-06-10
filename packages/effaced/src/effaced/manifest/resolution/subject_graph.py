"""The :class:`SubjectGraph` — every table's resolved path to the subject."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from effaced.exceptions import SubjectResolutionError
from effaced.manifest.resolution.table_access_plan import TableAccessPlan


class SubjectGraph(BaseModel):
    """Resolved subject reachability for every subject-linked table.

    :attr:`accesses` is ordered FK-safely for deletion: children before
    parents, the subject table last. Validators make incoherent graphs
    unrepresentable — exactly one access is the subject table and every
    other chain terminates at it — so consumers (the erasure planner, the
    exporter) need not re-check.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_table: str = Field(min_length=1)
    subject_id_column: str = Field(min_length=1)
    accesses: tuple[TableAccessPlan, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _graph_is_coherent(self) -> SubjectGraph:
        """Accesses must be unique and all chains must end at the subject."""
        names = [access.table for access in self.accesses]
        if len(names) != len(set(names)):
            msg = f"subject graph contains duplicate table accesses: {sorted(names)}"
            raise ValueError(msg)
        for access in self.accesses:
            if access.is_subject_table:
                if access.table != self.subject_table:
                    msg = f"table {access.table!r} has an empty hop chain but is not the subject"
                    raise ValueError(msg)
            elif access.hops[-1].target_table != self.subject_table:
                msg = (
                    f"table {access.table!r}: chain ends at "
                    f"{access.hops[-1].target_table!r}, not the subject table "
                    f"{self.subject_table!r}"
                )
                raise ValueError(msg)
        if self.subject_table not in names:
            msg = f"subject table {self.subject_table!r} has no access entry"
            raise ValueError(msg)
        return self

    @property
    def deletion_order(self) -> tuple[str, ...]:
        """Table names in FK-safe deletion order (children first)."""
        return tuple(access.table for access in self.accesses)

    def access(self, table: str) -> TableAccessPlan:
        """Return one table's access plan.

        Args:
            table: The table name to look up.

        Returns:
            The matching :class:`TableAccessPlan`.

        Raises:
            SubjectResolutionError: If the table is not in the graph.
        """
        for candidate in self.accesses:
            if candidate.table == table:
                return candidate
        msg = f"table {table!r} is not in the subject graph"
        raise SubjectResolutionError(msg)
