"""The schema generator is sound: real derivation accepts every drawn schema."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from schema_strategies import GeneratedSchema, annotated_schemas, scaled_examples

from effaced import ErasurePlanner, ErasureStrategy

pytestmark = pytest.mark.property


@given(schema=annotated_schemas())
@settings(max_examples=scaled_examples(4), deadline=None)
def test_generated_schemas_pass_real_derivation(schema: GeneratedSchema) -> None:
    """Collection, graph resolution, and planning never reject a drawn schema."""
    assert {entry.name for entry in schema.data_map.tables} == set(schema.graph.deletion_order)
    plan = ErasurePlanner(schema.data_map, schema.graph).plan("1")
    assert plan.subject_id == "1"
    assert not plan.external_steps


@given(schema=annotated_schemas())
@settings(max_examples=scaled_examples(4), deadline=None)
def test_planner_row_deletion_matches_generator_expectation(schema: GeneratedSchema) -> None:
    """The generator's ADR 0007 classification agrees with the real planner.

    Pinning this here keeps downstream proof failures readable: a bug in
    the generator's expectations shrinks to this test, not to a bleed or
    retention property.
    """
    plan = ErasurePlanner(schema.data_map, schema.graph).plan("1")
    deleted = {step.target for step in plan.local_steps if step.strategy is ErasureStrategy.DELETE}
    assert deleted == schema.row_deleted_tables
