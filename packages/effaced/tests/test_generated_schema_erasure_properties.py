"""Erasure proofs on arbitrary schemas: no bleed, retention kept, idempotent."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

import pytest
from conftest import RecordingAuditSink
from hypothesis import given, settings
from schema_strategies import GeneratedSchema, annotated_schemas, scaled_examples
from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import ErasurePlanner, ErasureStrategy, Outbox, bind_tables
from effaced.adapters.sqlalchemy import ErasureExecutor

if TYPE_CHECKING:
    from sqlalchemy import Engine

pytestmark = pytest.mark.property


class World(NamedTuple):
    """One generated schema wired to a fresh in-memory database."""

    engine: Engine
    session_factory: sessionmaker[Session]
    planner: ErasurePlanner


def build_world(schema: GeneratedSchema) -> World:
    """A fresh world per hypothesis example (no shared fixtures)."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    effaced_metadata = MetaData()
    tables = bind_tables(effaced_metadata)
    schema.metadata.create_all(engine)
    effaced_metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    planner = ErasurePlanner(
        schema.data_map,
        schema.graph,
        executor=ErasureExecutor(schema.metadata),
        outbox=Outbox(session_factory, tables.outbox),
        audit_sink=RecordingAuditSink(),
    )
    return World(engine, session_factory, planner)


def subject_rows(
    session: Session, schema: GeneratedSchema, table_name: str, subject_id: int
) -> list[dict[str, object]]:
    """One subject's rows in one table, ordered by primary key."""
    table = schema.metadata.tables[table_name]
    rows = session.execute(select(table).order_by(table.c.id)).mappings()
    return [
        dict(row) for row in rows if schema.owner(table_name, int(str(row["id"]))) == subject_id
    ]


def snapshot(
    session: Session, schema: GeneratedSchema, subject_id: int
) -> dict[str, list[dict[str, object]]]:
    """Every table's rows for one subject — the byte-identity baseline."""
    return {name: subject_rows(session, schema, name, subject_id) for name in schema.rows}


def retained_cells(
    session: Session, schema: GeneratedSchema, subject_id: int
) -> dict[tuple[str, object, str], object]:
    """Every RETAIN-column cell one subject owns, keyed by (table, row id, column)."""
    return {
        (name, row["id"], column): row[column]
        for name, specs in schema.pii_columns.items()
        for row in subject_rows(session, schema, name, subject_id)
        for column, spec in specs.items()
        if spec.erasure is ErasureStrategy.RETAIN
    }


def seed_two_subjects(world: World, schema: GeneratedSchema) -> None:
    with world.session_factory() as session:
        schema.seed(session, 1)
        schema.seed(session, 2)
        session.commit()


@given(schema=annotated_schemas())
@settings(max_examples=scaled_examples(4), deadline=None)
def test_erasure_on_any_schema_never_bleeds_and_preserves_retained(
    schema: GeneratedSchema,
) -> None:
    """Erasing subject 1 leaves subject 2 byte-identical and RETAIN cells intact."""
    world = build_world(schema)
    seed_two_subjects(world, schema)
    with world.session_factory() as session:
        before_subject_2 = snapshot(session, schema, 2)
        retained_before = retained_cells(session, schema, 1)
    with world.session_factory() as session:
        result = world.planner.erase_subject(session, "1")
        session.commit()
    assert result.deleted == {name: schema.rows[name] for name in schema.row_deleted_tables}
    assert result.retained == {name: schema.rows[name] for name in schema.retain_tables}
    assert result.anonymized == {name: schema.rows[name] for name in schema.anonymize_tables}
    with world.session_factory() as session:
        assert snapshot(session, schema, 2) == before_subject_2
        assert retained_cells(session, schema, 1) == retained_before
        for name in schema.row_deleted_tables:
            assert subject_rows(session, schema, name, 1) == []
        for name, specs in schema.pii_columns.items():
            for row in subject_rows(session, schema, name, 1):
                for column, spec in specs.items():
                    if spec.erasure is not ErasureStrategy.RETAIN:
                        assert "<s1>" not in str(row[column])
    world.engine.dispose()


@given(schema=annotated_schemas())
@settings(max_examples=scaled_examples(4), deadline=None)
def test_erasure_on_any_schema_is_idempotent(schema: GeneratedSchema) -> None:
    """A rerun deletes nothing further and converges to the same state shape.

    Anonymized cells legitimately change between runs (fresh surrogates per
    ADR 0009), so convergence is asserted on the state's shape — surviving
    row ids, RETAIN cells, and the other subject's rows — not on surrogate
    bytes.
    """
    world = build_world(schema)
    seed_two_subjects(world, schema)
    with world.session_factory() as session:
        world.planner.erase_subject(session, "1")
        session.commit()
    with world.session_factory() as session:
        ids_after_first = {
            name: [row["id"] for row in subject_rows(session, schema, name, 1)]
            for name in schema.rows
        }
        retained_after_first = retained_cells(session, schema, 1)
        subject_2_after_first = snapshot(session, schema, 2)
    with world.session_factory() as session:
        rerun = world.planner.erase_subject(session, "1")
        session.commit()
    assert all(count == 0 for count in rerun.deleted.values())
    assert rerun.retained == {name: schema.rows[name] for name in schema.retain_tables}
    with world.session_factory() as session:
        assert {
            name: [row["id"] for row in subject_rows(session, schema, name, 1)]
            for name in schema.rows
        } == ids_after_first
        assert retained_cells(session, schema, 1) == retained_after_first
        assert snapshot(session, schema, 2) == subject_2_after_first
    world.engine.dispose()
