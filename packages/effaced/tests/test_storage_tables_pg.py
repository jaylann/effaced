"""The effaced-owned tables round-trip against a real Postgres."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import Engine, MetaData, inspect, select
from sqlalchemy.dialects.postgresql import JSONB

from effaced import AuditEventType, OutboxStatus, bind_tables

pytestmark = pytest.mark.integration


def test_create_all_round_trips_all_three_tables(pg_engine: Engine) -> None:
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(pg_engine)
    try:
        event_id, entry_id = uuid4(), uuid4()
        now = datetime.now(UTC)
        with pg_engine.begin() as conn:
            conn.execute(
                tables.audit_events.insert(),
                {
                    "event_id": event_id,
                    "event_type": AuditEventType.CONSENT_GRANTED.value,
                    "subject_ref": "subject-1",
                    "occurred_at": now,
                    "payload": {"tables": 3, "dry_run": False, "version": "1"},
                },
            )
            conn.execute(
                tables.consent_records.insert(),
                {
                    "subject_id": "subject-1",
                    "purpose": "newsletter",
                    "policy_version": "2026-01",
                    "granted": True,
                    "recorded_at": now,
                    "source": None,
                },
            )
            conn.execute(
                tables.outbox.insert(),
                {
                    "entry_id": entry_id,
                    "subject_id": "subject-1",
                    "resolver": "stripe",
                    "ref_kind": "customer",
                    "ref_value": "cus_123",
                    "ref_extra": {"account": "acct_1"},
                    "enqueued_at": now,
                },
            )

        with pg_engine.connect() as conn:
            event = conn.execute(select(tables.audit_events)).one()
            assert event.event_id == event_id
            assert event.event_type == "consent_granted"
            assert event.occurred_at == now
            assert event.occurred_at.tzinfo is not None
            assert event.payload == {"tables": 3, "dry_run": False, "version": "1"}

            record = conn.execute(select(tables.consent_records)).one()
            assert record.record_id is not None  # client-side uuid4 default fired
            assert (record.subject_id, record.purpose, record.granted) == (
                "subject-1",
                "newsletter",
                True,
            )
            assert record.recorded_at == now
            assert record.source is None

            entry = conn.execute(select(tables.outbox)).one()
            assert entry.entry_id == entry_id
            assert entry.subject_id == "subject-1"
            assert entry.ref_extra == {"account": "acct_1"}
            assert entry.status == OutboxStatus.PENDING.value  # python-side default fired
            assert entry.attempts == 0
            assert entry.last_attempt_at is None
            assert entry.next_attempt_at is None
            assert entry.last_error is None
    finally:
        metadata.drop_all(pg_engine)


def test_postgres_schema_has_expected_indexes_and_jsonb(pg_engine: Engine) -> None:
    metadata = MetaData()
    bind_tables(metadata)
    metadata.create_all(pg_engine)
    try:
        inspector = inspect(pg_engine)
        expected = {
            "effaced_audit_events": "ix_effaced_audit_events_subject_ref_occurred_at",
            "effaced_consent_records": "ix_effaced_consent_records_subject_purpose_recorded_at",
            "effaced_outbox": "ix_effaced_outbox_status_enqueued_at",
        }
        for table_name, index_name in expected.items():
            names = {index["name"] for index in inspector.get_indexes(table_name)}
            assert index_name in names

        for table_name, column_name in (
            ("effaced_audit_events", "payload"),
            ("effaced_outbox", "ref_extra"),
        ):
            columns = {c["name"]: c for c in inspector.get_columns(table_name)}
            assert isinstance(columns[column_name]["type"], JSONB)
    finally:
        metadata.drop_all(pg_engine)
