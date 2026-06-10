"""The database audit sink appends durably and reads back faithfully."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
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
    DatabaseAuditSink,
    EffacedTables,
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
