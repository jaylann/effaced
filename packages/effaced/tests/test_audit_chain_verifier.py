"""Tests for AuditChainVerifier — detection of out-of-band row modifications.

The chain is an INSERTION-ORDER linked list (each row chains to the current
tail via ``prior_hash = tail.event_hash``); the verifier walks it from genesis
(``prior_hash IS NULL``) following predecessor pointers, never ordering by
``occurred_at``.

Proves: clean chain verifies; empty trail verifies; tamper at row K is
localized to K; tamper at the first chained row is reported; unchained-prefix
rows are skipped; an all-NULL-hash trail verifies vacuously; a backdated
append still verifies (and a tamper on it is still localized); a fork (two
rows sharing a predecessor) and a missing genesis are detected;
ChainVerification's cross-field validator rejects impossible states.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    AuditChainVerifier,
    AuditEvent,
    AuditEventType,
    ChainVerification,
    DatabaseAuditSink,
    EffacedTables,
    bind_tables,
    compute_event_hash,
)


class SinkHarness(NamedTuple):
    """A sink + verifier wired to a fresh in-memory database."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    sink: DatabaseAuditSink
    verifier: AuditChainVerifier


@pytest.fixture()
def harness() -> Iterator[SinkHarness]:
    """Database audit sink and chain verifier on a fresh in-memory SQLite database."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    factory = sessionmaker(engine)
    yield SinkHarness(
        session_factory=factory,
        tables=tables,
        sink=DatabaseAuditSink(factory, tables.audit_events),
        verifier=AuditChainVerifier(factory, tables.audit_events),
    )
    engine.dispose()


def _event(
    *,
    at: datetime,
    subject: str = "subject-1",
    event_type: AuditEventType = AuditEventType.CONSENT_GRANTED,
    payload: dict[str, str | int | bool] | None = None,
    event_id: UUID | None = None,
) -> AuditEvent:
    return AuditEvent(
        event_id=event_id if event_id is not None else uuid4(),
        event_type=event_type,
        subject_ref=subject,
        occurred_at=at,
        payload=payload if payload is not None else {},
    )


def _ts(offset_seconds: int) -> datetime:
    # Timezone-aware (UTC). compute_event_hash normalizes occurred_at to the
    # UTC instant (see _canonical_occurred_at), so the digest is identical
    # whether SQLite returns the column naive (it drops tzinfo on read-back)
    # or psycopg returns it aware — append-time and verify-time agree either
    # way. test_verifies_after_timezone_aware_append_survives_naive_readback
    # is the explicit regression for that round-trip.
    return datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC) + timedelta(seconds=offset_seconds)


# ---------------------------------------------------------------------------
# ChainVerification model invariant
# ---------------------------------------------------------------------------


def test_chain_verification_rejects_verified_with_break_id() -> None:
    with pytest.raises(ValidationError, match="verified chain cannot name"):
        ChainVerification(verified=True, first_broken_event_id=uuid4())


def test_chain_verification_rejects_unverified_without_break_id() -> None:
    with pytest.raises(ValidationError, match="unverified chain must name"):
        ChainVerification(verified=False, first_broken_event_id=None)


# ---------------------------------------------------------------------------
# Empty trail
# ---------------------------------------------------------------------------


def test_empty_trail_verifies_true(harness: SinkHarness) -> None:
    result = harness.verifier.verify()
    assert result.verified is True
    assert result.first_broken_event_id is None


# ---------------------------------------------------------------------------
# Clean chain
# ---------------------------------------------------------------------------


def test_five_events_verify_clean(harness: SinkHarness) -> None:
    for i in range(5):
        harness.sink.append(_event(at=_ts(i)))
    result = harness.verifier.verify()
    assert result.verified is True
    assert result.first_broken_event_id is None


def test_verifies_after_timezone_aware_append_survives_naive_readback(
    harness: SinkHarness,
) -> None:
    """Regression: aware-UTC append must verify after SQLite's naive read-back.

    SQLite's DateTime(timezone=True) column silently drops tzinfo on read-back,
    so the sink hashes an aware occurred_at at append time while the verifier
    reads it back naive. Before _canonical_occurred_at normalized the instant,
    isoformat() diverged by a ``+00:00`` suffix and every row looked tampered.
    This appends explicitly timezone-aware (UTC) events and asserts the chain
    still verifies clean — the exact round-trip that bug touched.
    """
    aware_times = [
        datetime(2026, 3, 1, 9, 0, 0, tzinfo=UTC) + timedelta(minutes=m) for m in range(4)
    ]
    for at in aware_times:
        appended = _event(at=at)
        assert appended.occurred_at.tzinfo is not None  # appended aware
        harness.sink.append(appended)

    result = harness.verifier.verify()
    assert result.verified is True
    assert result.first_broken_event_id is None


# ---------------------------------------------------------------------------
# Tamper in the middle
# ---------------------------------------------------------------------------


def test_tamper_at_middle_row_localizes_break(harness: SinkHarness) -> None:
    """Out-of-band UPDATE of event K's subject_ref is detected at K, not masked."""
    events = [_event(at=_ts(i)) for i in range(5)]
    for e in events:
        harness.sink.append(e)

    # tamper row at index 2 (K in the middle) via SQLAlchemy bound update so
    # UUID coercion works correctly on SQLite (text() binds pass str with dashes
    # which SQLite stores without, so the WHERE clause silently matches nothing)
    tampered_event = events[2]
    tbl = harness.tables.audit_events
    with harness.session_factory.begin() as session:
        session.execute(
            tbl.update()
            .where(tbl.c.event_id == tampered_event.event_id)
            .values(subject_ref="tampered")
        )

    result = harness.verifier.verify()
    assert result.verified is False
    assert result.first_broken_event_id == tampered_event.event_id


