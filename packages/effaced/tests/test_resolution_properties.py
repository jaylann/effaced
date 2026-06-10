"""For any generated schema, deletion order never violates FK dependencies."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from effaced import SubjectResolutionError, fk_safe_deletion_order

pytestmark = pytest.mark.property

identifiers = st.text(
    alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
    min_size=1,
    max_size=30,
)


@st.composite
def schemas(draw: st.DrawFn) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """A random FK graph: unique tables, DAG edges, optional self-loops."""
    tables = tuple(draw(st.lists(identifiers, min_size=1, max_size=12, unique=True)))
    indices = range(len(tables))
    pairs = st.tuples(st.sampled_from(indices), st.sampled_from(indices))
    dag_edges = [
        (tables[child], tables[parent])
        for child, parent in draw(st.lists(pairs, max_size=20))
        if child < parent
    ]
    self_loops = [(name, name) for name in draw(st.lists(st.sampled_from(tables), max_size=3))]
    return tables, tuple(dag_edges + self_loops)


@given(schema=schemas())
def test_order_is_a_permutation(
    schema: tuple[tuple[str, ...], tuple[tuple[str, str], ...]],
) -> None:
    tables, edges = schema
    assert sorted(fk_safe_deletion_order(tables, edges)) == sorted(tables)


@given(schema=schemas())
def test_every_child_is_deleted_before_its_parent(
    schema: tuple[tuple[str, ...], tuple[tuple[str, str], ...]],
) -> None:
    tables, edges = schema
    order = fk_safe_deletion_order(tables, edges)
    for child, parent in edges:
        if child != parent:
            assert order.index(child) < order.index(parent)


@given(schema=schemas())
def test_order_is_deterministic(
    schema: tuple[tuple[str, ...], tuple[tuple[str, str], ...]],
) -> None:
    tables, edges = schema
    assert fk_safe_deletion_order(tables, edges) == fk_safe_deletion_order(tables, edges)


@given(tables=st.lists(identifiers, min_size=2, max_size=8, unique=True))
def test_any_cycle_is_rejected(tables: list[str]) -> None:
    ring = tuple(zip(tables, tables[1:] + tables[:1], strict=True))
    with pytest.raises(SubjectResolutionError, match="cycle"):
        fk_safe_deletion_order(tuple(tables), ring)
