"""The owned tables ride a host Alembic setup cleanly (ADR 0018).

Three proofs against a real Postgres: autogenerate discovers the owned
tables like first-party models, a re-run after applying is diff-free (no
perpetual autogenerate noise), and an additive column carrying a server
default backfills an already-populated table — the ``effaced_outbox.operation``
precedent (ADR 0013), generalized.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, Connection, Engine, MetaData, select, text

from effaced import bind_tables
from effaced.adapters.sqlalchemy.storage.bind_tables import (
    AUDIT_EVENTS_TABLE_NAME,
    CONSENT_RECORDS_TABLE_NAME,
    OUTBOX_TABLE_NAME,
    RESTRICTION_RECORDS_TABLE_NAME,
)

pytestmark = pytest.mark.integration

OWNED_TABLE_NAMES = frozenset(
    {
        AUDIT_EVENTS_TABLE_NAME,
        CONSENT_RECORDS_TABLE_NAME,
        OUTBOX_TABLE_NAME,
        RESTRICTION_RECORDS_TABLE_NAME,
    }
)

EXPECTED_INDEX_NAMES = {
    "ix_effaced_audit_events_subject_ref_occurred_at",
    "ix_effaced_consent_records_subject_purpose_recorded_at",
    "ix_effaced_outbox_status_enqueued_at",
    "ix_effaced_outbox_subject_id",
    "ix_effaced_restriction_records_subject_purpose_recorded_at",
}


def _migration_ctx(conn: Connection, **extra: object) -> MigrationContext:
    """A context whose reflection sees only the owned tables.

    The integration database is shared by the whole suite, so unrelated
    leftovers must not surface as ``remove_table`` noise. The hook is the
    inverse of the host-app opt-out documented in the Alembic guide.
    """
    opts: dict[str, object] = {
        "include_name": (
            lambda name, type_, _parent: type_ != "table" or name in OWNED_TABLE_NAMES
        ),
    }
    opts.update(extra)
    return MigrationContext.configure(conn, opts=opts)


def test_autogenerate_discovers_all_owned_tables(pg_engine: Engine) -> None:
    metadata = MetaData()
    bind_tables(metadata)
    with pg_engine.connect() as conn:
        diffs = compare_metadata(_migration_ctx(conn), metadata)

    added_tables = {diff[1].name for diff in diffs if diff[0] == "add_table"}
    added_indexes = {diff[1].name for diff in diffs if diff[0] == "add_index"}
    assert added_tables == set(OWNED_TABLE_NAMES)
    assert added_indexes == EXPECTED_INDEX_NAMES
    assert {diff[0] for diff in diffs} == {"add_table", "add_index"}


def test_owned_tables_autogenerate_clean_after_create_all(pg_engine: Engine) -> None:
    """No perpetual diff: a second autogenerate after applying is empty.

    Asserted under ``compare_type`` and ``compare_server_default`` too — the
    ``operation`` server default (``'erase'`` vs the reflected
    ``'erase'::character varying``) and the JSONB/UUID variants must all
    round-trip without spurious ops.
    """
    metadata = MetaData()
    bind_tables(metadata)
    metadata.create_all(pg_engine)
    try:
        with pg_engine.connect() as conn:
            assert compare_metadata(_migration_ctx(conn), metadata) == []
            strict_ctx = _migration_ctx(conn, compare_type=True, compare_server_default=True)
            assert compare_metadata(strict_ctx, metadata) == []
    finally:
        metadata.drop_all(pg_engine)


def test_additive_column_with_server_default_backfills_populated_outbox(
    pg_engine: Engine,
) -> None:
    """The generalized ADR 0013 recipe, end to end.

    Simulate a pre-``operation`` deployment (drop the column, leave rows
    behind), let autogenerate propose exactly the missing column, apply it
    the way a rendered revision would, and watch the server default backfill
    every existing row.
    """
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(pg_engine)
    try:
        with pg_engine.begin() as conn:
            conn.execute(text("ALTER TABLE effaced_outbox DROP COLUMN operation"))
            for _ in range(2):
                conn.execute(
                    text(
                        "INSERT INTO effaced_outbox "
                        "(entry_id, subject_id, resolver, ref_kind, ref_value, ref_extra, "
                        "status, attempts, enqueued_at) "
                        "VALUES (:entry_id, 'subject-1', 'stripe', 'customer', 'cus_1', "
                        "CAST(:ref_extra AS JSONB), 'pending', 0, now())"
                    ),
                    {"entry_id": uuid4(), "ref_extra": "{}"},
                )

        with pg_engine.connect() as conn:
            diffs = compare_metadata(_migration_ctx(conn), metadata)
        assert len(diffs) == 1
        kind, schema, table_name, column = diffs[0]
        assert (kind, schema, table_name) == ("add_column", None, OUTBOX_TABLE_NAME)
        assert column.name == "operation"
        assert column.server_default is not None

        with pg_engine.begin() as conn:
            op = Operations(MigrationContext.configure(conn))
            # cloned: appending a Column already attached to the bound table
            # raises "already assigned to Table"
            op.add_column(
                OUTBOX_TABLE_NAME,
                Column(
                    column.name,
                    column.type,
                    nullable=column.nullable,
                    server_default=column.server_default.arg,
                ),
            )

        with pg_engine.connect() as conn:
            operations = conn.execute(select(tables.outbox.c.operation)).scalars().all()
            assert operations == ["erase", "erase"]
            assert compare_metadata(_migration_ctx(conn), metadata) == []
    finally:
        metadata.drop_all(pg_engine)
