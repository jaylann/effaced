"""The overdue-erasure report — the stuck-erasure half of the operator surface.

A1's ``effaced_subject_erasures`` tombstone makes a stuck local erasure
visible: a row whose ``requested_at`` is old and whose ``erased_at`` is still
``NULL`` is an erasure that was requested but never committed (first attempt or
a re-opened re-erasure). These tests pin that the report surfaces exactly those
subjects, computes their age against one cutoff instant, and writes nothing —
it is a plain ``SELECT`` over the tombstone, the read-only counterpart of
:meth:`effaced.Outbox.list_abandoned`.

SQLite drops ``FOR UPDATE``, but this report takes no locks (a plain SELECT), so
every guarantee here is portable; no ``_pg`` companion is needed.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

import pytest
from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    EffacedTables,
    OverdueErasure,
    OverdueErasureReport,
    OverdueErasureReporter,
    bind_tables,
)
from effaced.adapters.sqlalchemy.storage.subject_erasures_table import (
    SUBJECT_ERASURE_COMPLETED,
    SUBJECT_ERASURE_REQUESTED,
)

NOW = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
OLDER_THAN = timedelta(hours=1)


class Harness(NamedTuple):
    """A reporter wired to a fresh in-memory database."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    reporter: OverdueErasureReporter


