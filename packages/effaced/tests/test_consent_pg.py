"""Consent ledger + database audit sink wired together against a real Postgres."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import Engine, MetaData
from sqlalchemy.orm import sessionmaker

from effaced import (
    AuditEventType,
    ConsentLedger,
    ConsentRecord,
    DatabaseAuditSink,
    bind_tables,
)

pytestmark = pytest.mark.integration


def test_consent_flow_round_trips_on_postgres(pg_engine: Engine) -> None:
    """Grant then withdraw with the real sink committing inside an open caller transaction."""
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(pg_engine)
    try:
        session_factory = sessionmaker(pg_engine)
        sink = DatabaseAuditSink(session_factory, tables.audit_events)
        ledger = ConsentLedger(tables.consent_records, sink)
        granted_at = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        grant = ConsentRecord(
            subject_id="subject-1",
            purpose="newsletter",
            policy_version="2026-06",
            granted=True,
            recorded_at=granted_at,
            source="signup-form",
        )
        withdrawal = grant.model_copy(
            update={
                "granted": False,
                "recorded_at": granted_at + timedelta(hours=1),
                "source": "preferences-page",
            }
        )
        with session_factory() as session:
            ledger.record(session, grant)
            ledger.record(session, withdrawal)
            session.commit()

        with session_factory() as session:
            assert ledger.status(session, "subject-1", "newsletter") is False
            history = ledger.history(session, "subject-1")
        assert history == (grant, withdrawal)
        assert all(record.recorded_at.tzinfo is not None for record in history)

        events = sink.read("subject-1")
        assert [event.event_type for event in events] == [
            AuditEventType.CONSENT_GRANTED,
            AuditEventType.CONSENT_WITHDRAWN,
        ]
        assert all(
            event.payload == {"purpose": "newsletter", "policy_version": "2026-06"}
            for event in events
        )
        assert all(event.occurred_at.tzinfo is not None for event in events)
    finally:
        metadata.drop_all(pg_engine)


def test_audit_event_survives_caller_rollback(pg_engine: Engine) -> None:
    """The mirrored event persists even when the consent write is rolled back."""
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(pg_engine)
    try:
        session_factory = sessionmaker(pg_engine)
        sink = DatabaseAuditSink(session_factory, tables.audit_events)
        ledger = ConsentLedger(tables.consent_records, sink)
        record = ConsentRecord(
            subject_id="subject-1",
            purpose="newsletter",
            policy_version="2026-06",
            granted=True,
            recorded_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
        )
        with session_factory() as session:
            ledger.record(session, record)
            session.rollback()

        with session_factory() as session:
            assert ledger.status(session, "subject-1", "newsletter") is False
            assert ledger.history(session, "subject-1") == ()
        events = sink.read("subject-1")
        assert [event.event_type for event in events] == [AuditEventType.CONSENT_GRANTED]
    finally:
        metadata.drop_all(pg_engine)


def test_concurrent_grant_withdraw_last_write_wins(pg_engine: Engine) -> None:
    """Overlapping writers converge on the record with the greatest recorded_at."""
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(pg_engine)
    try:
        session_factory = sessionmaker(pg_engine)
        sink = DatabaseAuditSink(session_factory, tables.audit_events)
        ledger = ConsentLedger(tables.consent_records, sink)
        base = datetime(2026, 6, 1, tzinfo=UTC)
        records = [
            ConsentRecord(
                subject_id="subject-1",
                purpose="newsletter",
                policy_version="2026-06",
                granted=index % 2 == 0,
                recorded_at=base + timedelta(milliseconds=index),
            )
            for index in range(6)
        ]
        barrier = Barrier(len(records))

        def write(record: ConsentRecord) -> None:
            with session_factory() as session:
                barrier.wait(timeout=30)
                ledger.record(session, record)
                session.commit()

        with ThreadPoolExecutor(max_workers=len(records)) as pool:
            list(pool.map(write, records))  # re-raises any worker failure

        with session_factory() as session:
            assert ledger.status(session, "subject-1", "newsletter") is records[-1].granted
            assert ledger.history(session, "subject-1") == tuple(records)

        events = sink.read("subject-1")
        assert len(events) == len(records)
        granted = sum(event.event_type is AuditEventType.CONSENT_GRANTED for event in events)
        assert granted == 3
    finally:
        metadata.drop_all(pg_engine)
