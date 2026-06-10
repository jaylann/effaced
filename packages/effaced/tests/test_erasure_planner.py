"""ErasurePlanner.plan() — inspectable, FK-safe, retention-preserving plans.

The golden tests run against the shared conftest schema. Conflict and
mixed-strategy cases hand-build pure ``DataMap``/``SubjectGraph`` models
instead — ``plan()`` is a pure function of those models, and the shared
schema must stay conflict-free for the golden plan.
"""

from __future__ import annotations

import pytest
from sqlalchemy import MetaData
from sqlalchemy.orm import registry as orm_registry_type

from effaced import (
    DataMap,
    ErasurePlanner,
    ErasureStep,
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
    collect_data_map,
    resolve_subject_graph,
)
from effaced.manifest import ColumnEntry


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


@pytest.fixture()
def planner(metadata: MetaData, orm_registry: orm_registry_type) -> ErasurePlanner:
    data_map = collect_data_map(metadata)
    return ErasurePlanner(data_map, resolve_subject_graph(data_map, orm_registry))


def test_golden_plan_for_shared_schema(planner: ErasurePlanner) -> None:
    plan = planner.plan("42")
    assert plan.subject_id == "42"
    assert plan.refs == ()
    assert plan.steps == (
        ErasureStep(target="comments", strategy=ErasureStrategy.DELETE),
        ErasureStep(
            target="invoices",
            strategy=ErasureStrategy.RETAIN,
            columns=("billing_address",),
        ),
        ErasureStep(target="order_items", strategy=ErasureStrategy.DELETE),
        ErasureStep(target="orders", strategy=ErasureStrategy.DELETE),
        ErasureStep(
            target="users",
            strategy=ErasureStrategy.ANONYMIZE,
            columns=("email", "name"),
        ),
    )


def test_retain_columns_never_in_delete_or_anonymize_steps(planner: ErasurePlanner) -> None:
    plan = planner.plan("42")
    for step in plan.local_steps:
        if step.strategy is not ErasureStrategy.RETAIN:
            assert "billing_address" not in step.columns
            assert not (step.target == "invoices" and step.strategy is ErasureStrategy.DELETE)


def test_plan_is_pure(planner: ErasurePlanner) -> None:
    assert planner.plan("42") == planner.plan("42")


def test_plan_shape_is_subject_independent(planner: ErasurePlanner) -> None:
    assert planner.plan("42").steps == planner.plan("43").steps


def test_refs_are_recorded_on_the_plan(planner: ErasurePlanner) -> None:
    refs = (SubjectRef(kind="stripe", value="cus_1"),)
    assert planner.plan("42", refs=refs).refs == refs


def test_external_steps_follow_local_in_registration_order(
    metadata: MetaData, orm_registry: orm_registry_type
) -> None:
    data_map = collect_data_map(metadata)
    graph = resolve_subject_graph(data_map, orm_registry)
    registry = ResolverRegistry()
    registry.register(FakeResolver("crm"))
    registry.register(FakeResolver("stripe"))
    plan = ErasurePlanner(data_map, graph, registry).plan("42")
    assert plan.steps[-2:] == plan.external_steps
    assert [step.target for step in plan.external_steps] == ["crm", "stripe"]
    for step in plan.external_steps:
        assert step.strategy is ErasureStrategy.DELETE
        assert step.columns == ()


# --- hand-built pure-model cases ------------------------------------------


def _spec(strategy: ErasureStrategy) -> PiiSpec:
    retention = RetentionPolicy(reason="test duty") if strategy is ErasureStrategy.RETAIN else None
    return PiiSpec(category=PiiCategory.CONTACT, erasure=strategy, retention=retention)


def _entry(name: str, path: str, **columns: ErasureStrategy) -> TableEntry:
    return TableEntry(
        name=name,
        subject_link=SubjectLink(path=path),
        columns=tuple(
            ColumnEntry(name=column, spec=_spec(strategy)) for column, strategy in columns.items()
        ),
    )


def _hop(source: str, target: str) -> JoinHop:
    return JoinHop(
        source_table=source,
        source_columns=(f"{target}_id",),
        target_table=target,
        target_columns=("id",),
    )


def test_mixed_strategy_table_emits_anonymize_then_retain() -> None:
    data_map = DataMap(
        tables=(
            _entry(
                "people",
                "",
                email=ErasureStrategy.DELETE,
                nickname=ErasureStrategy.ANONYMIZE,
                ledger=ErasureStrategy.RETAIN,
            ),
        )
    )
    graph = SubjectGraph(
        subject_table="people",
        subject_id_column="id",
        accesses=(TableAccessPlan(table="people", fully_pii_owned=True),),
    )
    plan = ErasurePlanner(data_map, graph).plan("42")
    assert plan.steps == (
        ErasureStep(
            target="people",
            strategy=ErasureStrategy.ANONYMIZE,
            columns=("email", "nickname"),
        ),
        ErasureStep(target="people", strategy=ErasureStrategy.RETAIN, columns=("ledger",)),
    )


