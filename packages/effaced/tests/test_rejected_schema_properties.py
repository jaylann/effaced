"""Unsupported schema shapes fail loudly, never partially.

The valid generated space (``schema_strategies.py``) proves erasure and
export *succeed*; this module proves the rejected space (ADR 0007 planner
conflicts and many-to-many subject paths) is refused before any DML runs —
the never-partial-erasure contract this library exists to uphold.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from conftest import RecordingAuditSink
from hypothesis import given, settings
from rejected_schema_strategies import (
    GeneratedReject,
    conflicting_schemas,
    m2m_schemas,
)
from schema_strategies import scaled_examples
from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import ErasurePlanner, Outbox, bind_tables, resolve_subject_graph
from effaced.adapters.sqlalchemy import ErasureExecutor
from effaced.exceptions import ManifestError, RetentionViolationError, SubjectResolutionError

if TYPE_CHECKING:
    from sqlalchemy import Engine

pytestmark = pytest.mark.property


def _seeded_engine(schema: GeneratedReject) -> Engine:
    """A fresh in-memory database with both subjects' rows committed.

    The effaced-owned tables are deliberately absent: ``erase_subject`` must
    raise at plan time, before it would touch the outbox or audit trail.
    """
    engine = create_engine("sqlite://", poolclass=StaticPool)
    schema.metadata.create_all(engine)
    with sessionmaker(engine)() as session:
        schema.seed(session, 1)
        schema.seed(session, 2)
        session.commit()
    return engine


def _all_rows(engine: Engine, schema: GeneratedReject) -> dict[str, list[dict[str, object]]]:
    """Every table's rows, ordered by primary key — the byte-identity baseline."""
    with sessionmaker(engine)() as session:
        return {
            name: [
                dict(row) for row in session.execute(select(table).order_by(table.c.id)).mappings()
            ]
            for name, table in schema.metadata.tables.items()
        }


@given(schema=conflicting_schemas())
@settings(max_examples=scaled_examples(4), deadline=None)
def test_conflicting_schema_is_rejected_before_any_dml(schema: GeneratedReject) -> None:
    """A row-deleted ancestor with a surviving child raises, leaving data intact.

    The planner detects the unsatisfiable plan (ADR 0007) and raises
    ``RetentionViolationError`` (retained child) or ``ManifestError``
    (survivor with nothing erasable) deterministically — and ``erase_subject``
    raises before its first DELETE/UPDATE, so the seeded rows stay
    byte-identical (never a partial erasure).
    """
    graph = resolve_subject_graph(schema.data_map, schema.mappers)
    bare = ErasurePlanner(schema.data_map, graph)
    with pytest.raises((RetentionViolationError, ManifestError)):
        bare.plan("1")
    # Determinism: a second plan raises the same way.
    with pytest.raises((RetentionViolationError, ManifestError)):
        bare.plan("1")

    engine = _seeded_engine(schema)
    before = _all_rows(engine, schema)
    effaced = bind_tables(MetaData())
    planner = ErasurePlanner(
        schema.data_map,
        graph,
        executor=ErasureExecutor(schema.metadata),
        outbox=Outbox(sessionmaker(engine), effaced.outbox),
        audit_sink=RecordingAuditSink(),
    )
    with sessionmaker(engine)() as session:
        with pytest.raises((RetentionViolationError, ManifestError)):
            planner.erase_subject(session, "1")
        session.rollback()
    assert _all_rows(engine, schema) == before
    engine.dispose()


@given(schema=m2m_schemas())
@settings(max_examples=scaled_examples(4), deadline=None)
def test_many_to_many_subject_path_is_rejected_naming_the_link(
    schema: GeneratedReject,
) -> None:
    """A subject path through a secondary table is refused, never silently kept.

    ``resolve_subject_graph`` raises ``SubjectResolutionError`` whose message
    names the many-to-many relationship rather than returning a graph that
    omits the association table.
    """
    with pytest.raises(SubjectResolutionError) as excinfo:
        resolve_subject_graph(schema.data_map, schema.mappers)
    message = str(excinfo.value)
    assert "secondary" in message or "many-to-many" in message
    assert "owner" in message
