"""The consent ledger derives status, keeps history, and mirrors audit events."""

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
    ConsentLedger,
    ConsentRecord,
    EffacedTables,
    bind_tables,
)


class LedgerHarness(NamedTuple):
    """A ledger wired to an in-memory database and a recording sink."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    sink: RecordingAuditSink
    ledger: ConsentLedger


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
    """A consent ledger on a fresh in-memory SQLite database."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    sink = RecordingAuditSink()
    yield LedgerHarness(
        session_factory=sessionmaker(engine),
        tables=tables,
        sink=sink,
        ledger=ConsentLedger(tables.consent_records, sink),
    )
    engine.dispose()


def consent(
    subject: str,
    purpose: str,
    *,
    granted: bool,
    at: datetime,
    source: str | None = None,
) -> ConsentRecord:
    return ConsentRecord(
        subject_id=subject,
        purpose=purpose,
        policy_version="2026-01",
        granted=granted,
        recorded_at=at,
        source=source,
    )


T1 = datetime(2026, 1, 1, 10, 0)
T2 = datetime(2026, 1, 1, 11, 0)
T3 = datetime(2026, 1, 1, 12, 0)


def test_status_false_when_no_records(harness: LedgerHarness) -> None:
    with harness.session_factory() as session:
        assert harness.ledger.status(session, "alice", "newsletter") is False


def test_status_grant_then_withdraw_is_false(harness: LedgerHarness) -> None:
    with harness.session_factory() as session:
        harness.ledger.record(session, consent("alice", "newsletter", granted=True, at=T1))
        harness.ledger.record(session, consent("alice", "newsletter", granted=False, at=T2))
        session.commit()
        assert harness.ledger.status(session, "alice", "newsletter") is False


def test_status_withdraw_then_grant_is_true(harness: LedgerHarness) -> None:
    with harness.session_factory() as session:
        harness.ledger.record(session, consent("alice", "newsletter", granted=False, at=T1))
        harness.ledger.record(session, consent("alice", "newsletter", granted=True, at=T2))
        session.commit()
        assert harness.ledger.status(session, "alice", "newsletter") is True


def test_status_independent_per_purpose(harness: LedgerHarness) -> None:
    with harness.session_factory() as session:
        harness.ledger.record(session, consent("alice", "newsletter", granted=True, at=T1))
        harness.ledger.record(session, consent("alice", "analytics", granted=True, at=T1))
        harness.ledger.record(session, consent("alice", "analytics", granted=False, at=T2))
        session.commit()
        assert harness.ledger.status(session, "alice", "newsletter") is True
        assert harness.ledger.status(session, "alice", "analytics") is False


def test_no_cross_subject_bleed(harness: LedgerHarness) -> None:
    alice = consent("alice", "newsletter", granted=True, at=T1)
    with harness.session_factory() as session:
        harness.ledger.record(session, alice)
        harness.ledger.record(session, consent("bob", "newsletter", granted=False, at=T2))
        session.commit()
        assert harness.ledger.status(session, "alice", "newsletter") is True
        assert harness.ledger.status(session, "bob", "newsletter") is False
        assert harness.ledger.history(session, "alice") == (alice,)


@pytest.mark.parametrize("grant_record_id", [UUID(int=1), UUID(int=2)])
def test_status_tie_resolves_to_withdrawn(harness: LedgerHarness, grant_record_id: UUID) -> None:
    """Equal ``recorded_at`` resolves to the withdrawal, whatever the record ids."""
    withdraw_record_id = UUID(int=2) if grant_record_id == UUID(int=1) else UUID(int=1)
    common = {
        "subject_id": "alice",
        "purpose": "newsletter",
        "policy_version": "2026-01",
        "recorded_at": T1,
        "source": None,
    }
    with harness.session_factory() as session:
        session.execute(
            harness.tables.consent_records.insert(),
            [
                {"record_id": grant_record_id, "granted": True, **common},
                {"record_id": withdraw_record_id, "granted": False, **common},
            ],
        )
        session.commit()
        first = harness.ledger.status(session, "alice", "newsletter")
        second = harness.ledger.status(session, "alice", "newsletter")
    assert first is False
    assert second is first