def test_retained_child_under_deleted_subject_raises_retention_violation() -> None:
    data_map = DataMap(
        tables=(
            _entry("people", "", email=ErasureStrategy.DELETE),
            _entry("contracts", "person", terms=ErasureStrategy.RETAIN),
        )
    )
    graph = SubjectGraph(
        subject_table="people",
        subject_id_column="id",
        accesses=(
            TableAccessPlan(
                table="contracts",
                hops=(_hop("contracts", "people"),),
                fully_pii_owned=True,
            ),
            TableAccessPlan(table="people", fully_pii_owned=True),
        ),
    )
    planner = ErasurePlanner(data_map, graph)
    with pytest.raises(RetentionViolationError, match=r"contracts.*people.*row deletion"):
        planner.plan("42")


def test_unerasable_survivor_under_deleted_parent_raises_manifest_error() -> None:
    data_map = DataMap(
        tables=(
            _entry("people", "", email=ErasureStrategy.DELETE),
            _entry("orders", "person"),
            _entry("shipments", "order.person"),
        )
    )
    graph = SubjectGraph(
        subject_table="people",
        subject_id_column="id",
        accesses=(
            TableAccessPlan(
                table="shipments",
                hops=(_hop("shipments", "orders"), _hop("orders", "people")),
                fully_pii_owned=False,
            ),
            TableAccessPlan(table="orders", hops=(_hop("orders", "people"),), fully_pii_owned=True),
            TableAccessPlan(table="people", fully_pii_owned=True),
        ),
    )
    planner = ErasurePlanner(data_map, graph)
    with pytest.raises(ManifestError, match=r"shipments.*orders.*row deletion"):
        planner.plan("42")


def test_unannotated_not_fully_owned_table_emits_no_step() -> None:
    data_map = DataMap(
        tables=(
            _entry("people", "", email=ErasureStrategy.DELETE),
            _entry("logs", "person"),
        )
    )
    graph = SubjectGraph(
        subject_table="people",
        subject_id_column="id",
        accesses=(
            TableAccessPlan(table="logs", hops=(_hop("logs", "people"),), fully_pii_owned=False),
            TableAccessPlan(table="people", fully_pii_owned=False),
        ),
    )
    plan = ErasurePlanner(data_map, graph).plan("42")
    assert plan.steps == (
        ErasureStep(target="people", strategy=ErasureStrategy.ANONYMIZE, columns=("email",)),
    )


def test_retention_conflict_names_the_retention_reason() -> None:
    """The refusal cites the legal reason the retained column declares."""
    data_map = DataMap(
        tables=(
            _entry("people", "", email=ErasureStrategy.DELETE),
            _entry("contracts", "person", terms=ErasureStrategy.RETAIN),
        )
    )
    graph = SubjectGraph(
        subject_table="people",
        subject_id_column="id",
        accesses=(
            TableAccessPlan(
                table="contracts",
                hops=(_hop("contracts", "people"),),
                fully_pii_owned=True,
            ),
            TableAccessPlan(table="people", fully_pii_owned=True),
        ),
    )
    with pytest.raises(RetentionViolationError, match="test duty"):
        ErasurePlanner(data_map, graph).plan("42")


def test_conflict_detection_scans_past_conflict_free_tables() -> None:
    """A clean first table must not end conflict checking for later tables."""
    data_map = DataMap(
        tables=(
            _entry("people", "", email=ErasureStrategy.ANONYMIZE),
            _entry("orders", "person", note=ErasureStrategy.DELETE),
            _entry("contracts", "order.person", terms=ErasureStrategy.RETAIN),
        )
    )
    graph = SubjectGraph(
        subject_table="people",
        subject_id_column="id",
        accesses=(
            TableAccessPlan(table="people", fully_pii_owned=False),
            TableAccessPlan(table="orders", hops=(_hop("orders", "people"),), fully_pii_owned=True),
            TableAccessPlan(
                table="contracts",
                hops=(_hop("contracts", "orders"), _hop("orders", "people")),
                fully_pii_owned=True,
            ),
        ),
    )
    with pytest.raises(RetentionViolationError, match=r"contracts.*orders"):
        ErasurePlanner(data_map, graph).plan("42")


def test_mismatched_graph_and_data_map_raise_at_construction() -> None:
    data_map = DataMap(tables=(_entry("people", "", email=ErasureStrategy.DELETE),))
    graph = SubjectGraph(
        subject_table="people",
        subject_id_column="id",
        accesses=(
            TableAccessPlan(table="logs", hops=(_hop("logs", "people"),)),
            TableAccessPlan(table="people"),
        ),
    )
    with pytest.raises(ManifestError, match="disagree"):
        ErasurePlanner(data_map, graph)
