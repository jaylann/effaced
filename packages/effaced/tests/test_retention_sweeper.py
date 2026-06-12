"""RetentionSweeper.sweep — report-only expiry matching, attribution, audit (ADR 0012)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import NamedTuple

import pytest
from conftest import Base, RecordingAuditSink, seed_two_subjects
from sqlalchemy import Column, Engine, Integer, MetaData, String, Table, update
from sqlalchemy.orm import Session, sessionmaker

from effaced import (
    AuditEventType,
    DataMap,
    ManifestError,
    PiiCategory,
    RetentionPolicy,
    RetentionReport,
    RetentionReportEntry,
    RetentionSweeper,
    collect_data_map,
    pii,
    resolve_subject_graph,
    subject_link,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
"""Every test passes an explicit ``now`` — determinism over wall clocks."""

DURATION = timedelta(days=3650)
"""The conftest Invoice policy's duration (see ``conftest.Invoice``)."""

EXPIRED = datetime(2010, 5, 4)
"""Naive, like the SQLite ``closed_at`` column — far past the cutoff."""

FRESH = datetime(2025, 12, 1)
"""Naive and inside the retention window at ``NOW``."""


class SweepHarness(NamedTuple):
    """A sweeper wired to a seeded in-memory database and a recording sink."""

    session_factory: sessionmaker[Session]
    sink: RecordingAuditSink
    sweeper: RetentionSweeper
    data_map: DataMap


@pytest.fixture()
def harness(sqlite_engine: Engine) -> SweepHarness:
    """A sweeper on a database seeded with subjects 1 and 2 (anchors NULL)."""
    data_map = collect_data_map(Base.metadata)
    graph = resolve_subject_graph(data_map, Base.registry)
    sink = RecordingAuditSink()
    sweeper = RetentionSweeper(data_map, graph, Base.metadata, sink)
    session_factory = sessionmaker(sqlite_engine)
    with session_factory() as session:
        seed_two_subjects(session)
    return SweepHarness(session_factory, sink, sweeper, data_map)


def close_invoices(harness: SweepHarness, values: dict[int, datetime | None]) -> None:
    """Set ``closed_at`` per invoice id (the seed leaves every anchor NULL)."""
    invoices = Base.metadata.tables["invoices"]
    with harness.session_factory() as session:
        for invoice_id, value in values.items():
            session.execute(
                update(invoices).where(invoices.c.id == invoice_id).values(closed_at=value)
            )
        session.commit()


def sweep(harness: SweepHarness, sweeper: RetentionSweeper | None = None) -> RetentionReport:
    with harness.session_factory() as session:
        return (sweeper or harness.sweeper).sweep(session, now=NOW)


def invoices_entry(report: RetentionReport) -> RetentionReportEntry:
    (entry,) = [entry for entry in report.entries if entry.table == "invoices"]
    return entry


def with_invoice_retention(data_map: DataMap, retention: RetentionPolicy) -> DataMap:
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
        for entry in data_map.tables
    )
    return data_map.model_copy(update={"tables": tables})


def rebuilt_sweeper(harness: SweepHarness, retention: RetentionPolicy) -> RetentionSweeper:
    data_map = with_invoice_retention(harness.data_map, retention)
    graph = resolve_subject_graph(data_map, Base.registry)
    return RetentionSweeper(data_map, graph, Base.metadata, harness.sink)


# --- matching & report shape ---


def test_expired_rows_are_reported_per_subject_with_counts(harness: SweepHarness) -> None:
    close_invoices(harness, {1: EXPIRED, 2: EXPIRED})
    report = sweep(harness)
    entry = invoices_entry(report)
    assert entry.column == "billing_address"
    assert entry.reason == "§147 AO invoice retention"
    assert entry.anchor == "closed_at"
    assert entry.expired == {"1": 1, "2": 1}
    assert entry.indeterminate_rows == 0
    assert report.swept_at == NOW


def test_unexpired_rows_are_not_reported(harness: SweepHarness) -> None:
    close_invoices(harness, {1: FRESH, 2: FRESH})
    entry = invoices_entry(sweep(harness))
    assert entry.expired == {}
    assert entry.indeterminate_rows == 0
    assert harness.sink.events == []


def test_null_anchor_rows_count_as_indeterminate(harness: SweepHarness) -> None:
    entry = invoices_entry(sweep(harness))  # the seed leaves both anchors NULL
    assert entry.expired == {}
    assert entry.indeterminate_rows == 2


def test_duration_without_anchor_is_wholly_indeterminate(harness: SweepHarness) -> None:
    """No clock means no guessing — every row is indeterminate, even old ones."""
    close_invoices(harness, {1: EXPIRED, 2: EXPIRED})
    sweeper = rebuilt_sweeper(
        harness, RetentionPolicy(reason="§147 AO invoice retention", duration=DURATION)
    )
    entry = invoices_entry(sweep(harness, sweeper))
    assert entry.anchor is None
    assert entry.expired == {}
    assert entry.indeterminate_rows == 2
    assert harness.sink.events == []


