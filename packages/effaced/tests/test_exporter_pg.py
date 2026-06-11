"""Exporter wired to a real Postgres — strict typing, real audit sink."""

from __future__ import annotations

import pytest
from conftest import Base, seed_two_subjects
from sqlalchemy import Engine, MetaData
from sqlalchemy.orm import sessionmaker

from effaced import (
    AuditEventType,
    DatabaseAuditSink,
    Exporter,
    bind_tables,
    collect_data_map,
    resolve_subject_graph,
)

pytestmark = pytest.mark.integration


def test_export_round_trips_on_postgres(pg_engine: Engine) -> None:
    """The string subject id reaches Postgres typed; the sink commits independently."""
    effaced_metadata = MetaData()
    tables = bind_tables(effaced_metadata)
    Base.metadata.create_all(pg_engine)
    effaced_metadata.create_all(pg_engine)
    try:
        session_factory = sessionmaker(pg_engine)
        with session_factory() as session:
            seed_two_subjects(session)
        data_map = collect_data_map(Base.metadata)
        graph = resolve_subject_graph(data_map, Base.registry)
        sink = DatabaseAuditSink(session_factory, tables.audit_events)
        exporter = Exporter(data_map, graph, Base.metadata, sink)
        with session_factory() as session:
            bundle = exporter.export_subject(session, "1")
        assert {(record.source, record.field) for record in bundle.records} == {
            ("users", "email"),
            ("users", "name"),
            ("invoices", "billing_address"),
            ("order_items", "gift_message"),
        }
        assert all("bob" not in str(record.value) for record in bundle.records)
        events = sink.read("1")
        assert [event.event_type for event in events] == [
            AuditEventType.EXPORT_REQUESTED,
            AuditEventType.EXPORT_COMPLETED,
        ]
        assert events[1].payload["record_count"] == 4
    finally:
        Base.metadata.drop_all(pg_engine)
        effaced_metadata.drop_all(pg_engine)
