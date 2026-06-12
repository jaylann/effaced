"""SQL-side status_counts equivalence and zero-fill on a real Postgres."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import Engine, MetaData
from sqlalchemy.orm import sessionmaker

from effaced import (
    Outbox,
    OutboxEntry,
    OutboxStatus,
    SqlStatusCountsSource,
    SubjectRef,
    bind_tables,
)

pytestmark = pytest.mark.integration

ENQUEUED_AT = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _entry(number: int, status: OutboxStatus) -> OutboxEntry:
    return OutboxEntry(
        entry_id=UUID(int=number),
        subject_id=str(number),
        resolver="stripe",
        ref=SubjectRef(kind="stripe_customer", value=f"cus_{number}"),
        status=status,
        enqueued_at=ENQUEUED_AT,
    )


def test_zero_filled_on_an_empty_outbox(pg_engine: Engine) -> None:
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(pg_engine)
    try:
        outbox = Outbox(
            sessionmaker(pg_engine), tables.outbox, status_counts_source=SqlStatusCountsSource()
        )
        assert outbox.status_counts() == dict.fromkeys(OutboxStatus, 0)
    finally:
        metadata.drop_all(pg_engine)


def test_sql_counts_match_python_counts_under_real_postgres(pg_engine: Engine) -> None:
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(pg_engine)
    try:
        session_factory = sessionmaker(pg_engine)
        python_outbox = Outbox(session_factory, tables.outbox)
        sql_outbox = Outbox(
            session_factory, tables.outbox, status_counts_source=SqlStatusCountsSource()
        )
        population = {
            OutboxStatus.PENDING: 3,
            OutboxStatus.IN_FLIGHT: 1,
            OutboxStatus.FAILED: 2,
            OutboxStatus.ABANDONED: 1,
        }
        entries: list[OutboxEntry] = []
        number = 1
        for status, count in population.items():
            for _ in range(count):
                entries.append(_entry(number, status))
                number += 1
        with session_factory() as session:
            python_outbox.enqueue(session, entries)
            session.commit()

        expected = dict.fromkeys(OutboxStatus, 0) | population
        assert sql_outbox.status_counts() == python_outbox.status_counts() == expected
    finally:
        metadata.drop_all(pg_engine)