def test_history_empty_returns_empty_tuple(harness: LedgerHarness) -> None:
    with harness.session_factory() as session:
        assert harness.ledger.history(session, "alice") == ()


def test_history_oldest_first_and_unredacted(harness: LedgerHarness) -> None:
    records = (
        consent("alice", "newsletter", granted=True, at=T1, source="signup-form"),
        consent("alice", "newsletter", granted=False, at=T2, source=None),
        consent("alice", "newsletter", granted=True, at=T3, source="preferences-page"),
    )
    with harness.session_factory() as session:
        for record in (records[2], records[0], records[1]):  # insert out of order
            harness.ledger.record(session, record)
        session.commit()
        assert harness.ledger.history(session, "alice") == records


def test_history_spans_all_purposes_of_subject(harness: LedgerHarness) -> None:
    records = (
        consent("alice", "newsletter", granted=True, at=T1),
        consent("alice", "analytics", granted=True, at=T2),
        consent("alice", "newsletter", granted=False, at=T3),
    )
    with harness.session_factory() as session:
        for record in records:
            harness.ledger.record(session, record)
        session.commit()
        assert harness.ledger.history(session, "alice") == records


def test_record_mirrors_grant_event(harness: LedgerHarness) -> None:
    with harness.session_factory() as session:
        harness.ledger.record(session, consent("alice", "newsletter", granted=True, at=T1))
        session.commit()
    (event,) = harness.sink.events
    assert event.event_type is AuditEventType.CONSENT_GRANTED
    assert event.subject_ref == "alice"
    assert event.occurred_at == T1


def test_record_mirrors_withdrawal_event_with_unique_ids(harness: LedgerHarness) -> None:
    with harness.session_factory() as session:
        harness.ledger.record(session, consent("alice", "newsletter", granted=True, at=T1))
        harness.ledger.record(session, consent("alice", "newsletter", granted=False, at=T2))
        session.commit()
    grant, withdrawal = harness.sink.events
    assert withdrawal.event_type is AuditEventType.CONSENT_WITHDRAWN
    assert withdrawal.occurred_at == T2
    assert grant.event_id != withdrawal.event_id


def test_audit_payload_is_exactly_purpose_and_policy_version(harness: LedgerHarness) -> None:
    """Events carry refs + policy_version only — never the source or other PII."""
    with harness.session_factory() as session:
        harness.ledger.record(
            session,
            consent("alice", "newsletter", granted=True, at=T1, source="alice@example.com"),
        )
        session.commit()
    (event,) = harness.sink.events
    assert event.payload == {"purpose": "newsletter", "policy_version": "2026-01"}


def test_record_visible_in_callers_open_transaction(harness: LedgerHarness) -> None:
    with harness.session_factory() as session:
        harness.ledger.record(session, consent("alice", "newsletter", granted=True, at=T1))
        assert harness.ledger.status(session, "alice", "newsletter") is True  # not yet committed


def test_rollback_discards_consent_record(harness: LedgerHarness) -> None:
    with harness.session_factory() as session:
        harness.ledger.record(session, consent("alice", "newsletter", granted=True, at=T1))
        session.rollback()
        assert harness.ledger.status(session, "alice", "newsletter") is False
        assert harness.ledger.history(session, "alice") == ()


def test_failing_sink_propagates_before_commit(harness: LedgerHarness) -> None:
    """A failed audit append raises out of record(); nothing persists after rollback."""
    ledger = ConsentLedger(harness.tables.consent_records, ExplodingSink())
    with harness.session_factory() as session:
        with pytest.raises(RuntimeError, match="audit storage unavailable"):
            ledger.record(session, consent("alice", "newsletter", granted=True, at=T1))
        session.rollback()
        assert ledger.status(session, "alice", "newsletter") is False
        assert ledger.history(session, "alice") == ()
