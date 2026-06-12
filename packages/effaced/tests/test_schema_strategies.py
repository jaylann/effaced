"""The schema generator is sound: real derivation accepts every drawn schema."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from schema_strategies import GeneratedSchema, annotated_schemas, scaled_examples

from effaced import ErasurePlanner, ErasureStrategy

pytestmark = pytest.mark.property

_IMPORT_TIME_BUDGET = scaled_examples(1)


def test_hypothesis_profile_was_active_when_budgets_were_read() -> None:
    """The proof suite's claimed depth depends on profile-before-import ordering.

    ``scaled_examples`` reads ``settings.default.max_examples`` at module
    import; profiles activate in ``pytest_configure``, which runs first. If
    that ordering ever breaks, every generated-schema test silently degrades
    to the 100-example dev default — this guard makes it fail loudly instead.
    """
    assert scaled_examples(1) == _IMPORT_TIME_BUDGET
    assert max(20, settings.default.max_examples) == _IMPORT_TIME_BUDGET


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


def test_widened_space_actually_draws_composite_and_deep_shapes() -> None:
    """The widened draw really produces composite hops and deep self-chains.

    The shared invariant proofs only evidence composite ``JoinHop`` pairs and
    depth-4 self-referential chains if the strategy draws them; this guard
    fails loudly if a future change silently narrows the space back.
    """
    seen: dict[str, bool] = {"composite_with_child": False, "deep_self_chain": False}

    @given(schema=annotated_schemas())
    @settings(max_examples=scaled_examples(2), deadline=None)
    def collect(schema: GeneratedSchema) -> None:
        if any(parent in schema.composite_tables for parent in schema.parents.values()):
            seen["composite_with_child"] = True
        if any(
            schema.rows[name] >= 4 and "self_id" in schema.metadata.tables[name].c
            for name in schema.rows
        ):
            seen["deep_self_chain"] = True

    collect()
    assert seen["composite_with_child"], "no composite FK hop with a child was drawn"
    assert seen["deep_self_chain"], "no depth-4 self-referential chain was drawn"
