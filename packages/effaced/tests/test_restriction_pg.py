"""Restriction ledger + database audit sink wired together against a real Postgres."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, MetaData
from sqlalchemy.orm import sessionmaker

from effaced import (
    AuditEventType,
    DatabaseAuditSink,
    RestrictionLedger,
    RestrictionRecord,
    bind_tables,
)

pytestmark = pytest.mark.integration


def test_restriction_flow_round_trips_on_postgres(pg_engine: Engine) -> None:
    """Place then lift with the real sink committing inside an open caller transaction."""
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(pg_engine)
    try:
        session_factory = sessionmaker(pg_engine)
        sink = DatabaseAuditSink(session_factory, tables.audit_events)
        ledger = RestrictionLedger(tables.restriction_records, sink)
        placed_at = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        placement = RestrictionRecord(
            subject_id="subject-1",
            purpose="ads",
            restricted=True,
            reason="accuracy disputed",
            recorded_at=placed_at,
            source="dsar-portal",
        )
        lift = placement.model_copy(
            update={
                "restricted": False,
                "reason": "dispute settled",
                "recorded_at": placed_at + timedelta(hours=1),
            }
        )
        with session_factory() as session:
            ledger.record(session, placement)
            ledger.record(session, lift)
            session.commit()

        with session_factory() as session:
            assert ledger.status(session, "subject-1", "ads") is False
            assert ledger.status(session, "subject-1") is False
            history = ledger.history(session, "subject-1")
        assert history == (placement, lift)
        assert all(record.recorded_at.tzinfo is not None for record in history)

        events = sink.read("subject-1")
        assert [event.event_type for event in events] == [
            AuditEventType.RESTRICTION_PLACED,
            AuditEventType.RESTRICTION_LIFTED,
        ]
        assert all(event.payload == {"purpose": "ads"} for event in events)
        assert all(event.occurred_at.tzinfo is not None for event in events)
    finally:
        metadata.drop_all(pg_engine)
