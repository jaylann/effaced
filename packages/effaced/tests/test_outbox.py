"""Outbox.enqueue persists entries inside the caller's open transaction."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import NamedTuple
from uuid import UUID

import pytest
from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    Correction,
    EffacedTables,
    Outbox,
    OutboxEntry,
    OutboxOperation,
    OutboxStatus,
    PiiCategory,
    SubjectRef,
    bind_tables,
)


class OutboxHarness(NamedTuple):
    """An outbox wired to a fresh in-memory database."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    outbox: Outbox


@pytest.fixture()
def harness() -> Iterator[OutboxHarness]:
    """An outbox on a fresh in-memory SQLite database."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    yield OutboxHarness(
        session_factory=session_factory,
        tables=tables,
        outbox=Outbox(session_factory, tables.outbox),
    )
    engine.dispose()


ENQUEUED_AT = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def entry(
    entry_id: UUID,
    *,
    resolver: str = "stripe",
    extra: dict[str, str] | None = None,
) -> OutboxEntry:
    return OutboxEntry(
        entry_id=entry_id,
        subject_id="1",
        resolver=resolver,
        ref=SubjectRef(kind="stripe_customer", value="cus_123", extra=extra or {}),
        enqueued_at=ENQUEUED_AT,
    )


def stored_rows(harness: OutboxHarness) -> list[dict[str, object]]:
    columns = harness.tables.outbox.c
    with harness.session_factory() as session:
        statement = select(harness.tables.outbox).order_by(columns.entry_id)
        return [dict(row) for row in session.execute(statement).mappings()]


def test_enqueued_entries_are_visible_in_the_callers_transaction(
    harness: OutboxHarness,
) -> None:
    with harness.session_factory() as session:
        harness.outbox.enqueue(session, [entry(UUID(int=1))])
        count = session.execute(select(harness.tables.outbox)).all()
        assert len(count) == 1


def test_rollback_discards_enqueued_entries(harness: OutboxHarness) -> None:
    """enqueue never commits — the caller's rollback takes the entries with it."""
    with harness.session_factory() as session:
        harness.outbox.enqueue(session, [entry(UUID(int=1))])
        session.rollback()
    assert stored_rows(harness) == []


def test_commit_persists_flattened_entries(harness: OutboxHarness) -> None:
    with harness.session_factory() as session:
        harness.outbox.enqueue(
            session,
            [entry(UUID(int=1), extra={"account": "acct_9"}), entry(UUID(int=2), resolver="crm")],
        )
        session.commit()
    first, second = stored_rows(harness)
    assert first["entry_id"] == UUID(int=1)
    assert first["subject_id"] == "1"
    assert first["resolver"] == "stripe"
    assert first["ref_kind"] == "stripe_customer"
    assert first["ref_value"] == "cus_123"
    assert first["ref_extra"] == {"account": "acct_9"}
    assert first["status"] == OutboxStatus.PENDING.value
    assert first["attempts"] == 0
    assert first["last_attempt_at"] is None
    assert first["next_attempt_at"] is None
    assert first["last_error"] is None
    assert second["resolver"] == "crm"
    assert second["ref_extra"] == {}


def test_enqueue_of_nothing_is_a_no_op(harness: OutboxHarness) -> None:
    with harness.session_factory() as session:
        harness.outbox.enqueue(session, [])
        session.commit()
    assert stored_rows(harness) == []


def test_lifecycle_fields_round_trip_non_default_values(harness: OutboxHarness) -> None:
    """status/attempts/timestamps/error persist as given, not as column defaults."""
    attempted = OutboxEntry(
        entry_id=UUID(int=7),
        subject_id="1",
        resolver="stripe",
        ref=SubjectRef(kind="stripe_customer", value="cus_123"),
        status=OutboxStatus.FAILED,
        attempts=3,
        enqueued_at=ENQUEUED_AT,
        last_attempt_at=datetime(2026, 6, 2, 8, 30, tzinfo=UTC),
        last_error="resolver timed out",
    )
    with harness.session_factory() as session:
        harness.outbox.enqueue(session, [attempted])
        session.commit()
    (row,) = stored_rows(harness)
    assert row["subject_id"] == "1"
    assert row["status"] == OutboxStatus.FAILED.value
    assert row["attempts"] == 3
    assert row["enqueued_at"] == ENQUEUED_AT.replace(tzinfo=None)
    assert row["last_attempt_at"] == datetime(2026, 6, 2, 8, 30)
    assert row["last_error"] == "resolver timed out"


