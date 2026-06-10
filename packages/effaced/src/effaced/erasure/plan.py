"""The :class:`ErasurePlan` — an ordered, inspectable erasure programme."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from effaced.annotations import SubjectRef
from effaced.categories import ErasureStrategy


class ErasureStep(BaseModel):
    """One action the erasure will take, in execution order.

    Local ``DELETE`` steps remove whole rows; local ``ANONYMIZE`` and
    ``RETAIN`` steps are column-level and must name the columns they
    touch (or, for ``RETAIN``, deliberately leave untouched). External
    steps address a whole subject in a resolver, never columns — a
    validator makes any other shape unrepresentable.

    Attributes:
        target: Table name (local) or resolver name (external).
        strategy: What happens to the matched records.
        external: ``True`` when the step is a resolver call that runs
            through the saga/outbox after the local transaction commits.
        columns: The column names the step touches; empty for whole-row
            deletion and for external steps.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    target: str = Field(min_length=1)
    strategy: ErasureStrategy
    external: bool = False
    columns: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _columns_match_step_shape(self) -> ErasureStep:
        """Column lists belong to local column-level steps only."""
        columnwise = not self.external and self.strategy is not ErasureStrategy.DELETE
        if columnwise and not self.columns:
            msg = (
                f"step {self.target!r}: a local {self.strategy.value} step "
                f"must name the columns it covers"
            )
            raise ValueError(msg)
        if not columnwise and self.columns:
            kind = "an external" if self.external else "a row-deletion"
            msg = f"step {self.target!r}: {kind} step operates on whole rows, not columns"
            raise ValueError(msg)
        return self


class ErasurePlan(BaseModel):
    """The full programme for erasing one subject.

    Local steps run inside one atomic transaction in FK-safe order;
    external steps are enqueued durably and fanned out afterwards. Plans
    are inspectable so callers (and tests) can assert exactly what an
    erasure will touch *before* anything happens.

    Attributes:
        subject_id: The subject being erased.
        steps: All steps in execution order (local first, then external).
        refs: The external-system references the erasure will hand to
            resolvers — recorded for inspectability; the executor matches
            them to resolvers at execution time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_id: str = Field(min_length=1)
    steps: tuple[ErasureStep, ...] = ()
    refs: tuple[SubjectRef, ...] = ()

    @property
    def local_steps(self) -> tuple[ErasureStep, ...]:
        """Steps that run inside the local database transaction."""
        return tuple(step for step in self.steps if not step.external)

    @property
    def external_steps(self) -> tuple[ErasureStep, ...]:
        """Steps that run through the saga/outbox after commit."""
        return tuple(step for step in self.steps if step.external)
