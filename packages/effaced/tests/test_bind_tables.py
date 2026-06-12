"""``bind_tables`` mounts the effaced-owned tables with stable, portable DDL."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import Column, Integer, MetaData, String, Table
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateTable

from effaced import AuditEvent, AuditEventType, EffacedTables, OutboxStatus, bind_tables
from effaced.adapters.sqlalchemy.storage.bind_tables import (
    AUDIT_EVENTS_TABLE_NAME,
    CONSENT_RECORDS_TABLE_NAME,
    OUTBOX_TABLE_NAME,
    RESTRICTION_RECORDS_TABLE_NAME,
)

ALL_TABLE_NAMES = (
    AUDIT_EVENTS_TABLE_NAME,
    CONSENT_RECORDS_TABLE_NAME,
    OUTBOX_TABLE_NAME,
    RESTRICTION_RECORDS_TABLE_NAME,
)

EXPECTED_INDEX_NAMES = {
    AUDIT_EVENTS_TABLE_NAME: {"ix_effaced_audit_events_subject_ref_occurred_at"},
    CONSENT_RECORDS_TABLE_NAME: {"ix_effaced_consent_records_subject_purpose_recorded_at"},
    OUTBOX_TABLE_NAME: {
        "ix_effaced_outbox_status_enqueued_at",
        "ix_effaced_outbox_subject_id",
    },
    RESTRICTION_RECORDS_TABLE_NAME: {"ix_effaced_restriction_records_subject_purpose_recorded_at"},
}


def test_bind_tables_mounts_all_four_tables() -> None:
    metadata = MetaData()
    bind_tables(metadata)
    for name in ALL_TABLE_NAMES:
        assert name in metadata.tables
        assert name.startswith("effaced_")
    assert len([n for n in metadata.tables if n.startswith("effaced_")]) == 4


def test_bind_tables_returns_table_handles() -> None:
    metadata = MetaData()
    tables = bind_tables(metadata)
    assert isinstance(tables, EffacedTables)
    assert tables.audit_events is metadata.tables[AUDIT_EVENTS_TABLE_NAME]
    assert tables.consent_records is metadata.tables[CONSENT_RECORDS_TABLE_NAME]
    assert tables.outbox is metadata.tables[OUTBOX_TABLE_NAME]
    assert tables.restriction_records is metadata.tables[RESTRICTION_RECORDS_TABLE_NAME]


def test_bind_tables_is_idempotent() -> None:
    metadata = MetaData()
    first = bind_tables(metadata)
    second = bind_tables(metadata)
    assert second.audit_events is first.audit_events
    assert second.consent_records is first.consent_records
    assert second.outbox is first.outbox
    assert second.restriction_records is first.restriction_records


def test_bind_tables_rejects_partial_collision() -> None:
    metadata = MetaData()
    Table(OUTBOX_TABLE_NAME, metadata, Column("id", Integer, primary_key=True))
    with pytest.raises(ValueError, match=OUTBOX_TABLE_NAME):
        bind_tables(metadata)


def test_audit_events_columns_mirror_model() -> None:
    table = bind_tables(MetaData()).audit_events
    assert {c.name for c in table.columns} == {
        "event_id",
        "event_type",
        "subject_ref",
        "occurred_at",
        "payload",
    }
    assert [c.name for c in table.primary_key.columns] == ["event_id"]
    for name in ("event_type", "subject_ref", "occurred_at", "payload"):
        assert table.columns[name].nullable is False
    assert table.columns["occurred_at"].type.timezone  # type: ignore[attr-defined]


def test_consent_records_has_surrogate_uuid_pk_and_no_unique_constraint() -> None:
    table = bind_tables(MetaData()).consent_records
    assert [c.name for c in table.primary_key.columns] == ["record_id"]
    assert table.columns["record_id"].default is not None
    assert table.columns["source"].nullable is True
    assert not any(index.unique for index in table.indexes)


def test_restriction_records_has_surrogate_uuid_pk_and_nullable_scope_fields() -> None:
    table = bind_tables(MetaData()).restriction_records
    assert [c.name for c in table.primary_key.columns] == ["record_id"]
    assert table.columns["record_id"].default is not None
    assert table.columns["purpose"].nullable is True
    assert table.columns["reason"].nullable is True
    assert table.columns["source"].nullable is True
    assert table.columns["restricted"].nullable is False
    assert not any(index.unique for index in table.indexes)


def test_outbox_flattens_subject_ref() -> None:
    table = bind_tables(MetaData()).outbox
    assert {"ref_kind", "ref_value", "ref_extra"} <= {c.name for c in table.columns}
    assert table.columns["status"].default is not None
    assert table.columns["status"].default.arg == "pending"  # type: ignore[union-attr]
    assert table.columns["attempts"].default is not None
    assert table.columns["attempts"].default.arg == 0  # type: ignore[union-attr]
    assert table.columns["subject_id"].nullable is False
    assert table.columns["last_attempt_at"].nullable is True
    assert table.columns["next_attempt_at"].nullable is True
    assert table.columns["last_error"].nullable is True


def test_postgresql_ddl_uses_jsonb_uuid_timestamptz() -> None:
    metadata = MetaData()
    tables = bind_tables(metadata)
    dialect = postgresql.dialect()
    all_tables = (
        tables.audit_events,
        tables.consent_records,
        tables.outbox,
        tables.restriction_records,
    )
    for table in all_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert "UUID" in ddl
        assert "TIMESTAMP WITH TIME ZONE" in ddl
    assert "JSONB" in str(CreateTable(tables.audit_events).compile(dialect=dialect))
    assert "JSONB" in str(CreateTable(tables.outbox).compile(dialect=dialect))


def test_generic_ddl_falls_back_to_json() -> None:
    tables = bind_tables(MetaData())
    ddl = str(CreateTable(tables.audit_events).compile(dialect=sqlite.dialect()))
    assert "JSONB" not in ddl
    assert "JSON" in ddl


def test_index_names_are_stable_and_within_pg_limit() -> None:
    convention = {"ix": "ix_%(column_0_label)s"}
    for metadata in (MetaData(), MetaData(naming_convention=convention)):
        tables = bind_tables(metadata)
        all_tables = (
            tables.audit_events,
            tables.consent_records,
            tables.outbox,
            tables.restriction_records,
        )
        for table in all_tables:
            names = {index.name for index in table.indexes}
            assert names == EXPECTED_INDEX_NAMES[table.name]
            assert all(len(name) <= 63 for name in names if name)


def test_no_server_defaults_except_the_outbox_operation_migration_aid() -> None:
    """Defaults stay python-side — except ``operation``, whose server default
    is what makes the additive ALTER backfill populated outboxes (ADR 0013)."""
    tables = bind_tables(MetaData())
    all_tables = (
        tables.audit_events,
        tables.consent_records,
        tables.outbox,
        tables.restriction_records,
    )
    for table in all_tables:
        for column in table.columns:
            if (table.name, column.name) == (OUTBOX_TABLE_NAME, "operation"):
                assert column.server_default is not None
                continue
            assert column.server_default is None, f"{table.name}.{column.name}"


def test_enum_values_fit_their_columns() -> None:
    tables = bind_tables(MetaData())
    event_type = tables.audit_events.columns["event_type"].type
    status = tables.outbox.columns["status"].type
    assert isinstance(event_type, String) and event_type.length is not None
    assert isinstance(status, String) and status.length is not None
    assert all(len(member.value) <= event_type.length for member in AuditEventType)
    assert all(len(member.value) <= status.length for member in OutboxStatus)


def test_model_bounds_match_bounded_string_columns() -> None:
    """Identifiers the models accept always fit the DDL: 255 chars is the shared cap."""
    table = bind_tables(MetaData()).audit_events
    subject_ref_length = table.columns["subject_ref"].type.length  # type: ignore[attr-defined]
    assert subject_ref_length == 255
    AuditEvent(
        event_id=uuid4(),
        event_type=AuditEventType.EXPORT_REQUESTED,
        subject_ref="s" * 255,
        occurred_at=datetime.now(UTC),
    )
    with pytest.raises(ValidationError):
        AuditEvent(
            event_id=uuid4(),
            event_type=AuditEventType.EXPORT_REQUESTED,
            subject_ref="s" * 256,
            occurred_at=datetime.now(UTC),
        )
