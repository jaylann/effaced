"""The database audit sink appends durably and reads back faithfully."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import NamedTuple
from uuid import UUID, uuid4

import pytest
from sqlalchemy import MetaData, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    AuditEvent,
    AuditEventType,
    AuditIntegrityError,
    AuditSink,
    ConfigurationError,
    DatabaseAuditSink,
    EffacedTables,
    ReplaySource,
    bind_tables,
)


class SinkHarness(NamedTuple):
    """A sink wired to a fresh in-memory database."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    sink: DatabaseAuditSink


@pytest.fixture()
def harness() -> Iterator[SinkHarness]:
    """A database audit sink on a fresh in-memory SQLite database."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    yield SinkHarness(
        session_factory=session_factory,
        tables=tables,
        sink=DatabaseAuditSink(session_factory, tables.audit_events),
    )
    engine.dispose()


def event(
    subject: str = "subject-1",
    *,
    at: datetime,
    event_type: AuditEventType = AuditEventType.CONSENT_GRANTED,
    event_id: UUID | None = None,
    payload: dict[str, str | int | bool] | None = None,
) -> AuditEvent:
    return AuditEvent(
        event_id=event_id if event_id is not None else uuid4(),
        event_type=event_type,
        subject_ref=subject,
        occurred_at=at,
        payload=payload if payload is not None else {},
    )


T1 = datetime(2026, 1, 1, 10, 0)
T2 = datetime(2026, 1, 1, 11, 0)
T3 = datetime(2026, 1, 1, 12, 0)

AWARE_T1 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
AWARE_T2 = datetime(2026, 1, 1, 11, 0, tzinfo=UTC)
AWARE_T3 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_append_then_read_round_trips(harness: SinkHarness) -> None:
    appended = event(
        at=T1,
        event_type=AuditEventType.CONSENT_WITHDRAWN,
        payload={"purpose": "newsletter", "policy_version": "2026-01"},
    )
    harness.sink.append(appended)
    assert tuple(harness.sink.read("subject-1")) == (appended,)


def test_payload_json_round_trip_preserves_types(harness: SinkHarness) -> None:
    harness.sink.append(event(at=T1, payload={"version": "1", "count": 2, "dry_run": False}))
    (read_back,) = harness.sink.read("subject-1")
    assert read_back.payload["dry_run"] is False
    count = read_back.payload["count"]
    assert isinstance(count, int)
    assert not isinstance(count, bool)
    assert read_back.payload["version"] == "1"


def test_read_filters_by_subject_ref(harness: SinkHarness) -> None:
    mine = event("subject-1", at=T1)
    harness.sink.append(mine)
    harness.sink.append(event("subject-2", at=T2))
    assert tuple(harness.sink.read("subject-1")) == (mine,)


def test_read_unknown_subject_returns_empty(harness: SinkHarness) -> None:
    assert tuple(harness.sink.read("nobody")) == ()


def test_read_oldest_first_with_event_id_tiebreak(harness: SinkHarness) -> None:
    later = event(at=T2, event_id=UUID(int=1))
    tied_second = event(at=T1, event_id=UUID(int=3))
    tied_first = event(at=T1, event_id=UUID(int=2))
    for appended in (later, tied_second, tied_first):
        harness.sink.append(appended)
    assert tuple(harness.sink.read("subject-1")) == (tied_first, tied_second, later)


def test_duplicate_event_id_is_rejected_never_overwritten(harness: SinkHarness) -> None:
    appended = event(at=T1, event_id=UUID(int=7))
    harness.sink.append(appended)
    with pytest.raises(IntegrityError):
        harness.sink.append(event(at=T2, event_id=UUID(int=7)))
    assert tuple(harness.sink.read("subject-1")) == (appended,)


def test_read_unknown_event_type_raises_with_upgrade_guidance(harness: SinkHarness) -> None:
    """Events from a newer effaced fail loudly instead of being skipped."""
    with harness.session_factory.begin() as session:
        session.execute(
            harness.tables.audit_events.insert().values(
                event_id=uuid4(),
                event_type="from_the_future",
                subject_ref="subject-1",
                occurred_at=T1,
                payload={},
            )
        )
    with pytest.raises(AuditIntegrityError, match="from_the_future"):
        harness.sink.read("subject-1")


def test_database_sink_satisfies_protocol(harness: SinkHarness) -> None:
    assert isinstance(harness.sink, AuditSink)


def test_read_since_returns_the_window_across_subjects_oldest_first(
    harness: SinkHarness,
) -> None:
    """read_since spans all subjects, inclusive at the boundary (ADR 0018)."""
    before = event("subject-1", at=AWARE_T1)
    at_boundary = event("subject-2", at=AWARE_T2)
    after = event("subject-1", at=AWARE_T3)
    for appended in (after, before, at_boundary):
        harness.sink.append(appended)
    window = harness.sink.read_since(AWARE_T2)
    assert [read.event_id for read in window] == [at_boundary.event_id, after.event_id]


def test_read_since_orders_by_occurred_at_before_event_id(harness: SinkHarness) -> None:
    """``occurred_at`` is the primary sort; ``event_id`` only breaks ties.

    Event ids are chosen to run *opposite* to chronological order, so a
    read that sorted by ``event_id`` alone (dropping the ``occurred_at``
    key) would return them reversed — pinning ``occurred_at`` as primary.
    """
    earlier = event("subject-1", at=AWARE_T1, event_id=UUID(int=9))
    later = event("subject-2", at=AWARE_T3, event_id=UUID(int=1))
    for appended in (later, earlier):
        harness.sink.append(appended)
    window = harness.sink.read_since(AWARE_T1)
    assert [read.event_id for read in window] == [UUID(int=9), UUID(int=1)]


def test_read_since_breaks_occurred_at_ties_by_event_id(harness: SinkHarness) -> None:
    tied_second = event("subject-2", at=AWARE_T1, event_id=UUID(int=9))
    tied_first = event("subject-1", at=AWARE_T1, event_id=UUID(int=4))
    for appended in (tied_second, tied_first):
        harness.sink.append(appended)
    window = harness.sink.read_since(AWARE_T1)
    assert [read.event_id for read in window] == [UUID(int=4), UUID(int=9)]


def test_read_since_unknown_event_type_raises_with_upgrade_guidance(
    harness: SinkHarness,
) -> None:
    """The window read is all-or-nothing, exactly like the per-subject read."""
    with harness.session_factory.begin() as session:
        session.execute(
            harness.tables.audit_events.insert().values(
                event_id=uuid4(),
                event_type="from_the_future",
                subject_ref="subject-1",
                occurred_at=AWARE_T2,
                payload={},
            )
        )
    with pytest.raises(AuditIntegrityError, match="from_the_future"):
        harness.sink.read_since(AWARE_T1)


def test_read_since_rejects_a_naive_bound(harness: SinkHarness) -> None:
    """A naive bound could silently shift the window — refused loudly.

    Symmetric with ``ReplayPlan.derive``'s cutoff guard: the trail's
    timestamps are UTC, and on a timestamptz column a naive comparison
    shifts by the session offset, dropping events without an error.
    """
    harness.sink.append(event("subject-1", at=AWARE_T1))
    with pytest.raises(
        ConfigurationError,
        match="since must be timezone-aware; the audit trail's timestamps "
        "are UTC and a naive bound can silently shift the window",
    ):
        harness.sink.read_since(T1)  # T1 is naive


def test_database_sink_is_a_replay_source(harness: SinkHarness) -> None:
    assert isinstance(harness.sink, ReplaySource)
