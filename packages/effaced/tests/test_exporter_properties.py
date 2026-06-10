"""Property-based guarantee: an export never bleeds across subjects."""

from __future__ import annotations

import pytest
from conftest import (
    Base,
    Comment,
    Invoice,
    Order,
    OrderItem,
    RecordingAuditSink,
    User,
)
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import Exporter, collect_data_map, resolve_subject_graph

pytestmark = pytest.mark.property

populations = st.lists(
    st.tuples(
        st.integers(min_value=0, max_value=3),  # invoices per subject
        st.integers(min_value=0, max_value=3),  # order items per subject
        st.integers(min_value=0, max_value=2),  # comments per subject
    ),
    min_size=2,
    max_size=5,
)


@given(population=populations, data=st.data())
def test_export_never_contains_other_subjects_rows(
    population: list[tuple[int, int, int]],
    data: st.DataObject,
) -> None:
    """Every exported value carries the exported subject's sentinel only."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    with session_factory() as session:
        _seed(session, population)
    data_map = collect_data_map(Base.metadata)
    graph = resolve_subject_graph(data_map, Base.registry)
    exporter = Exporter(data_map, graph, Base.metadata, RecordingAuditSink())
    subject = data.draw(st.integers(min_value=1, max_value=len(population)))
    with session_factory() as session:
        bundle = exporter.export_subject(session, str(subject))
    own, others = (
        f"<s{subject}>",
        {f"<s{i + 1}>" for i in range(len(population))} - {f"<s{subject}>"},
    )
    invoices, items, comments = population[subject - 1]
    assert len(bundle.records) == 2 + invoices + items
    assert comments >= 0  # comments carry no annotated columns, never exported
    for record in bundle.records:
        value = str(record.value)
        assert own in value
        assert not any(other in value for other in others)
    engine.dispose()


def _seed(session: Session, population: list[tuple[int, int, int]]) -> None:
    """One user per population entry, each row value tagged with its owner."""
    add = session.add
    next_id = iter(range(1, 10_000)).__next__
    for index, (invoices, items, comments) in enumerate(population):
        subject = index + 1
        tag = f"<s{subject}>"
        add(User(id=subject, email=f"{tag} email", name=f"{tag} name", theme="dark"))
        for _ in range(invoices):
            add(Invoice(id=next_id(), user_id=subject, billing_address=f"{tag} address"))
        order = Order(id=next_id(), user_id=subject)
        add(order)
        for _ in range(items):
            add(OrderItem(id=next_id(), order_id=order.id, gift_message=f"{tag} gift"))
        for _ in range(comments):
            add(Comment(id=next_id(), user_id=subject))
    session.commit()