def test_claim_batch_default_limit_is_fifty(harness: OutboxHarness) -> None:
    """One default claim takes at most 50 entries, oldest first."""
    entries = [entry(UUID(int=n)) for n in range(51)]
    with harness.session_factory() as session:
        harness.outbox.enqueue(session, entries)
        session.commit()

    claimed = harness.outbox.claim_batch()

    assert len(claimed) == 50
    assert {e.entry_id for e in claimed} == {UUID(int=n) for n in range(50)}


# --- operation & corrections payload (ADR 0013) ------------------------------

CORRECTIONS = (
    Correction(category=PiiCategory.CONTACT, value="new@example.com"),
    Correction(category=PiiCategory.IDENTITY, value="New Name"),
)


def rectify_entry(entry_id: UUID, *, subject_id: str = "1") -> OutboxEntry:
    return OutboxEntry(
        entry_id=entry_id,
        subject_id=subject_id,
        resolver="stripe",
        ref=SubjectRef(kind="stripe", value="cus_123"),
        operation=OutboxOperation.RECTIFY,
        corrections=CORRECTIONS,
        enqueued_at=ENQUEUED_AT,
    )


def test_operation_defaults_to_erase_and_round_trips(harness: OutboxHarness) -> None:
    with harness.session_factory() as session:
        harness.outbox.enqueue(session, [entry(UUID(int=1))])
        session.commit()
    (row,) = stored_rows(harness)
    assert row["operation"] == "erase"
    assert row["payload"] is None
    (claimed,) = harness.outbox.claim_batch()
    assert claimed.operation is OutboxOperation.ERASE
    assert claimed.corrections == ()


def test_corrections_round_trip_through_storage_and_claim(harness: OutboxHarness) -> None:
    with harness.session_factory() as session:
        harness.outbox.enqueue(session, [rectify_entry(UUID(int=1))])
        session.commit()
    (row,) = stored_rows(harness)
    assert row["operation"] == "rectify"
    assert row["payload"] == {
        "corrections": [
            {"category": "contact", "value": "new@example.com"},
            {"category": "identity", "value": "New Name"},
        ]
    }
    (claimed,) = harness.outbox.claim_batch()
    assert claimed.operation is OutboxOperation.RECTIFY
    assert claimed.corrections == CORRECTIONS


def test_succeeded_clears_the_payload(harness: OutboxHarness) -> None:
    """A terminal row never retains corrected values (ADR 0013)."""
    with harness.session_factory() as session:
        harness.outbox.enqueue(session, [rectify_entry(UUID(int=1))])
        session.commit()
    (claimed,) = harness.outbox.claim_batch()
    harness.outbox.mark_succeeded(claimed, on_subject_complete=lambda: None)
    (row,) = stored_rows(harness)
    assert row["status"] == OutboxStatus.SUCCEEDED.value
    assert row["payload"] is None


def test_abandoned_clears_the_payload(harness: OutboxHarness) -> None:
    with harness.session_factory() as session:
        harness.outbox.enqueue(session, [rectify_entry(UUID(int=1))])
        session.commit()
    (claimed,) = harness.outbox.claim_batch()
    harness.outbox.mark_abandoned(claimed, error="ResolverError")
    (row,) = stored_rows(harness)
    assert row["status"] == OutboxStatus.ABANDONED.value
    assert row["payload"] is None


def test_failed_keeps_the_payload_for_the_retry(harness: OutboxHarness) -> None:
    with harness.session_factory() as session:
        harness.outbox.enqueue(session, [rectify_entry(UUID(int=1))])
        session.commit()
    (claimed,) = harness.outbox.claim_batch()
    harness.outbox.mark_failed(claimed, error="TimeoutError", next_attempt_at=ENQUEUED_AT)
    (row,) = stored_rows(harness)
    assert row["status"] == OutboxStatus.FAILED.value
    assert row["payload"] is not None
    (reclaimed,) = harness.outbox.claim_batch()
    assert reclaimed.corrections == CORRECTIONS


def test_completion_is_scoped_per_subject_and_operation(harness: OutboxHarness) -> None:
    """An open erase entry does not block rectify completion, and vice versa."""
    with harness.session_factory() as session:
        harness.outbox.enqueue(session, [entry(UUID(int=1)), rectify_entry(UUID(int=2))])
        session.commit()
    completions: list[str] = []
    rectify_done = rectify_entry(UUID(int=2))
    harness.outbox.mark_succeeded(
        rectify_done, on_subject_complete=lambda: completions.append("rectify")
    )
    assert completions == ["rectify"]  # the pending erase sibling is invisible here
    erase_done = entry(UUID(int=1))
    harness.outbox.mark_succeeded(
        erase_done, on_subject_complete=lambda: completions.append("erase")
    )
    assert completions == ["rectify", "erase"]
