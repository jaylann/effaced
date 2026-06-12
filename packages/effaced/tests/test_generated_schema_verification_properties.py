"""Post-erasure verification on arbitrary schemas: honest verified verdict."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

import pytest
from conftest import RecordingAuditSink
from hypothesis import given, settings
from schema_strategies import GeneratedSchema, annotated_schemas, scaled_examples
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import ErasurePlanner, ErasureVerifier, Outbox, bind_tables
from effaced.adapters.sqlalchemy import ErasureExecutor

if TYPE_CHECKING:
    from sqlalchemy import Engine

pytestmark = pytest.mark.property


class World(NamedTuple):
    """One generated schema wired to a fresh in-memory database."""

    engine: Engine
    session_factory: sessionmaker[Session]
    planner: ErasurePlanner
    verifier: ErasureVerifier


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
    verifier = ErasureVerifier(
        schema.data_map, schema.graph, schema.metadata, audit_sink=RecordingAuditSink()
    )
    return World(engine, session_factory, planner, verifier)


def seed_two_subjects(world: World, schema: GeneratedSchema) -> None:
    with world.session_factory() as session:
        schema.seed(session, 1)
        schema.seed(session, 2)
        session.commit()


@given(schema=annotated_schemas())
@settings(max_examples=scaled_examples(4), deadline=None)
def test_erase_then_verify_is_verified_on_any_schema(schema: GeneratedSchema) -> None:
    """After erasing subject 1, every row-deleted table reads back empty."""
    world = build_world(schema)
    seed_two_subjects(world, schema)
    with world.session_factory() as session:
        world.planner.erase_subject(session, "1")
        session.commit()
    with world.session_factory() as session:
        verification = world.verifier.verify_subject_erased(session, "1")
    assert verification.verified is True
    assert set(verification.residual) == schema.row_deleted_tables
    assert all(count == 0 for count in verification.residual.values())
    world.engine.dispose()


@given(schema=annotated_schemas())
@settings(max_examples=scaled_examples(4), deadline=None)
def test_resurrecting_a_row_deleted_row_flips_verified(schema: GeneratedSchema) -> None:
    """Re-inserting one row into a row-deleted table makes the verdict False.

    Schemas with no row-deleted table can't resurrect one; the erase-then-
    verify property already covers them, so they are skipped here.
    """
    world = build_world(schema)
    if not schema.row_deleted_tables:
        world.engine.dispose()
        return
    seed_two_subjects(world, schema)
    with world.session_factory() as session:
        world.planner.erase_subject(session, "1")
        session.commit()
    target = min(schema.row_deleted_tables)
    with world.session_factory() as session:
        session.execute(
            schema.metadata.tables[target].insert().values(**_resurrected_row(schema, target))
        )
        session.commit()
    with world.session_factory() as session:
        verification = world.verifier.verify_subject_erased(session, "1")
    assert verification.verified is False
    assert verification.residual[target] >= 1
    world.engine.dispose()


def _resurrected_row(schema: GeneratedSchema, table: str) -> dict[str, object]:
    """A fresh subject-1 row for a row-deleted table, mirroring the seeder.

    A row-deleted table is fully PII-owned but may carry annotated (all
    ``DELETE``) NOT NULL columns, so the row needs its id, a parent pointer
    back toward the subject (absent on the subject table itself), and a value
    for every annotated column.
    """
    columns = schema.metadata.tables[table].c
    values: dict[str, object] = {"id": schema.row_id(table, 1, 99)}
    if table in schema.parents:
        values["pid"] = schema.row_id(schema.parents[table], 1, 0)
    if "self_id" in columns:
        values["self_id"] = None
    for column in columns:
        if column.name not in {"id", "pid", "self_id"}:
            values[column.name] = f"resurrected:{table}.{column.name}"
    return values
