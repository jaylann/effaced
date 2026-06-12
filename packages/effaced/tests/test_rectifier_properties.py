"""Properties: rectification never bleeds across subjects and converges."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

import pytest
from conftest import RecordingAuditSink
from hypothesis import given, settings
from hypothesis import strategies as st
from schema_strategies import GeneratedSchema, annotated_schemas, scaled_examples
from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import Correction, Outbox, PiiCategory, Rectifier, bind_tables
from effaced.adapters.sqlalchemy import RectificationExecutor

if TYPE_CHECKING:
    from sqlalchemy import Engine

pytestmark = pytest.mark.property


class World(NamedTuple):
    """One generated schema wired to a fresh in-memory database."""

    engine: Engine
    session_factory: sessionmaker[Session]
    rectifier: Rectifier


def build_world(schema: GeneratedSchema) -> World:
    """A fresh world per hypothesis example (no shared fixtures)."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    effaced_metadata = MetaData()
    tables = bind_tables(effaced_metadata)
    schema.metadata.create_all(engine)
    effaced_metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    rectifier = Rectifier(
        schema.data_map,
        schema.graph,
        executor=RectificationExecutor(schema.metadata),
        outbox=Outbox(session_factory, tables.outbox),
        audit_sink=RecordingAuditSink(),
    )
    return World(engine, session_factory, rectifier)


def all_rows(session: Session, schema: GeneratedSchema) -> dict[str, list[dict[str, object]]]:
    """Every table's rows, ordered by primary key."""
    out: dict[str, list[dict[str, object]]] = {}
    for name, table in schema.metadata.tables.items():
        rows = session.execute(select(table).order_by(table.c.id)).mappings()
        out[name] = [dict(row) for row in rows]
    return out


def drawn_corrections(schema: GeneratedSchema, value: str) -> tuple[Correction, ...]:
    """One correction per category the drawn schema annotates."""
    categories = sorted(
        {spec.category for specs in schema.pii_columns.values() for spec in specs.values()},
        key=lambda category: category.value,
    )
    return tuple(
        Correction(category=category, value=f"{value}:{category.value}") for category in categories
    )


@settings(max_examples=scaled_examples(4), deadline=None)
@given(schema=annotated_schemas(), value=st.text(min_size=1, max_size=8))
def test_rectification_never_bleeds_across_subjects(schema: GeneratedSchema, value: str) -> None:
    """Rectifying subject 1 leaves every one of subject 2's cells byte-identical."""
    corrections = drawn_corrections(schema, value)
    if not corrections:
        return
    world = build_world(schema)
    with world.session_factory() as session:
        schema.seed(session, 1)
        schema.seed(session, 2)
        session.commit()
    with world.session_factory() as session:
        before = all_rows(session, schema)
        world.rectifier.rectify_subject(session, "1", corrections)
        session.commit()
    with world.session_factory() as session:
        after = all_rows(session, schema)
    for name, rows in after.items():
        for old, new in zip(before[name], rows, strict=True):
            owner = schema.owner(name, int(str(old["id"])))
            if owner == 2:
                assert new == old
            else:
                values = {c.category: c.value for c in corrections}
                for column, spec in schema.pii_columns.get(name, {}).items():
                    assert new[column] == values[spec.category]
    world.engine.dispose()


@settings(max_examples=scaled_examples(4), deadline=None)
@given(schema=annotated_schemas(), value=st.text(min_size=1, max_size=8))
def test_rectification_is_convergent(schema: GeneratedSchema, value: str) -> None:
    """A second identical run changes nothing further and matches the same rows."""
    corrections = drawn_corrections(schema, value)
    if not corrections:
        return
    world = build_world(schema)
    with world.session_factory() as session:
        schema.seed(session, 1)
        session.commit()
    with world.session_factory() as session:
        first = world.rectifier.rectify_subject(session, "1", corrections)
        session.commit()
    with world.session_factory() as session:
        state_after_first = all_rows(session, schema)
    with world.session_factory() as session:
        second = world.rectifier.rectify_subject(session, "1", corrections)
        session.commit()
    with world.session_factory() as session:
        state_after_second = all_rows(session, schema)
    assert second.rectified == first.rectified
    assert state_after_second == state_after_first
    world.engine.dispose()


def test_unknown_category_correction_is_rejected_by_pydantic() -> None:
    """Corrections validate their category against the shared vocabulary."""
    with pytest.raises(ValueError, match="category"):
        Correction.model_validate({"category": "not-a-category", "value": "x"})
    assert Correction(category=PiiCategory.CONTACT, value="x").value == "x"