@pytest.fixture()
def harness() -> Iterator[Harness]:
    """A reporter over a fresh in-memory SQLite tombstone table."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    yield Harness(
        session_factory=session_factory,
        tables=tables,
        reporter=OverdueErasureReporter(session_factory, tables.subject_erasures),
    )
    engine.dispose()


def tombstone(
    harness: Harness,
    subject_ref: str,
    *,
    requested_at: datetime,
    erased_at: datetime | None,
    status: str,
) -> None:
    """Seed one ``effaced_subject_erasures`` row directly."""
    with harness.session_factory() as session:
        session.execute(
            harness.tables.subject_erasures.insert().values(
                subject_ref=subject_ref,
                requested_at=requested_at,
                erased_at=erased_at,
                status=status,
            )
        )
        session.commit()


def test_only_overdue_incomplete_subjects_are_reported(harness: Harness) -> None:
    """An old, still-NULL ``erased_at`` is overdue; completed and recent are not."""
    tombstone(
        harness,
        "overdue",
        requested_at=NOW - timedelta(hours=3),
        erased_at=None,
        status=SUBJECT_ERASURE_REQUESTED,
    )
    tombstone(
        harness,
        "completed",
        requested_at=NOW - timedelta(hours=5),
        erased_at=NOW - timedelta(hours=4),
        status=SUBJECT_ERASURE_COMPLETED,
    )
    tombstone(
        harness,
        "recent",
        requested_at=NOW - timedelta(minutes=10),
        erased_at=None,
        status=SUBJECT_ERASURE_REQUESTED,
    )
    report = harness.reporter.report(OLDER_THAN, now=NOW)
    assert isinstance(report, OverdueErasureReport)
    assert [item.subject_ref for item in report.entries] == ["overdue"]


def test_reopened_tombstone_is_reported_when_overdue(harness: Harness) -> None:
    """A re-erasure clears ``erased_at`` to NULL — a stuck re-run is overdue too.

    A re-request of a previously completed subject re-opens the row to
    ``requested`` and nulls ``erased_at`` (ADR 0026). The report finds it by
    the same ``erased_at IS NULL`` predicate as a first attempt — the prior
    completion's timestamp never masks the stuck re-run.
    """
    tombstone(
        harness,
        "reopened",
        requested_at=NOW - timedelta(hours=2),
        erased_at=None,
        status=SUBJECT_ERASURE_REQUESTED,
    )
    report = harness.reporter.report(OLDER_THAN, now=NOW)
    assert [item.subject_ref for item in report.entries] == ["reopened"]


def test_report_carries_cutoff_age_and_request_time(harness: Harness) -> None:
    """Each entry reports the subject ref, its request time, and computed age."""
    requested = NOW - timedelta(hours=3)
    tombstone(
        harness,
        "overdue",
        requested_at=requested,
        erased_at=None,
        status=SUBJECT_ERASURE_REQUESTED,
    )
    report = harness.reporter.report(OLDER_THAN, now=NOW)
    assert report.reported_at == NOW
    assert report.older_than == OLDER_THAN
    (entry,) = report.entries
    assert entry.subject_ref == "overdue"
    # SQLite round-trips DateTime naive; the reporter normalizes to UTC.
    assert entry.requested_at == requested
    assert entry.age == timedelta(hours=3)


def test_entries_are_ordered_oldest_request_first(harness: Harness) -> None:
    """The most-overdue subject surfaces first (by ``requested_at``)."""
    tombstone(
        harness,
        "newer",
        requested_at=NOW - timedelta(hours=2),
        erased_at=None,
        status=SUBJECT_ERASURE_REQUESTED,
    )
    tombstone(
        harness,
        "older",
        requested_at=NOW - timedelta(hours=4),
        erased_at=None,
        status=SUBJECT_ERASURE_REQUESTED,
    )
    report = harness.reporter.report(OLDER_THAN, now=NOW)
    assert [item.subject_ref for item in report.entries] == ["older", "newer"]


def test_empty_when_nothing_is_overdue(harness: Harness) -> None:
    """A drained tombstone table reports no overdue erasures."""
    tombstone(
        harness,
        "completed",
        requested_at=NOW - timedelta(hours=5),
        erased_at=NOW - timedelta(hours=4),
        status=SUBJECT_ERASURE_COMPLETED,
    )
    report = harness.reporter.report(OLDER_THAN, now=NOW)
    assert report.entries == ()


def test_limit_caps_the_listing(harness: Harness) -> None:
    """The oldest ``limit`` overdue subjects are returned, most-overdue first."""
    for hours in (2, 3, 4):
        tombstone(
            harness,
            f"s{hours}",
            requested_at=NOW - timedelta(hours=hours),
            erased_at=None,
            status=SUBJECT_ERASURE_REQUESTED,
        )
    report = harness.reporter.report(OLDER_THAN, now=NOW, limit=2)
    assert [item.subject_ref for item in report.entries] == ["s4", "s3"]


def test_report_is_strictly_read_only(harness: Harness) -> None:
    """The report mutates nothing — the tombstone rows are untouched."""
    tombstone(
        harness,
        "overdue",
        requested_at=NOW - timedelta(hours=3),
        erased_at=None,
        status=SUBJECT_ERASURE_REQUESTED,
    )

    def snapshot() -> list[dict[str, object]]:
        with harness.session_factory() as session:
            return [
                dict(row)
                for row in session.execute(select(harness.tables.subject_erasures)).mappings()
            ]

    before = snapshot()
    harness.reporter.report(OLDER_THAN, now=NOW)
    harness.reporter.report(timedelta(0), now=NOW)
    assert snapshot() == before


def test_default_now_uses_current_time(harness: Harness) -> None:
    """Omitting ``now`` evaluates against the wall clock; a long-old row shows."""
    tombstone(
        harness,
        "ancient",
        requested_at=datetime(2020, 1, 1, tzinfo=UTC),
        erased_at=None,
        status=SUBJECT_ERASURE_REQUESTED,
    )
    report = harness.reporter.report(timedelta(days=1))
    assert [item.subject_ref for item in report.entries] == ["ancient"]


def test_overdue_erasure_model_is_frozen() -> None:
    """The per-subject entry is an immutable value object."""
    item = OverdueErasure(
        subject_ref="1",
        requested_at=NOW - timedelta(hours=2),
        age=timedelta(hours=2),
    )
    with pytest.raises(ValueError, match="frozen"):
        item.subject_ref = "2"  # type: ignore[misc]  # frozen-model assignment is the test
