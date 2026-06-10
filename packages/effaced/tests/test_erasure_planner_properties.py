"""For any generated manifest+graph, plans preserve the planner invariants."""

from __future__ import annotations

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from effaced import (
    DataMap,
    ErasurePlan,
    ErasurePlanner,
    ErasureStrategy,
    JoinHop,
    ManifestError,
    PiiCategory,
    PiiSpec,
    ResolverErasure,
    ResolverExport,
    ResolverRegistry,
    RetentionPolicy,
    RetentionViolationError,
    SubjectGraph,
    SubjectLink,
    SubjectRef,
    TableAccessPlan,
    TableEntry,
)
from effaced.manifest import ColumnEntry

pytestmark = pytest.mark.property

identifiers = st.text(
    alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
    min_size=1,
    max_size=12,
)

strategies = st.sampled_from(
    (ErasureStrategy.DELETE, ErasureStrategy.ANONYMIZE, ErasureStrategy.RETAIN)
)


class FakeResolver:
    """A resolver double — never called at plan time."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        raise NotImplementedError

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        raise NotImplementedError


def _spec(strategy: ErasureStrategy) -> PiiSpec:
    retention = RetentionPolicy(reason="test duty") if strategy is ErasureStrategy.RETAIN else None
    return PiiSpec(category=PiiCategory.CONTACT, erasure=strategy, retention=retention)


@st.composite
def planner_inputs(draw: st.DrawFn) -> tuple[DataMap, SubjectGraph]:
    """A random subject-rooted tree with random strategies and ownership."""
    names = draw(st.lists(identifiers, min_size=1, max_size=8, unique=True))
    subject = names[0]
    chains: dict[str, tuple[JoinHop, ...]] = {subject: ()}
    for name in names[1:]:
        parent = draw(st.sampled_from(sorted(chains)))
        hop = JoinHop(
            source_table=name,
            source_columns=("pid",),
            target_table=parent,
            target_columns=("id",),
        )
        chains[name] = (hop, *chains[parent])
    entries: list[TableEntry] = []
    accesses: list[TableAccessPlan] = []
    for name in names:
        columns = draw(st.lists(identifiers, max_size=4, unique=True))
        entries.append(
            TableEntry(
                name=name,
                subject_link=SubjectLink(path="" if name == subject else "parent"),
                columns=tuple(
                    ColumnEntry(name=column, spec=_spec(draw(strategies))) for column in columns
                ),
            )
        )
        accesses.append(
            TableAccessPlan(table=name, hops=chains[name], fully_pii_owned=draw(st.booleans()))
        )
    accesses.sort(key=lambda access: len(access.hops), reverse=True)
    graph = SubjectGraph(subject_table=subject, subject_id_column="id", accesses=tuple(accesses))
    return DataMap(tables=tuple(entries)), graph


def _plan(data_map: DataMap, graph: SubjectGraph) -> ErasurePlan | None:
    """Plan, or ``None`` when the drawn declarations conflict (a valid outcome)."""
    try:
        return ErasurePlanner(data_map, graph).plan("42")
    except (RetentionViolationError, ManifestError):
        return None


@given(inputs=planner_inputs())
def test_retain_columns_never_in_delete_or_anonymize_steps(
    inputs: tuple[DataMap, SubjectGraph],
) -> None:
    data_map, graph = inputs
    plan = _plan(data_map, graph)
    assume(plan is not None)
    assert plan is not None
    retained = {
        (entry.name, column.name)
        for entry in data_map.tables
        for column in entry.columns
        if column.spec.erasure is ErasureStrategy.RETAIN
    }
    for step in plan.local_steps:
        if step.strategy is ErasureStrategy.RETAIN:
            continue
        assert all((step.target, column) not in retained for column in step.columns)
        if step.strategy is ErasureStrategy.DELETE:
            assert step.target not in {table for table, _ in retained}


@given(inputs=planner_inputs())
def test_local_targets_follow_deletion_order(inputs: tuple[DataMap, SubjectGraph]) -> None:
    data_map, graph = inputs
    plan = _plan(data_map, graph)
    assume(plan is not None)
    assert plan is not None
    targets = list(dict.fromkeys(step.target for step in plan.local_steps))
    order = iter(graph.deletion_order)
    assert all(target in order for target in targets)


@given(inputs=planner_inputs())
def test_plan_is_pure(inputs: tuple[DataMap, SubjectGraph]) -> None:
    data_map, graph = inputs
    assert _plan(data_map, graph) == _plan(data_map, graph)


@given(inputs=planner_inputs())
def test_row_deletion_requires_full_ownership_and_all_delete(
    inputs: tuple[DataMap, SubjectGraph],
) -> None:
    data_map, graph = inputs
    plan = _plan(data_map, graph)
    assume(plan is not None)
    assert plan is not None
    for step in plan.local_steps:
        if step.strategy is ErasureStrategy.DELETE:
            assert graph.access(step.target).fully_pii_owned
            assert all(
                column.spec.erasure is ErasureStrategy.DELETE
                for column in data_map.table(step.target).columns
            )


@given(
    inputs=planner_inputs(),
    resolver_names=st.lists(identifiers, max_size=3, unique=True),
)
def test_external_steps_trail_local_in_registration_order(
    inputs: tuple[DataMap, SubjectGraph],
    resolver_names: list[str],
) -> None:
    data_map, graph = inputs
    registry = ResolverRegistry()
    for name in resolver_names:
        registry.register(FakeResolver(name))
    try:
        plan = ErasurePlanner(data_map, graph, registry).plan("42")
    except (RetentionViolationError, ManifestError):
        assume(False)
        return
    assert [step.target for step in plan.external_steps] == resolver_names
    assert plan.steps == plan.local_steps + plan.external_steps