def test_anchor_without_duration_is_excluded(harness: SweepHarness) -> None:
    """An unbounded duty has no expiry to evaluate — no report entry at all."""
    close_invoices(harness, {1: EXPIRED, 2: EXPIRED})
    sweeper = rebuilt_sweeper(
        harness, RetentionPolicy(reason="§147 AO invoice retention", anchor="closed_at")
    )
    assert sweep(harness, sweeper).entries == ()


def test_retain_strategy_columns_participate(harness: SweepHarness) -> None:
    """Eligibility ignores the erasure strategy: the conftest column is RETAIN."""
    close_invoices(harness, {1: EXPIRED})
    entry = invoices_entry(sweep(harness))
    assert entry.expired == {"1": 1}


def test_naive_anchor_column_compares_portably(harness: SweepHarness) -> None:
    """A tz-aware ``now`` sweeps a naive column; the cutoff boundary is inclusive."""
    naive_cutoff = (NOW - DURATION).replace(tzinfo=None)
    close_invoices(harness, {1: naive_cutoff, 2: naive_cutoff + timedelta(seconds=1)})
    entry = invoices_entry(sweep(harness))
    assert entry.expired == {"1": 1}


# --- subject attribution ---


def test_sweep_through_hop_chain_attributes_to_the_right_subject(harness: SweepHarness) -> None:
    """The anchor lives on a linked child table; attribution walks the hop chain."""
    close_invoices(harness, {1: EXPIRED, 2: FRESH})
    entry = invoices_entry(sweep(harness))
    assert entry.expired == {"1": 1}


def test_no_cross_subject_bleed_in_attribution(harness: SweepHarness) -> None:
    close_invoices(harness, {1: EXPIRED, 2: FRESH})
    report = sweep(harness)
    assert "2" not in invoices_entry(report).expired
    assert {event.subject_ref for event in harness.sink.events} == {"1"}


# --- audit ---


def test_sweep_emits_one_event_per_subject_with_expired_data(harness: SweepHarness) -> None:
    close_invoices(harness, {1: EXPIRED, 2: EXPIRED})
    sweep(harness)
    first, second = harness.sink.events
    assert first.event_type is AuditEventType.RETENTION_EXPIRED
    assert second.event_type is AuditEventType.RETENTION_EXPIRED
    assert (first.subject_ref, second.subject_ref) == ("1", "2")
    for event in (first, second):
        assert event.payload == {"table": "invoices", "column": "billing_address", "rows": 1}


def test_event_payload_carries_names_and_counts_only(harness: SweepHarness) -> None:
    """Payloads name tables and columns — never values, never anchor timestamps."""
    close_invoices(harness, {1: EXPIRED, 2: EXPIRED})
    sweep(harness)
    assert harness.sink.events
    leaks = ("alice", "bob", "street", "2010", "2026")  # PII fragments and timestamps
    for event in harness.sink.events:
        assert set(event.payload) == {"table", "column", "rows"}
        for value in event.payload.values():
            assert not any(fragment in str(value).lower() for fragment in leaks)


def test_repeated_sweep_reemits(harness: SweepHarness) -> None:
    """Each run is evidence — still-expired data is re-reported, not deduplicated."""
    close_invoices(harness, {1: EXPIRED})
    sweep(harness)
    sweep(harness)
    assert len(harness.sink.events) == 2
    assert all(
        event.event_type is AuditEventType.RETENTION_EXPIRED for event in harness.sink.events
    )


# --- collector validation (fail loudly at assembly, ADR 0007 direction) ---


def _anchored_metadata(anchor_column: Column[str] | None) -> MetaData:
    """A one-table schema whose annotated column names a ``closed_at`` anchor."""
    metadata = MetaData()
    columns = [
        Column("id", Integer, primary_key=True),
        Column(
            "note",
            String,
            info=pii(
                PiiCategory.COMMUNICATION,
                retention=RetentionPolicy(reason="ticket duty", anchor="closed_at"),
            ),
        ),
    ]
    if anchor_column is not None:
        columns.append(anchor_column)
    Table("tickets", metadata, *columns, info=subject_link(""))
    return metadata


def test_nonexistent_anchor_fails_at_collection() -> None:
    metadata = _anchored_metadata(None)
    with pytest.raises(ManifestError, match=r"'tickets'.*'note'.*'closed_at'.*does not exist"):
        collect_data_map(metadata)


def test_non_datetime_anchor_fails_at_collection() -> None:
    metadata = _anchored_metadata(Column("closed_at", String))
    with pytest.raises(ManifestError, match=r"'tickets'.*'note'.*'closed_at'.*not a datetime"):
        collect_data_map(metadata)
