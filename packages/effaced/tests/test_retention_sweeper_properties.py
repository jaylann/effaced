"""Property guarantees pinned by ADR 0012: report-only, honest, time-free planning."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import Base, RecordingAuditSink, seed_two_subjects
from hypothesis import given, settings
from hypothesis import strategies as st
from schema_strategies import scaled_examples
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    DataMap,
    ErasurePlanner,
    RetentionPolicy,
    RetentionSweeper,
    collect_data_map,
    resolve_subject_graph,
)

pytestmark = pytest.mark.property

DATA_MAP = collect_data_map(Base.metadata)
GRAPH = resolve_subject_graph(DATA_MAP, Base.registry)

durations = st.timedeltas(min_value=timedelta(0), max_value=timedelta(days=9000))
anchor_values = st.none() | st.datetimes(
    min_value=datetime(2000, 1, 1), max_value=datetime(2050, 1, 1)
)
nows = st.datetimes(min_value=datetime(2000, 1, 1), max_value=datetime(2050, 1, 1)).map(
    lambda value: value.replace(tzinfo=UTC)
)


def with_invoice_retention(retention: RetentionPolicy) -> DataMap:
    """The collected manifest with the invoice column's policy swapped out."""
    tables = tuple(
        entry.model_copy(
            update={
                "columns": tuple(
                    column.model_copy(
                        update={"spec": column.spec.model_copy(update={"retention": retention})}
                    )
                    for column in entry.columns
                )
            }
        )
        if entry.name == "invoices"
        else entry
        for entry in DATA_MAP.tables
    )
    return DATA_MAP.model_copy(update={"tables": tables})


def seeded_factory(
    closed_at: tuple[datetime | None, datetime | None],
) -> sessionmaker[Session]:
    """A fresh seeded in-memory database with the drawn anchor values applied."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    invoices = Base.metadata.tables["invoices"]
    with session_factory() as session:
        seed_two_subjects(session)
        for invoice_id, value in zip((1, 2), closed_at, strict=True):
            session.execute(
                update(invoices).where(invoices.c.id == invoice_id).values(closed_at=value)
            )
        session.commit()
    return session_factory


def snapshot(session_factory: sessionmaker[Session]) -> list[tuple[tuple[object, ...], ...]]:
    """Every row of every table, deterministically ordered."""
    with session_factory() as session:
        return [
            tuple(tuple(row) for row in session.execute(statement))
            for table in Base.metadata.sorted_tables
            for statement in (table.select().order_by(*table.primary_key.columns),)
        ]


@given(duration=durations, closed_at=st.tuples(anchor_values, anchor_values), now=nows)
@settings(max_examples=scaled_examples(4), deadline=None)
def test_sweep_never_mutates_any_table(
    duration: timedelta,
    closed_at: tuple[datetime | None, datetime | None],
    now: datetime,
) -> None:
    """Report-only by construction: a sweep leaves every table byte-identical."""
    data_map = with_invoice_retention(
        RetentionPolicy(reason="drawn duty", duration=duration, anchor="closed_at")
    )
    sweeper = RetentionSweeper(
        data_map,
        resolve_subject_graph(data_map, Base.registry),
        Base.metadata,
        RecordingAuditSink(),
    )
    session_factory = seeded_factory(closed_at)
    before = snapshot(session_factory)
    with session_factory() as session:
        sweeper.sweep(session, now=now)
        assert not session.new
        assert not session.dirty
        assert not session.deleted
    assert snapshot(session_factory) == before


@given(duration=durations, closed_at=st.tuples(anchor_values, anchor_values), now=nows)
@settings(max_examples=scaled_examples(4), deadline=None)
def test_no_anchor_is_never_matched_as_expired(
    duration: timedelta,
    closed_at: tuple[datetime | None, datetime | None],
    now: datetime,
) -> None:
    """A duration without an anchor is counted indeterminate, never guessed."""
    data_map = with_invoice_retention(RetentionPolicy(reason="drawn duty", duration=duration))
    sweeper = RetentionSweeper(
        data_map,
        resolve_subject_graph(data_map, Base.registry),
        Base.metadata,
        RecordingAuditSink(),
    )
    session_factory = seeded_factory(closed_at)
    with session_factory() as session:
        report = sweeper.sweep(session, now=now)
    (entry,) = [entry for entry in report.entries if entry.table == "invoices"]
    assert entry.expired == {}
    assert entry.indeterminate_rows == 2


@given(
    duration=st.none() | durations,
    anchor=st.none() | st.just("closed_at"),
)
def test_plan_is_unaffected_by_duration_and_anchor(
    duration: timedelta | None,
    anchor: str | None,
) -> None:
    """plan() is a pure function of the manifest, never of the wall clock."""
    baseline = ErasurePlanner(DATA_MAP, GRAPH).plan("1")
    mutated = with_invoice_retention(
        RetentionPolicy(reason="§147 AO invoice retention", duration=duration, anchor=anchor)
    )
    assert ErasurePlanner(mutated, GRAPH).plan("1") == baseline
