"""The schema generator is sound: real derivation accepts every drawn schema."""

from __future__ import annotations

import pytest
from hypothesis import find, given, settings
from schema_strategies import (
    _SELF_FK_ROW_MAX,
    GeneratedSchema,
    annotated_schemas,
    scaled_examples,
)

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


def test_widened_space_actually_draws_a_composite_hop_with_a_child() -> None:
    """The widened draw really produces a composite FK hop on a subject path.

    The shared invariant proofs only evidence composite ``JoinHop`` pairs if
    the strategy draws a composite parent *with* a child. ``find`` searches
    for exactly that shape (no co-occurrence with other shapes inside one
    small sample) and raises ``NoSuchExample`` if a future change silently
    narrows the space back.
    """
    find(
        annotated_schemas(),
        lambda schema: any(parent in schema.composite_tables for parent in schema.parents.values()),
    )


def test_widened_space_actually_draws_a_depth_4_self_chain() -> None:
    """The widened draw really produces a depth-4 self-referential chain.

    Five seeded rows (indices 0..4) chained through ``self_id`` are four
    hops — the depth the module docstring, PROOFS.md, and testing.md
    advertise. ``find`` raises ``NoSuchExample`` if the space narrows back.
    """
    find(
        annotated_schemas(),
        lambda schema: any(
            schema.rows[name] >= _SELF_FK_ROW_MAX and "self_id" in schema.metadata.tables[name].c
            for name in schema.rows
        ),
    )