def test_tamper_at_middle_does_not_mask_the_first_break(harness: SinkHarness) -> None:
    """Row K's break is reported; rows after K are not separately reported."""
    events = [_event(at=_ts(i)) for i in range(5)]
    for e in events:
        harness.sink.append(e)

    # tamper row index 1 (early, not first)
    tampered_event = events[1]
    tbl = harness.tables.audit_events
    with harness.session_factory.begin() as session:
        session.execute(
            tbl.update()
            .where(tbl.c.event_id == tampered_event.event_id)
            .values(payload={"injected": "value"})
        )

    result = harness.verifier.verify()
    assert result.verified is False
    assert result.first_broken_event_id == tampered_event.event_id


# ---------------------------------------------------------------------------
# Tamper at the first chained event
# ---------------------------------------------------------------------------


def test_tamper_at_first_event_reports_first_event(harness: SinkHarness) -> None:
    events = [_event(at=_ts(i)) for i in range(3)]
    for e in events:
        harness.sink.append(e)

    first_event = events[0]
    tbl = harness.tables.audit_events
    with harness.session_factory.begin() as session:
        session.execute(
            tbl.update()
            .where(tbl.c.event_id == first_event.event_id)
            .values(subject_ref="tampered")
        )

    result = harness.verifier.verify()
    assert result.verified is False
    assert result.first_broken_event_id == first_event.event_id


# ---------------------------------------------------------------------------
# Unchained prefix
# ---------------------------------------------------------------------------


def test_unchained_prefix_rows_are_skipped_and_chain_verifies(harness: SinkHarness) -> None:
    """Legacy rows with NULL hashes are skipped; subsequent chained rows verify."""
    tbl = harness.tables.audit_events
    with harness.session_factory.begin() as session:
        # insert two legacy rows without hashes
        for i in range(2):
            session.execute(
                tbl.insert().values(
                    event_id=uuid4(),
                    event_type=AuditEventType.CONSENT_GRANTED.value,
                    subject_ref="subject-legacy",
                    occurred_at=_ts(i),
                    payload={},
                    prior_hash=None,
                    event_hash=None,
                )
            )

    # append real chained events after the unchained prefix
    for i in range(3):
        harness.sink.append(_event(at=_ts(10 + i)))

    result = harness.verifier.verify()
    assert result.verified is True
    assert result.first_broken_event_id is None


def test_entirely_null_hash_trail_verifies_vacuously(harness: SinkHarness) -> None:
    """A trail that is entirely unchained (all NULL event_hash) verifies True."""
    tbl = harness.tables.audit_events
    with harness.session_factory.begin() as session:
        for i in range(3):
            session.execute(
                tbl.insert().values(
                    event_id=uuid4(),
                    event_type=AuditEventType.CONSENT_GRANTED.value,
                    subject_ref="subject-legacy",
                    occurred_at=_ts(i),
                    payload={},
                    prior_hash=None,
                    event_hash=None,
                )
            )

    result = harness.verifier.verify()
    assert result.verified is True
    assert result.first_broken_event_id is None


