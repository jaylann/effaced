"""Property guarantees pinned by ADR 0012: report-only, honest, time-free planning."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import Base, RecordingAuditSink, seed_two_subjects
from hypothesis import given, settings
from hypothesis import strategies as st
from schema_strategies import scaled_examples
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    update,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    AuditEventType,
    CompositeSubjectId,
    DataMap,
    ErasurePlanner,
    PiiCategory,
    RetentionPolicy,
    RetentionSweeper,
    canonical_subject_id,
    collect_data_map,
    pii,
    resolve_subject_graph,
    resolve_subject_graph_from_fk,
    subject_link,
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


def test_composite_sweep_attributes_expired_rows_without_shared_key_bleed() -> None:
    """A composite subject's expired rows are attributed to the WHOLE key.

    Two subjects share ``tenant=1`` and differ only in ``user_id`` (ADR 0025);
    each owns one expired ``notes`` row. The attribution path
    (``_attribution_statement`` + ``_subject_ref``) is the one matching path
    that does NOT route through ``subject_scope`` — it buckets all rows by
    owner via the full-key join — so it gets its own composite proof: each
    subject's ``RETENTION_EXPIRED`` entry must carry that subject's full
    composite canonical ref and count exactly its own row, with no bleed of
    the shared ``tenant`` component across the two.
    """
    metadata = MetaData()
    Table(
        "members",
        metadata,
        Column("tenant", Integer, primary_key=True, autoincrement=False),
        Column("user_id", Integer, primary_key=True, autoincrement=False),
        Column("email", String(64), nullable=False, info=pii(PiiCategory.CONTACT)),
        info=subject_link("", subject_id_columns=("tenant", "user_id")),
    )
    Table(
        "notes",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=False),
        Column("m_tenant", Integer, nullable=False),
        Column("m_user", Integer, nullable=False),
        Column("closed_at", DateTime, nullable=True),
        Column(
            "body",
            String(64),
            nullable=False,
            info=pii(
                PiiCategory.BEHAVIORAL,
                retention=RetentionPolicy(
                    reason="statutory record", duration=timedelta(days=30), anchor="closed_at"
                ),
            ),
        ),
        ForeignKeyConstraint(["m_tenant", "m_user"], ["members.tenant", "members.user_id"]),
        info=subject_link("members"),
    )
    data_map = collect_data_map(metadata)
    graph = resolve_subject_graph_from_fk(data_map, metadata)
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata.create_all(engine)
    factory = sessionmaker(engine)
    long_ago = datetime(2020, 1, 1)  # well past any 30-day window
    with factory() as session:
        session.execute(
            metadata.tables["members"].insert(),
            [
                {"tenant": 1, "user_id": 10, "email": "a@x"},
                {"tenant": 1, "user_id": 20, "email": "b@x"},
            ],
        )
        session.execute(
            metadata.tables["notes"].insert(),
            [
                {"id": 1, "m_tenant": 1, "m_user": 10, "closed_at": long_ago, "body": "A"},
                {"id": 2, "m_tenant": 1, "m_user": 20, "closed_at": long_ago, "body": "B"},
            ],
        )
        session.commit()

    sink = RecordingAuditSink()
    sweeper = RetentionSweeper(data_map, graph, metadata, sink)
    with factory() as session:
        report = sweeper.sweep(session, now=datetime(2026, 1, 1, tzinfo=UTC))
    engine.dispose()

    ref_10 = canonical_subject_id(CompositeSubjectId(values=("1", "10")))
    ref_20 = canonical_subject_id(CompositeSubjectId(values=("1", "20")))
    assert ref_10 != ref_20  # the shared tenant component never collapses them
    (notes_entry,) = [entry for entry in report.entries if entry.table == "notes"]
    # Each subject's own row, attributed on the full (tenant, user_id) key.
    assert notes_entry.expired == {ref_10: 1, ref_20: 1}
    # The audit events carry the same full composite refs, never a partial key.
    expired_events = {
        event.subject_ref: event.payload["rows"]
        for event in sink.events
        if event.event_type is AuditEventType.RETENTION_EXPIRED
    }
    assert expired_events == {ref_10: 1, ref_20: 1}
