"""Subject-link paths resolve against mappers and order FK-safely."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import MetaData
from sqlalchemy.orm import registry

from effaced import (
    DataMap,
    JoinHop,
    PiiCategory,
    PiiSpec,
    SubjectGraph,
    SubjectLink,
    SubjectResolutionError,
    TableAccessPlan,
    TableEntry,
    collect_data_map,
    fk_safe_deletion_order,
    resolve_subject_graph,
)
from effaced.manifest import ColumnEntry

USERS = TableEntry(name="users", subject_link=SubjectLink(path=""))


def hop(source: str, target: str, columns: tuple[str, str] = ("user_id", "id")) -> JoinHop:
    return JoinHop(
        source_table=source,
        source_columns=(columns[0],),
        target_table=target,
        target_columns=(columns[1],),
    )


# --- pure model validators ---


def test_join_hop_rejects_column_count_mismatch() -> None:
    with pytest.raises(ValidationError):
        JoinHop(
            source_table="invoices",
            source_columns=("user_id", "tenant_id"),
            target_table="users",
            target_columns=("id",),
        )


def test_access_plan_rejects_chain_not_starting_at_table() -> None:
    with pytest.raises(ValidationError):
        TableAccessPlan(table="invoices", hops=(hop("orders", "users"),))


def test_access_plan_rejects_broken_chain() -> None:
    with pytest.raises(ValidationError):
        TableAccessPlan(
            table="order_items",
            hops=(hop("order_items", "orders"), hop("invoices", "users")),
        )


def test_subject_graph_rejects_duplicate_tables() -> None:
    subject = TableAccessPlan(table="users")
    with pytest.raises(ValidationError):
        SubjectGraph(
            subject_table="users",
            subject_id_column="id",
            accesses=(subject, subject),
        )


def test_subject_graph_requires_subject_access() -> None:
    with pytest.raises(ValidationError):
        SubjectGraph(
            subject_table="users",
            subject_id_column="id",
            accesses=(TableAccessPlan(table="invoices", hops=(hop("invoices", "users"),)),),
        )


def test_subject_graph_rejects_chain_ending_elsewhere() -> None:
    with pytest.raises(ValidationError):
        SubjectGraph(
            subject_table="users",
            subject_id_column="id",
            accesses=(
                TableAccessPlan(table="users"),
                TableAccessPlan(table="order_items", hops=(hop("order_items", "orders"),)),
            ),
        )


def test_subject_graph_unknown_access_raises() -> None:
    graph = SubjectGraph(
        subject_table="users",
        subject_id_column="id",
        accesses=(TableAccessPlan(table="users"),),
    )
    with pytest.raises(SubjectResolutionError, match="not in the subject graph"):
        graph.access("ghosts")


# --- pure ordering ---


def test_deletion_order_puts_children_first() -> None:
    order = fk_safe_deletion_order(
        ("users", "orders", "order_items"),
        (("orders", "users"), ("order_items", "orders")),
    )
    assert order.index("order_items") < order.index("orders") < order.index("users")


def test_deletion_order_tolerates_self_loops() -> None:
    assert fk_safe_deletion_order(("comments",), (("comments", "comments"),)) == ("comments",)


def test_deletion_order_rejects_cycles() -> None:
    with pytest.raises(SubjectResolutionError, match="cycle"):
        fk_safe_deletion_order(("a", "b"), (("a", "b"), ("b", "a")))


def test_deletion_order_rejects_unknown_edge_endpoint() -> None:
    with pytest.raises(SubjectResolutionError, match="outside the graph"):
        fk_safe_deletion_order(("a",), (("a", "b"),))


# --- resolution against the shared schema ---


@pytest.fixture()
def graph(metadata: MetaData, orm_registry: registry) -> SubjectGraph:
    return resolve_subject_graph(collect_data_map(metadata), orm_registry)


def test_subject_table_resolves_with_no_hops(graph: SubjectGraph) -> None:
    assert graph.subject_table == "users"
    assert graph.subject_id_column == "id"
    assert graph.access("users").hops == ()
    assert graph.access("users").is_subject_table


def test_single_hop_path_resolves(graph: SubjectGraph) -> None:
    (single,) = graph.access("invoices").hops
    assert single.source_columns == ("user_id",)
    assert single.target_table == "users"
    assert single.target_columns == ("id",)


def test_multi_hop_path_resolves(graph: SubjectGraph) -> None:
    first, second = graph.access("order_items").hops
    assert (first.source_table, first.target_table) == ("order_items", "orders")
    assert first.source_columns == ("order_id",)
    assert (second.source_table, second.target_table) == ("orders", "users")
    assert second.source_columns == ("user_id",)


def test_self_referential_table_resolves(graph: SubjectGraph) -> None:
    (single,) = graph.access("comments").hops
    assert single.source_columns == ("user_id",)
    assert single.target_table == "users"


def test_deletion_order_is_fk_safe(graph: SubjectGraph) -> None:
    order = graph.deletion_order
    assert sorted(order) == sorted({"users", "invoices", "orders", "order_items", "comments"})
    assert order.index("order_items") < order.index("orders") < order.index("users")
    assert order.index("invoices") < order.index("users")
    assert order.index("comments") < order.index("users")


# --- loud failures ---


def test_missing_subject_table_raises(orm_registry: registry) -> None:
    data_map = DataMap(tables=(TableEntry(name="invoices", subject_link=SubjectLink(path="user")),))
    with pytest.raises(SubjectResolutionError, match="no subject table"):
        resolve_subject_graph(data_map, orm_registry)


def test_multiple_subject_tables_raise(orm_registry: registry) -> None:
    second = TableEntry(name="comments", subject_link=SubjectLink(path=""))
    with pytest.raises(SubjectResolutionError, match="multiple subject tables"):
        resolve_subject_graph(DataMap(tables=(USERS, second)), orm_registry)


def test_missing_link_raises(orm_registry: registry) -> None:
    unlinked = TableEntry(
        name="app_settings",
        columns=(ColumnEntry(name="value", spec=PiiSpec(category=PiiCategory.CONTACT)),),
    )
    with pytest.raises(SubjectResolutionError, match="declares no subject_link"):
        resolve_subject_graph(DataMap(tables=(USERS, unlinked)), orm_registry)


def test_unmapped_table_raises(orm_registry: registry) -> None:
    ghost = TableEntry(name="ghosts", subject_link=SubjectLink(path="user"))
    with pytest.raises(SubjectResolutionError, match="not mapped"):
        resolve_subject_graph(DataMap(tables=(USERS, ghost)), orm_registry)


def test_unknown_path_segment_raises(orm_registry: registry) -> None:
    bad = TableEntry(name="invoices", subject_link=SubjectLink(path="ghost"))
    with pytest.raises(SubjectResolutionError, match="not a relationship"):
        resolve_subject_graph(DataMap(tables=(USERS, bad)), orm_registry)


def test_path_not_reaching_subject_raises(orm_registry: registry) -> None:
    stops_short = TableEntry(name="order_items", subject_link=SubjectLink(path="order"))
    with pytest.raises(SubjectResolutionError, match="ends at"):
        resolve_subject_graph(DataMap(tables=(USERS, stops_short)), orm_registry)


def test_many_to_many_path_raises(orm_registry: registry) -> None:
    via_secondary = TableEntry(name="tags", subject_link=SubjectLink(path="users"))
    with pytest.raises(SubjectResolutionError, match="many-to-many"):
        resolve_subject_graph(DataMap(tables=(USERS, via_secondary)), orm_registry)


def test_subject_id_column_on_non_subject_table_raises(orm_registry: registry) -> None:
    misplaced = TableEntry(
        name="invoices", subject_link=SubjectLink(path="user", subject_id_column="uuid")
    )
    with pytest.raises(SubjectResolutionError, match="only meaningful on the subject table"):
        resolve_subject_graph(DataMap(tables=(USERS, misplaced)), orm_registry)


def test_unknown_subject_id_column_raises(orm_registry: registry) -> None:
    bad_subject = TableEntry(
        name="users", subject_link=SubjectLink(path="", subject_id_column="uuid")
    )
    with pytest.raises(SubjectResolutionError, match="has no column"):
        resolve_subject_graph(DataMap(tables=(bad_subject,)), orm_registry)