# ---------------------------------------------------------------------------
# Backdated append (insertion-keyed chain, not occurred_at-keyed)
# ---------------------------------------------------------------------------


def test_backdated_append_verifies_clean(harness: SinkHarness) -> None:
    """A later-inserted event with an earlier occurred_at still verifies.

    Minimal reproduction of the reviewer's false alarm: append A at t2, then B
    at t1 < t2. The sink chains B.prior_hash = A.event_hash (insertion order).
    The old occurred_at-ordered verifier read them as [B, A] and reported a
    break; the linked-list verifier walks A→B and verifies clean.
    """
    a = _event(at=_ts(120), subject="subject-a")
    b = _event(at=_ts(60), subject="subject-b")  # earlier instant, later insert
    harness.sink.append(a)
    harness.sink.append(b)

    result = harness.verifier.verify()
    assert result.verified is True
    assert result.first_broken_event_id is None


def test_backdated_append_then_tamper_breaks_at_tampered_row(harness: SinkHarness) -> None:
    """A tamper on the backdated row is localized to that row.

    Insertion order is A then B (B backdated); tampering B's content makes B's
    recomputed hash mismatch, so B is named — occurred_at order is irrelevant.
    """
    a = _event(at=_ts(120), subject="subject-a")
    b = _event(at=_ts(60), subject="subject-b")
    harness.sink.append(a)
    harness.sink.append(b)

    tbl = harness.tables.audit_events
    with harness.session_factory.begin() as session:
        session.execute(
            tbl.update().where(tbl.c.event_id == b.event_id).values(subject_ref="tampered")
        )

    result = harness.verifier.verify()
    assert result.verified is False
    assert result.first_broken_event_id == b.event_id


# ---------------------------------------------------------------------------
# Fork and orphan / missing-genesis structural breaks
# ---------------------------------------------------------------------------


def _insert_chained_row(harness: SinkHarness, event: AuditEvent, prior_hash: str | None) -> str:
    """Insert one chained row with a valid event_hash, returning that hash.

    A test-only raw insert (the sink never forks); the event_hash is computed
    exactly as the sink would so the row is content-valid — the break under
    test is the chain *structure* (fork / missing genesis), not a content
    mismatch.
    """
    event_hash = compute_event_hash(event, prior_hash)
    tbl = harness.tables.audit_events
    with harness.session_factory.begin() as session:
        session.execute(
            tbl.insert().values(
                event_id=event.event_id,
                event_type=event.event_type.value,
                subject_ref=event.subject_ref,
                occurred_at=event.occurred_at,
                payload=dict(event.payload),
                prior_hash=prior_hash,
                event_hash=event_hash,
            )
        )
    return event_hash


def test_fork_two_events_share_a_predecessor_is_detected(harness: SinkHarness) -> None:
    """Two rows citing the same predecessor (a fork) fail verification.

    Build A as genesis, then C and D that BOTH set prior_hash = A.event_hash,
    each content-valid. The walk reaches A, finds two children, and reports the
    fork — the named break is one of the forked children.
    """
    a = _event(at=_ts(0), subject="subject-a")
    a_hash = _insert_chained_row(harness, a, None)

    c = _event(at=_ts(1), subject="subject-c")
    d = _event(at=_ts(2), subject="subject-d")
    _insert_chained_row(harness, c, a_hash)
    _insert_chained_row(harness, d, a_hash)

    result = harness.verifier.verify()
    assert result.verified is False
    assert result.first_broken_event_id in {c.event_id, d.event_id}


def test_chain_with_no_genesis_is_detected(harness: SinkHarness) -> None:
    """Chained rows with no genesis (no prior_hash IS NULL row) fail.

    Both rows cite a predecessor that is not present as a genesis, so the list
    is unreachable from a head — an orphan/missing-genesis structural break.
    """
    # Two rows, neither a genesis: each points at a hash that has no NULL-prior
    # row. They form a chain fragment detached from any head.
    fake_head_hash = "0" * 64
    x = _event(at=_ts(0), subject="subject-x")
    x_hash = _insert_chained_row(harness, x, fake_head_hash)
    y = _event(at=_ts(1), subject="subject-y")
    _insert_chained_row(harness, y, x_hash)

    result = harness.verifier.verify()
    assert result.verified is False
    assert result.first_broken_event_id is not None
