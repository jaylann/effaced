"""Replay proofs on arbitrary schemas and event orderings (ADR 0018)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple
from uuid import UUID

import pytest
from conftest import RecordingAuditSink
from hypothesis import given, settings
from hypothesis import strategies as st
from schema_strategies import GeneratedSchema, annotated_schemas, scaled_examples
from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    AuditEvent,
    AuditEventType,
    ErasurePlanner,
    ErasureStrategy,
    Outbox,
    Replayer,
    ReplayPlan,
    bind_tables,
)
from effaced.adapters.sqlalchemy import ErasureExecutor

if TYPE_CHECKING:
    from sqlalchemy import Engine

pytestmark = pytest.mark.property

BACKUP_AT = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

DRAWN_TYPES = (
    AuditEventType.ERASURE_REQUESTED,
    AuditEventType.ERASURE_STEP_SUCCEEDED,
    AuditEventType.ERASURE_STEP_FAILED,
    AuditEventType.ERASURE_LOCAL_COMPLETED,
    AuditEventType.ERASURE_COMPLETED,
    AuditEventType.CONSENT_GRANTED,
)


@st.composite
def trails(draw: st.DrawFn) -> list[AuditEvent]:
    """A surviving trail: mixed subjects, types, and times around the cutoff."""
    specs = draw(
        st.lists(
            st.tuples(
                st.sampled_from(("1", "2", "3")),
                st.sampled_from(DRAWN_TYPES),
                st.integers(min_value=-120, max_value=120),
            ),
            max_size=30,
        )
    )
    return [
        AuditEvent(
            event_id=UUID(int=index),
            event_type=event_type,
            subject_ref=subject,
            occurred_at=BACKUP_AT + timedelta(minutes=minutes),
        )
        for index, (subject, event_type, minutes) in enumerate(specs)
    ]


@given(events=trails().flatmap(lambda batch: st.tuples(st.just(batch), st.permutations(batch))))
@settings(deadline=None)
def test_derivation_is_a_pure_function_of_the_event_set(
    events: tuple[list[AuditEvent], list[AuditEvent]],
) -> None:
    """Any ordering of the same events derives an equal plan; buckets are disjoint."""
    drawn, permuted = events
    plan = ReplayPlan.derive(drawn, backup_taken_at=BACKUP_AT)
    assert plan == ReplayPlan.derive(permuted, backup_taken_at=BACKUP_AT)
    replayable = {entry.subject_id for entry in plan.entries}
    assert replayable.isdisjoint(plan.indeterminate)
    assert replayable.isdisjoint(plan.failed_only)
    assert set(plan.indeterminate).isdisjoint(plan.failed_only)
    for entry in plan.entries:
        assert entry.last_completed_at >= BACKUP_AT  # the window is inclusive


class World(NamedTuple):
    """One generated schema wired to a fresh in-memory database."""

    engine: Engine
    session_factory: sessionmaker[Session]
    sink: RecordingAuditSink
    planner: ErasurePlanner
    replayer: Replayer


def build_world(schema: GeneratedSchema) -> World:
    """A fresh world per hypothesis example (no shared fixtures)."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    effaced_metadata = MetaData()
    tables = bind_tables(effaced_metadata)
    schema.metadata.create_all(engine)
    effaced_metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    sink = RecordingAuditSink()
    planner = ErasurePlanner(
        schema.data_map,
        schema.graph,
        executor=ErasureExecutor(schema.metadata),
        outbox=Outbox(session_factory, tables.outbox),
        audit_sink=sink,
    )
    return World(engine, session_factory, sink, planner, Replayer(planner, sink))


def subject_rows(
    session: Session, schema: GeneratedSchema, table_name: str, subject_id: int
) -> list[dict[str, object]]:
    """One subject's rows in one table, ordered by primary key."""
    table = schema.metadata.tables[table_name]
    rows = session.execute(select(table).order_by(table.c.id)).mappings()
    return [
        dict(row) for row in rows if schema.owner(table_name, int(str(row["id"]))) == subject_id
    ]


def state_shape(session: Session, schema: GeneratedSchema) -> dict[str, object]:
    """The convergence target: surviving row ids, RETAIN cells, subject 2's rows.

    Anonymized cells legitimately churn between runs (fresh surrogates per
    ADR 0009), so the shape deliberately excludes them.
    """
    return {
        "subject_1_ids": {
            name: [row["id"] for row in subject_rows(session, schema, name, 1)]
            for name in schema.rows
        },
        "subject_1_retained": {
            (name, row["id"], column): row[column]
            for name, specs in schema.pii_columns.items()
            for row in subject_rows(session, schema, name, 1)
            for column, spec in specs.items()
            if spec.erasure is ErasureStrategy.RETAIN
        },
        "subject_2": {name: subject_rows(session, schema, name, 2) for name in schema.rows},
    }


def seed_both(world: World, schema: GeneratedSchema) -> None:
    with world.session_factory() as session:
        schema.seed(session, 1)
        schema.seed(session, 2)
        session.commit()


def restore_backup(world: World, schema: GeneratedSchema) -> None:
    """Wipe the schema tables and re-seed the pre-erasure backup."""
    with world.session_factory() as session:
        for table in reversed(schema.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
    seed_both(world, schema)


@given(schema=annotated_schemas())
@settings(max_examples=scaled_examples(4), deadline=None)
def test_replay_on_any_schema_restores_the_post_erasure_state(
    schema: GeneratedSchema,
) -> None:
    """Erase → restore → replay converges back to the erased state shape.

    And replaying again converges to the same shape with zero further
    deletes — a replay of a replay is a no-op success.
    """
    world = build_world(schema)
    seed_both(world, schema)
    backup_taken_at = datetime.now(UTC)
    with world.session_factory() as session:
        world.planner.erase_subject(session, "1")
        session.commit()
    with world.session_factory() as session:
        erased_shape = state_shape(session, schema)
    surviving_trail = tuple(world.sink.events)
    restore_backup(world, schema)
    plan = world.replayer.plan(surviving_trail, backup_taken_at=backup_taken_at)
    assert [entry.subject_id for entry in plan.entries] == ["1"]
    with world.session_factory() as session:
        (first,) = world.replayer.replay(session, plan)
        session.commit()
    assert first.deleted == {name: schema.rows[name] for name in schema.row_deleted_tables}
    with world.session_factory() as session:
        assert state_shape(session, schema) == erased_shape
    with world.session_factory() as session:
        (rerun,) = world.replayer.replay(session, plan)
        session.commit()
    assert all(count == 0 for count in rerun.deleted.values())
    with world.session_factory() as session:
        assert state_shape(session, schema) == erased_shape
    replayed = [
        event for event in world.sink.events if event.event_type is AuditEventType.ERASURE_REPLAYED
    ]
    assert len(replayed) == 2  # each run is evidence
    world.engine.dispose()
