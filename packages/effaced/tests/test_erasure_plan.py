"""Erasure plans separate local from external work and validate step shape."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from effaced import ErasurePlan, ErasureStep, ErasureStrategy, SubjectRef


def test_local_and_external_steps_partition() -> None:
    plan = ErasurePlan(
        subject_id="42",
        steps=(
            ErasureStep(
                target="invoices",
                strategy=ErasureStrategy.ANONYMIZE,
                columns=("billing_address",),
            ),
            ErasureStep(target="users", strategy=ErasureStrategy.DELETE),
            ErasureStep(target="stripe", strategy=ErasureStrategy.DELETE, external=True),
        ),
    )
    assert [step.target for step in plan.local_steps] == ["invoices", "users"]
    assert [step.target for step in plan.external_steps] == ["stripe"]


def test_refs_round_trip_on_the_plan() -> None:
    refs = (SubjectRef(kind="stripe_customer", value="cus_1"),)
    plan = ErasurePlan(subject_id="42", refs=refs)
    assert plan.refs == refs
    assert ErasurePlan(subject_id="42").refs == ()


def test_local_anonymize_step_requires_columns() -> None:
    with pytest.raises(ValidationError, match="must name the columns"):
        ErasureStep(target="users", strategy=ErasureStrategy.ANONYMIZE)


def test_local_retain_step_requires_columns() -> None:
    with pytest.raises(ValidationError, match="must name the columns"):
        ErasureStep(target="invoices", strategy=ErasureStrategy.RETAIN)


def test_local_delete_step_rejects_columns() -> None:
    with pytest.raises(ValidationError, match="whole rows"):
        ErasureStep(target="users", strategy=ErasureStrategy.DELETE, columns=("email",))


def test_external_step_rejects_columns() -> None:
    with pytest.raises(ValidationError, match="whole rows"):
        ErasureStep(
            target="stripe",
            strategy=ErasureStrategy.DELETE,
            external=True,
            columns=("email",),
        )
