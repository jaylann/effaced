"""The restriction ledger derives status, keeps history, and mirrors audit events."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import NamedTuple
from uuid import UUID

import pytest
from conftest import RecordingAuditSink
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    AuditEvent,
    AuditEventType,
    EffacedTables,
    RestrictionLedger,
    RestrictionRecord,
    bind_tables,
)


class LedgerHarness(NamedTuple):
    """A ledger wired to an in-memory database and a recording sink."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    sink: RecordingAuditSink
    ledger: RestrictionLedger


class ExplodingSink:
    """An ``AuditSink`` whose append always fails."""

    def append(self, event: AuditEvent) -> None:
        """Refuse to persist anything."""
        msg = "audit storage unavailable"
        raise RuntimeError(msg)

    def read(self, subject_ref: str) -> Sequence[AuditEvent]:
        """Nothing was ever stored."""
        return ()


@pytest.fixture()
def harness() -> Iterator[LedgerHarness]:
    """A restriction ledger on a fresh in-memory SQLite database."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    sink = RecordingAuditSink()
    yield LedgerHarness(
        session_factory=sessionmaker(engine),
        tables=tables,
        sink=sink,
        ledger=RestrictionLedger(tables.restriction_records, sink),
    )
    engine.dispose()


def restriction(
    subject: str,
    purpose: str | None,
    *,
    restricted: bool,
    at: datetime,
    reason: str | None = None,
    source: str | None = None,
) -> RestrictionRecord:
    return RestrictionRecord(
        subject_id=subject,
        purpose=purpose,
        restricted=restricted,
        reason=reason,
        recorded_at=at,
        source=source,
    )


T1 = datetime(2026, 1, 1, 10, 0)
T2 = datetime(2026, 1, 1, 11, 0)
T3 = datetime(2026, 1, 1, 12, 0)


@pytest.mark.parametrize(
    ("restricted", "event_type"),
    [
        (True, AuditEventType.RESTRICTION_PLACED),
        (False, AuditEventType.RESTRICTION_LIFTED),
    ],
)
def test_record_appends_and_mirrors_audit_event(
    harness: LedgerHarness, restricted: bool, event_type: AuditEventType
) -> None:
    with harness.session_factory() as session:
        harness.ledger.record(session, restriction("alice", "ads", restricted=restricted, at=T1))
        session.commit()
        assert harness.ledger.history(session, "alice") != ()
    (event,) = harness.sink.events
    assert event.event_type is event_type
    assert event.subject_ref == "alice"
    assert event.occurred_at == T1


def test_status_with_no_records_is_unrestricted(harness: LedgerHarness) -> None:
    with harness.session_factory() as session:
        assert harness.ledger.status(session, "alice") is False
        assert harness.ledger.status(session, "alice", "ads") is False


def test_global_restriction_shadows_purpose_lift(harness: LedgerHarness) -> None:
    """A purpose-level lift cannot undo a global restriction."""
    with harness.session_factory() as session:
        harness.ledger.record(session, restriction("alice", None, restricted=True, at=T1))
        harness.ledger.record(session, restriction("alice", "ads", restricted=False, at=T2))
        session.commit()
        assert harness.ledger.status(session, "alice", "ads") is True
        assert harness.ledger.status(session, "alice") is True


def test_purpose_restriction_does_not_restrict_other_purposes(harness: LedgerHarness) -> None:
    with harness.session_factory() as session:
        harness.ledger.record(session, restriction("alice", "ads", restricted=True, at=T1))
        session.commit()
        assert harness.ledger.status(session, "alice", "ads") is True
        assert harness.ledger.status(session, "alice", "analytics") is False


def test_purpose_restriction_does_not_answer_global_status(harness: LedgerHarness) -> None:
    """``status(purpose=None)`` consults only the global (purpose IS NULL) records."""
    with harness.session_factory() as session:
        harness.ledger.record(session, restriction("alice", "ads", restricted=True, at=T1))
        session.commit()
        assert harness.ledger.status(session, "alice") is False


@pytest.mark.parametrize("place_record_id", [UUID(int=1), UUID(int=2)])
def test_exact_timestamp_tie_resolves_to_restricted(
    harness: LedgerHarness, place_record_id: UUID
) -> None:
    """Equal ``recorded_at`` resolves to the restricting record, whatever the ids."""
    lift_record_id = UUID(int=2) if place_record_id == UUID(int=1) else UUID(int=1)
    common = {
        "subject_id": "alice",
        "purpose": "ads",
        "reason": None,
        "recorded_at": T1,
        "source": None,
    }
    with harness.session_factory() as session:
        session.execute(
            harness.tables.restriction_records.insert(),
            [
                {"record_id": place_record_id, "restricted": True, **common},
                {"record_id": lift_record_id, "restricted": False, **common},
            ],
        )
        session.commit()
        first = harness.ledger.status(session, "alice", "ads")
        second = harness.ledger.status(session, "alice", "ads")
    assert first is True
    assert second is first


def test_lift_without_placement_simply_appends(harness: LedgerHarness) -> None:
    """Events are evidence, not a state machine — no transition validation."""
    lift = restriction("alice", "ads", restricted=False, at=T1)
    with harness.session_factory() as session:
        harness.ledger.record(session, lift)
        session.commit()
        assert harness.ledger.status(session, "alice", "ads") is False
        assert harness.ledger.history(session, "alice") == (lift,)
    (event,) = harness.sink.events
    assert event.event_type is AuditEventType.RESTRICTION_LIFTED


def test_payloads_never_contain_reason_or_source(harness: LedgerHarness) -> None:
    """Free-text fields are PII-bearing; events carry the scope only."""
    with harness.session_factory() as session:
        harness.ledger.record(
            session,
            restriction(
                "alice",
                "ads",
                restricted=True,
                at=T1,
                reason="accuracy disputed by alice@example.com",
                source="support-ticket-7",
            ),
        )
        session.commit()
    (event,) = harness.sink.events
    assert event.payload == {"purpose": "ads"}


def test_global_record_payload_is_scope_all(harness: LedgerHarness) -> None:
    with harness.session_factory() as session:
        harness.ledger.record(session, restriction("alice", None, restricted=True, at=T1))
        session.commit()
    (event,) = harness.sink.events
    assert event.payload == {"scope": "all"}


def test_no_cross_subject_bleed(harness: LedgerHarness) -> None:
    alice = restriction("alice", None, restricted=True, at=T1)
    with harness.session_factory() as session:
        harness.ledger.record(session, alice)
        harness.ledger.record(session, restriction("bob", None, restricted=False, at=T2))
        session.commit()
        assert harness.ledger.status(session, "alice") is True
        assert harness.ledger.status(session, "bob") is False
        assert harness.ledger.history(session, "alice") == (alice,)


def test_failing_sink_raises_before_commit(harness: LedgerHarness) -> None:
    """A failed audit append raises out of record(); nothing persists after rollback."""
    ledger = RestrictionLedger(harness.tables.restriction_records, ExplodingSink())
    with harness.session_factory() as session:
        with pytest.raises(RuntimeError, match="audit storage unavailable"):
            ledger.record(session, restriction("alice", None, restricted=True, at=T1))
        session.rollback()
        assert ledger.status(session, "alice") is False
        assert ledger.history(session, "alice") == ()


def test_history_returns_full_records_oldest_first(harness: LedgerHarness) -> None:
    """History is unredacted — reason and source DO appear — and chronological."""
    records = (
        restriction("alice", "ads", restricted=True, at=T1, reason="accuracy disputed"),
        restriction("alice", None, restricted=True, at=T2, source="dsar-portal"),
        restriction("alice", "ads", restricted=False, at=T3, reason="dispute settled"),
    )
    with harness.session_factory() as session:
        for record in (records[2], records[0], records[1]):  # insert out of order
            harness.ledger.record(session, record)
        session.commit()
        assert harness.ledger.history(session, "alice") == records


def test_history_breaks_ties_by_record_id(harness: LedgerHarness) -> None:
    """Identical timestamps order by record_id, not by insertion order.

    Rows are written through the public table handle because the tiebreaker
    is only observable with chosen record ids; the larger id is inserted
    first so storage order and record_id order disagree.
    """
    with harness.session_factory() as session:
        for record_id, source in ((UUID(int=2), "second-by-id"), (UUID(int=1), "first-by-id")):
            session.execute(
                harness.tables.restriction_records.insert().values(
                    record_id=record_id,
                    subject_id="alice",
                    purpose=None,
                    restricted=True,
                    reason=None,
                    recorded_at=T1,
                    source=source,
                )
            )
        session.commit()
        history = harness.ledger.history(session, "alice")
    assert [record.source for record in history] == ["first-by-id", "second-by-id"]
