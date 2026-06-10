"""The :class:`ErasurePlan` — an ordered, inspectable erasure programme."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from effaced.categories import ErasureStrategy


class ErasureStep(BaseModel):
    """One action the erasure will take, in execution order.

    Attributes:
        target: Table name (local) or resolver name (external).
        strategy: What happens to the matched records.
        external: ``True`` when the step is a resolver call that runs
            through the saga/outbox after the local transaction commits.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    target: str = Field(min_length=1)
    strategy: ErasureStrategy
    external: bool = False


class ErasurePlan(BaseModel):
    """The full programme for erasing one subject.

    Local steps run inside one atomic transaction in FK-safe order;
    external steps are enqueued durably and fanned out afterwards. Plans
    are inspectable so callers (and tests) can assert exactly what an
    erasure will touch *before* anything happens.

    Attributes:
        subject_id: The subject being erased.
        steps: All steps in execution order (local first, then external).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_id: str = Field(min_length=1)
    steps: tuple[ErasureStep, ...] = ()

    @property
    def local_steps(self) -> tuple[ErasureStep, ...]:
        """Steps that run inside the local database transaction."""
        return tuple(step for step in self.steps if not step.external)

    @property
    def external_steps(self) -> tuple[ErasureStep, ...]:
        """Steps that run through the saga/outbox after commit."""
        return tuple(step for step in self.steps if step.external)
