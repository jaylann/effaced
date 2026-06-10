"""Property guarantees: every reachable row handled, no bleed, convergent reruns."""

from __future__ import annotations

from typing import NamedTuple

import pytest
from conftest import Base, Comment, Invoice, Order, OrderItem, RecordingAuditSink, User
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import ErasurePlanner, Outbox, bind_tables, collect_data_map, resolve_subject_graph
from effaced.adapters.sqlalchemy import ErasureExecutor

pytestmark = pytest.mark.property


class SubjectShape(NamedTuple):
    """How much data one subject owns."""

    invoices: int
    orders: int
    items_per_order: int
    comments: int


subject_shapes = st.builds(
    SubjectShape,
    invoices=st.integers(min_value=0, max_value=2),
    orders=st.integers(min_value=0, max_value=3),
    items_per_order=st.integers(min_value=0, max_value=2),
    comments=st.integers(min_value=0, max_value=3),
)


def seed_subject(session: Session, user_id: int, shape: SubjectShape, counter: int) -> None:
    """Create one subject's rows; comment chains stay within the subject."""
    session.add(User(id=user_id, email=f"u{user_id}@example.com", name=f"U{user_id}", theme="t"))
    session.flush()
    for index in range(shape.invoices):
        session.add(Invoice(id=counter + index, user_id=user_id, billing_address=f"addr-{user_id}"))
    order_ids = [counter + 10 + index for index in range(shape.orders)]
    session.add_all([Order(id=order_id, user_id=user_id) for order_id in order_ids])
    session.flush()
    item_id = counter + 40
    for order_id in order_ids:
        for _ in range(shape.items_per_order):
            session.add(OrderItem(id=item_id, order_id=order_id))
            item_id += 1
    parent: int | None = None
    for index in range(shape.comments):
        comment_id = counter + 80 + index
        session.add(Comment(id=comment_id, user_id=user_id, parent_id=parent))
        session.flush()
        parent = comment_id


def all_rows(session: Session, table_name: str) -> list[dict[str, object]]:
    table = Base.metadata.tables[table_name]
    return [dict(row) for row in session.execute(select(table).order_by(*table.c)).mappings()]


@given(shape_a=subject_shapes, shape_b=subject_shapes)
def test_erase_handles_every_reachable_row_and_never_bleeds(
    shape_a: SubjectShape, shape_b: SubjectShape
) -> None:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    effaced_metadata = MetaData()
    tables = bind_tables(effaced_metadata)
    Base.metadata.create_all(engine)
    effaced_metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    with session_factory() as session:
        seed_subject(session, 1, shape_a, counter=100)
        seed_subject(session, 2, shape_b, counter=500)
        session.commit()
        before_b = {
            name: [row for row in all_rows(session, name) if _belongs_to_b(session, name, row)]
            for name in ("users", "invoices", "orders", "order_items", "comments")
        }
    data_map = collect_data_map(Base.metadata)
    planner = ErasurePlanner(
        data_map,
        resolve_subject_graph(data_map, Base.registry),
        executor=ErasureExecutor(Base.metadata),
        outbox=Outbox(session_factory, tables.outbox),
        audit_sink=RecordingAuditSink(),
    )
    with session_factory() as session:
        result = planner.erase_subject(session, "1")
        session.commit()
    assert result.deleted == {
        "orders": shape_a.orders,
        "order_items": shape_a.orders * shape_a.items_per_order,
        "comments": shape_a.comments,
    }
    assert result.anonymized == {"users": 1}
    assert result.retained == {"invoices": shape_a.invoices}
    with session_factory() as session:
        # Acceptance (c): no row reachable via subject links for A is un-handled.
        assert all(row["user_id"] != 1 for row in all_rows(session, "orders"))
        assert all(row["user_id"] != 1 for row in all_rows(session, "comments"))
        a_order_ids = {100 + 10 + index for index in range(shape_a.orders)}
        assert all(row["order_id"] not in a_order_ids for row in all_rows(session, "order_items"))
        (ada,) = (row for row in all_rows(session, "users") if row["id"] == 1)
        assert ada["email"] != "u1@example.com"
        assert ada["name"] != "U1"
        a_invoices = [row for row in all_rows(session, "invoices") if row["user_id"] == 1]
        assert len(a_invoices) == shape_a.invoices
        assert all(row["billing_address"] == "addr-1" for row in a_invoices)
        # No cross-subject bleed: B's rows are byte-identical.
        for name, rows in before_b.items():
            after = [row for row in all_rows(session, name) if _belongs_to_b(session, name, row)]
            assert after == rows
    # Idempotent convergence: a rerun deletes nothing further.
    with session_factory() as session:
        rerun = planner.erase_subject(session, "1")
        session.commit()
    assert all(count == 0 for count in rerun.deleted.values())
    assert rerun.retained == {"invoices": shape_a.invoices}
    engine.dispose()


def _belongs_to_b(session: Session, table_name: str, row: dict[str, object]) -> bool:
    """Whether a row belongs to subject B (user id 2)."""
    if table_name == "users":
        return row["id"] == 2
    if table_name == "order_items":
        order = session.get(Order, row["order_id"])
        return order is not None and order.user_id == 2
    return row["user_id"] == 2
