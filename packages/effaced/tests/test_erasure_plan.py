"""Erasure plans separate local from external work."""

from __future__ import annotations

from effaced import ErasurePlan, ErasureStep, ErasureStrategy


def test_local_and_external_steps_partition() -> None:
    plan = ErasurePlan(
        subject_id="42",
        steps=(
            ErasureStep(target="invoices", strategy=ErasureStrategy.ANONYMIZE),
            ErasureStep(target="users", strategy=ErasureStrategy.DELETE),
            ErasureStep(target="stripe", strategy=ErasureStrategy.DELETE, external=True),
        ),
    )
    assert [step.target for step in plan.local_steps] == ["invoices", "users"]
    assert [step.target for step in plan.external_steps] == ["stripe"]
