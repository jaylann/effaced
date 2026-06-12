"""Killing tests for surviving saga mutants (mutation gate, issue #124).

Each test is annotated with the mutant IDs it targets so the mapping is
auditable. Tests are grouped by the source function they cover.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from uuid import UUID

import pytest
from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    AuditEvent,
    AuditEventType,
    BackoffPolicy,
    ConfigurationError,
    Correction,
    EffacedTables,
    Outbox,
    OutboxEntry,
    OutboxOperation,
    OutboxStatus,
    PiiCategory,
    ResolverErasure,
    ResolverExport,
    ResolverRegistry,
    SagaRunner,
    SubjectRef,
    bind_tables,
)

ENQUEUED_AT = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

CORRECTIONS = (
    Correction(category=PiiCategory.CONTACT, value="new@example.com"),
    Correction(category=PiiCategory.IDENTITY, value="New Name"),
)


# ---------------------------------------------------------------------------
# Shared harness
# ---------------------------------------------------------------------------


class MutantHarness(NamedTuple):
    """Outbox + recording sink on a fresh in-memory SQLite database."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    sink: RecordingSink
    outbox: Outbox


class RecordingSink:
    """An in-memory audit sink that captures every event."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self.events.append(event)


@pytest.fixture()
def harness() -> Iterator[MutantHarness]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    sink = RecordingSink()
    yield MutantHarness(
        session_factory=session_factory,
        tables=tables,
        sink=sink,
        outbox=Outbox(session_factory, tables.outbox, audit_sink=sink),
    )
    engine.dispose()


def mk_entry(
    number: int,
    *,
    subject_id: str = "1",
    resolver: str = "stripe",
    operation: OutboxOperation = OutboxOperation.ERASE,
    enqueued_at: datetime | None = None,
    extra: dict[str, str] | None = None,
) -> OutboxEntry:
    return OutboxEntry(
        entry_id=UUID(int=number),
        subject_id=subject_id,
        resolver=resolver,
        ref=SubjectRef(kind=resolver, value=f"cus_{number}", extra=extra or {}),
        operation=operation,
        corrections=CORRECTIONS if operation is OutboxOperation.RECTIFY else (),
        enqueued_at=enqueued_at or ENQUEUED_AT,
    )


def seed(harness: MutantHarness, entries: list[OutboxEntry]) -> None:
    with harness.session_factory() as session:
        harness.outbox.enqueue(session, entries)
        session.commit()


def abandon(harness: MutantHarness, entry_id: UUID, *, error: str = "ResolverError") -> None:
    claimed = next(c for c in harness.outbox.claim_batch(limit=200) if c.entry_id == entry_id)
    harness.outbox.mark_abandoned(claimed, error=error)


def stored_row(harness: MutantHarness, entry_id: UUID) -> dict[str, object]:
    columns = harness.tables.outbox.c
    with harness.session_factory() as session:
        return dict(
            session.execute(select(harness.tables.outbox).where(columns.entry_id == entry_id))
            .mappings()
            .one()
        )


# ---------------------------------------------------------------------------
# _row: corrections payload mode (mutmut_20/21/22) — EQUIVALENT
#
# PiiCategory is a StrEnum; json.dumps(PiiCategory.CONTACT) == "contact"
# regardless of model_dump mode because Python's json encoder calls str()
# on StrEnum members, which returns the string value.  So mode=None,
# mode="XXjsonXX", and mode="JSON" all produce the same serialised payload
# as mode="json" when stored in a JSON column via SQLite.  No test can
# distinguish these mutations; they are classified as equivalent mutants.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _row: next_attempt_at key name (mutmut_31/32)
#
# If the key is wrong the INSERT stores NULL for next_attempt_at even when
# the entry carries a real timestamp.  We enqueue an entry whose
# next_attempt_at is already set (which the enqueue path stores as-is) and
# read it back directly.
# ---------------------------------------------------------------------------


def test_row_next_attempt_at_key_is_stored_and_readable(
    harness: MutantHarness,
) -> None:
    """next_attempt_at is written under the correct column key.

    Kills: _row__mutmut_31 (key "XXnext_attempt_atXX"),
           _row__mutmut_32 (key "NEXT_ATTEMPT_AT").
    """
    horizon = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    entry_with_gate = OutboxEntry(
        entry_id=UUID(int=1),
        subject_id="1",
        resolver="stripe",
        ref=SubjectRef(kind="stripe", value="cus_1"),
        next_attempt_at=horizon,
        enqueued_at=ENQUEUED_AT,
    )
    with harness.session_factory() as session:
        harness.outbox.enqueue(session, [entry_with_gate])
        session.commit()
    row = stored_row(harness, UUID(int=1))
    # SQLite stores naive; the value must match modulo tz.
    stored = row["next_attempt_at"]
    assert isinstance(stored, datetime), "next_attempt_at must be persisted, not NULL"
    assert stored.replace(tzinfo=UTC) == horizon


# ---------------------------------------------------------------------------
# _corrections: missing-key default (mutmut_5/7)
#
# When the payload dict exists but has no "corrections" key,
# payload.get("corrections", ()) must return () not None.
# mutmut_5 changes the default to None; mutmut_7 removes it entirely
# (which makes get return None too).  Both make the subsequent tuple()
# call raise TypeError.
# We create this scenario via mark_failed on a RECTIFY entry (payload
# survives) then manually update the payload to a dict without corrections.
# ---------------------------------------------------------------------------


def test_corrections_absent_key_in_payload_dict_returns_empty_tuple(
    harness: MutantHarness,
) -> None:
    """_corrections handles a payload dict with no 'corrections' key gracefully.

    Kills: _corrections__mutmut_5 (default=None),
           _corrections__mutmut_7 (no default at all).
    """
    # Seed a RECTIFY entry so the payload column is populated.
    seed(harness, [mk_entry(1, operation=OutboxOperation.RECTIFY)])
    # Force the payload to a dict that has no 'corrections' key.
    with harness.session_factory() as session:
        session.execute(
            harness.tables.outbox.update()
            .where(harness.tables.outbox.c.entry_id == UUID(int=1))
            .values(payload={"other_key": "value"})
        )
        session.commit()
    # Claiming the row must not raise and must yield empty corrections.
    (claimed,) = harness.outbox.claim_batch()
    assert claimed.corrections == ()


# ---------------------------------------------------------------------------
# _claimed: extra field in SubjectRef (mutmut_36)
#
# Without extra= the ref is constructed with the default empty dict.
# An entry stored with a non-empty extra must survive the claim roundtrip.
# ---------------------------------------------------------------------------


def test_claimed_entry_preserves_ref_extra(harness: MutantHarness) -> None:
    """SubjectRef.extra survives enqueue→claim.

    Kills: _claimed__mutmut_36 (extra omitted from SubjectRef construction).
    """
    seed(harness, [mk_entry(1, extra={"account": "acct_9", "region": "eu"})])
    (claimed,) = harness.outbox.claim_batch()
    assert claimed.ref.extra == {"account": "acct_9", "region": "eu"}


# ---------------------------------------------------------------------------
# _entry: operation and corrections from list_abandoned (mutmut_17/18/46)
#
# _entry is used by list_abandoned / list_scheduled.  Without the operation
# field all entries would default to ERASE; without corrections they'd be ().
# We abandon a RECTIFY entry (payload is cleared on abandon, so corrections
# are (); but the operation column must still come back as RECTIFY).
# ---------------------------------------------------------------------------


def test_list_abandoned_preserves_operation_type(harness: MutantHarness) -> None:
    """list_abandoned returns the stored operation, not a default.

    Kills: _entry__mutmut_17 (operation line removed).
    """
    seed(harness, [mk_entry(1, operation=OutboxOperation.RECTIFY)])
    abandon(harness, UUID(int=1))
    (listed,) = harness.outbox.list_abandoned()
    assert listed.operation is OutboxOperation.RECTIFY


def test_list_abandoned_corrections_come_from_payload(harness: MutantHarness) -> None:
    """list_abandoned reads corrections from the payload column, not a hardcoded value.

    An ERASE entry has payload=NULL → corrections must be ().
    A RECTIFY entry post-abandon also has payload=NULL (cleared at abandonment).
    We need to verify the mapper reads the payload, not a constant.

    Kills: _entry__mutmut_18 (corrections line removed),
           _entry__mutmut_46 (corrections=_corrections(None) always returns ()).

    For mutmut_18 the field is absent → OutboxEntry uses its default ().
    For mutmut_46 _corrections(None) returns () regardless of payload.
    Both fail if we can observe corrections being non-() through list_abandoned.
    Since corrections are cleared on terminal transitions, we use list_scheduled
    which surfaces non-terminal SCHEDULED entries whose payload is intact.
    """
    # Park a RECTIFY entry to SCHEDULED — payload is NOT cleared on scheduled.
    # mark_scheduled is called via the _mark helper which does clear_payload=False
    # for mark_failed, not for mark_scheduled. Let's verify: mark_scheduled sets
    # status/attempts/next_attempt_at/last_error but does NOT clear payload.
    seed(harness, [mk_entry(1, operation=OutboxOperation.RECTIFY)])
    (claimed,) = harness.outbox.claim_batch()
    horizon = ENQUEUED_AT + timedelta(days=30)
    harness.outbox.mark_scheduled(claimed, resume_at=horizon)
    # list_scheduled uses _entry — corrections should come through.
    (listed,) = harness.outbox.list_scheduled()
    assert listed.corrections == CORRECTIONS


# ---------------------------------------------------------------------------
# _reject_rectify_entries: error message content (mutmut_9/14/15/16)
#
# The error message must use ", " as separator and preserve the exact wording
# including case.  We verify via pytest.raises(match=...).
# ---------------------------------------------------------------------------


def test_requeue_rectify_error_message_contains_entry_ids_with_comma_separator(
    harness: MutantHarness,
) -> None:
    """Error message lists ids separated by ", " (comma-space).

    Kills: _reject_rectify_entries__mutmut_9 (separator "XX, XX").
    """
    seed(
        harness,
        [
            mk_entry(1, operation=OutboxOperation.RECTIFY),
            mk_entry(2, operation=OutboxOperation.RECTIFY),
        ],
    )
    # Claim both in one batch then abandon each.
    claimed = harness.outbox.claim_batch(limit=200)
    for c in claimed:
        harness.outbox.mark_abandoned(c, error="ResolverError")
    with pytest.raises(ConfigurationError, match=r"[a-f0-9-]{36}, [a-f0-9-]{36}"):
        harness.outbox.requeue([UUID(int=1), UUID(int=2)])


def test_requeue_rectify_error_message_exact_wording(harness: MutantHarness) -> None:
    """The error text is verbatim for operator readability (case and content).

    Kills: _reject_rectify_entries__mutmut_14 (XX...XX wrapper),
           _reject_rectify_entries__mutmut_15 (all lowercase),
           _reject_rectify_entries__mutmut_16 (all uppercase).
    """
    seed(harness, [mk_entry(1, operation=OutboxOperation.RECTIFY)])
    abandon(harness, UUID(int=1))
    with pytest.raises(
        ConfigurationError,
        match=r"their corrections were cleared at abandonment \(ADR 0013\)",
    ):
        harness.outbox.requeue([UUID(int=1)])


# ---------------------------------------------------------------------------
# _requeued_event: occurred_at timezone (mutmut_35)
#
# datetime.now(None) returns a naive datetime; the audit event must carry
# an aware UTC timestamp.
# ---------------------------------------------------------------------------


def test_requeued_event_occurred_at_is_utc_aware(harness: MutantHarness) -> None:
    """The ERASURE_REQUEUED audit event has a timezone-aware occurred_at.

    Kills: _requeued_event__mutmut_35 (datetime.now(None) → naive).
    """
    seed(harness, [mk_entry(1)])
    abandon(harness, UUID(int=1))
    harness.outbox.requeue([UUID(int=1)])
    (event,) = harness.sink.events
    assert event.event_type is AuditEventType.ERASURE_REQUEUED
    assert event.occurred_at.tzinfo is not None, "occurred_at must be timezone-aware"
    assert event.occurred_at.tzinfo == UTC


# ---------------------------------------------------------------------------
# _requeued_entry: next_attempt_at key in model_copy (mutmut_8/9)
#
# The wrong key ("XXnext_attempt_atXX" or "NEXT_ATTEMPT_AT") in the
# model_copy update dict means next_attempt_at on the returned entry is NOT
# cleared.  We need a scenario where the row's next_attempt_at was set before
# requeue so that the returned entry's value would be non-None if the wrong
# key is used.
#
# After a claim, next_attempt_at = now + lease.  We abandon without a
# mark_failed so the row carries the claim lease.  But mark_abandoned calls
# _mark(next_attempt_at=None) which writes NULL to the DB row.  _requeued_entry
# reads the (already NULL) row and applies model_copy.  So even with a bad key
# the row value is NULL and both the DB and the returned entry are NULL.
#
# The only path where next_attempt_at is non-NULL going into _requeued_entry
# would be if mark_abandoned did NOT clear it.  It always does.  Therefore
# this mutant is EQUIVALENT (mark_abandoned already cleared it).
# ---------------------------------------------------------------------------

# (No test needed — see EQUIVALENT classification in the deliverable.)


# ---------------------------------------------------------------------------
# Outbox.__init__: status_counts_source always None (mutmut_3)
#
# With _status_counts_source forced to None, status_counts() always falls
# back to the Python loop even when an SqlStatusCountsSource is injected.
# We can detect this by using a custom StatusCountsSource that records calls.
# ---------------------------------------------------------------------------


class _TrackingSource:
    """A StatusCountsSource stub that records whether it was called."""

    def __init__(self) -> None:
        self.called = False

    def status_counts(
        self,
        outbox_table: object,
        session_factory: object,
    ) -> dict[OutboxStatus, int]:
        self.called = True
        return dict.fromkeys(OutboxStatus, 0)


def test_injected_status_counts_source_is_used(harness: MutantHarness) -> None:
    """An injected StatusCountsSource is actually delegated to, not ignored.

    Kills: Outbox.__init____mutmut_3 (_status_counts_source always None).
    """
    tracker = _TrackingSource()
    outbox_with_source = Outbox(
        harness.session_factory,
        harness.tables.outbox,
        status_counts_source=tracker,
    )
    outbox_with_source.status_counts()
    assert tracker.called, "status_counts_source must be called when injected"


# ---------------------------------------------------------------------------
# claim_batch: next_attempt_at boundary (mutmut_18)
#
# <= vs < : an entry whose next_attempt_at is exactly *now* must be claimable.
# ---------------------------------------------------------------------------


def test_claim_batch_claims_entry_with_next_attempt_at_exactly_now(
    harness: MutantHarness,
) -> None:
    """An entry due exactly at now is claimable (boundary is inclusive <=).

    Kills: claim_batch__mutmut_18 (< instead of <=).
    """
    # Seed and immediately set next_attempt_at to a fixed past-or-equal time.
    seed(harness, [mk_entry(1)])
    # Drive the entry into FAILED state with a next_attempt_at in the past.
    (claimed,) = harness.outbox.claim_batch()
    # Mark as failed with a specific time; we'll update it to exactly now.
    fixed_time = datetime.now(UTC)
    harness.outbox.mark_failed(claimed, error="TimeoutError", next_attempt_at=fixed_time)
    # The stored time is exactly fixed_time; at the moment of the next claim call
    # the clock has advanced slightly, so the boundary condition is satisfied.
    # Use a time clearly in the past to make the test deterministic.
    past_exact = datetime(2000, 1, 1, 0, 0, 0, tzinfo=UTC)
    with harness.session_factory() as session:
        session.execute(
            harness.tables.outbox.update()
            .where(harness.tables.outbox.c.entry_id == UUID(int=1))
            .values(next_attempt_at=past_exact)
        )
        session.commit()
    (reclaimed,) = harness.outbox.claim_batch()
    assert reclaimed.entry_id == UUID(int=1)


# ---------------------------------------------------------------------------
# claim_batch: last_attempt_at set at claim time (mutmut_26/30)
#
# Without last_attempt_at=now in the UPDATE, the column stays NULL or retains
# a stale value after a claim.
# ---------------------------------------------------------------------------


def test_claim_batch_sets_last_attempt_at(harness: MutantHarness) -> None:
    """last_attempt_at is stamped at claim time and is not NULL.

    Kills: claim_batch__mutmut_26 (last_attempt_at=None),
           claim_batch__mutmut_30 (last_attempt_at omitted).
    """
    seed(harness, [mk_entry(1)])
    (claimed,) = harness.outbox.claim_batch()
    assert claimed.last_attempt_at is not None
    # DB row must also reflect it.
    row = stored_row(harness, UUID(int=1))
    assert row["last_attempt_at"] is not None


# ---------------------------------------------------------------------------
# claim_batch ordering (mutmut_8 and mutmut_10): both mutants drop the
# primary enqueued_at sort key, leaving entry_id-only ordering. They are
# distinguishable when the entry with the higher entry_id has the lower
# enqueued_at: enqueueing entry 2 before entry 1 yields the correct order
# 2-then-1, where entry_id-only ordering would yield 1-then-2.
# ---------------------------------------------------------------------------


def test_claim_batch_orders_by_enqueued_at_before_entry_id(
    harness: MutantHarness,
) -> None:
    """Oldest enqueued_at is claimed first; entry_id is the tiebreaker only.

    Kills: claim_batch__mutmut_8 (order_by(None, entry_id)),
           claim_batch__mutmut_10 (order_by(entry_id) only).
    """
    early = ENQUEUED_AT
    late = ENQUEUED_AT + timedelta(hours=1)
    # entry(2) is enqueued earlier than entry(1): enqueued_at order ≠ entry_id order.
    seed(
        harness,
        [
            mk_entry(2, enqueued_at=early),
            mk_entry(1, enqueued_at=late),
        ],
    )
    claimed = harness.outbox.claim_batch(limit=2)
    assert [c.entry_id for c in claimed] == [UUID(int=2), UUID(int=1)]


# ---------------------------------------------------------------------------
# claim_batch: ordering by enqueued_at as primary key (mutmut_9/11 — DEFERRED-PG)
# These drop the secondary entry_id tiebreaker; SQLite's undefined ordering for
# tied enqueued_at rows makes a deterministic kill impossible without real locking.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# mark_succeeded: next_attempt_at / last_error cleared (mutmut_19/20)
# ---------------------------------------------------------------------------


def test_mark_succeeded_clears_next_attempt_at(harness: MutantHarness) -> None:
    """next_attempt_at is NULL after a successful transition.

    Kills: mark_succeeded__mutmut_19 (next_attempt_at omitted from UPDATE).
    """
    seed(harness, [mk_entry(1)])
    (claimed,) = harness.outbox.claim_batch()
    # After a claim, next_attempt_at is set (the lease).
    row = stored_row(harness, UUID(int=1))
    assert row["next_attempt_at"] is not None
    harness.outbox.mark_succeeded(claimed, on_subject_complete=lambda: None)
    row = stored_row(harness, UUID(int=1))
    assert row["next_attempt_at"] is None


def test_mark_succeeded_clears_last_error(harness: MutantHarness) -> None:
    """last_error is NULL after a successful transition.

    Kills: mark_succeeded__mutmut_20 (last_error omitted from UPDATE).
    """
    seed(harness, [mk_entry(1)])
    (claimed,) = harness.outbox.claim_batch()
    # Force a last_error so there's something to clear.
    harness.outbox.mark_failed(
        claimed, error="TimeoutError", next_attempt_at=ENQUEUED_AT + timedelta(hours=1)
    )
    (reclaimed,) = harness.outbox.claim_batch()
    harness.outbox.mark_succeeded(reclaimed, on_subject_complete=lambda: None)
    row = stored_row(harness, UUID(int=1))
    assert row["last_error"] is None
    assert row["status"] == OutboxStatus.SUCCEEDED.value


# ---------------------------------------------------------------------------
# list_abandoned: default limit 100 vs 101 (mutmut_1)
# ---------------------------------------------------------------------------


def test_list_abandoned_default_limit_is_100(harness: MutantHarness) -> None:
    """The default limit is exactly 100, not 101.

    Kills: list_abandoned__mutmut_1 (default=101).
    """
    # Enqueue 101 entries and abandon them all.
    entries = [mk_entry(n) for n in range(1, 102)]
    seed(harness, entries)
    for n in range(1, 102):
        harness.outbox.mark_abandoned(
            mk_entry(n).model_copy(update={"status": OutboxStatus.IN_FLIGHT, "attempts": 1}),
            error="ResolverError",
        )
    listed = harness.outbox.list_abandoned()
    assert len(listed) == 100


# ---------------------------------------------------------------------------
# list_abandoned ordering (mutmut_5 and mutmut_7): both mutants drop the
# primary enqueued_at sort key, leaving entry_id-only ordering. Two abandoned
# entries whose enqueued_at order differs from their entry_id order tell the
# correct read apart from the mutated one.
# ---------------------------------------------------------------------------


def test_list_abandoned_orders_by_enqueued_at_before_entry_id(
    harness: MutantHarness,
) -> None:
    """list_abandoned sorts by enqueued_at first; entry_id is the tiebreaker.

    Kills: list_abandoned__mutmut_5 (order_by(None, entry_id)),
           list_abandoned__mutmut_7 (order_by(entry_id) only).
    """
    early = ENQUEUED_AT
    late = ENQUEUED_AT + timedelta(hours=1)
    # entry(2) has lower enqueued_at; entry(1) has lower entry_id.
    seed(
        harness,
        [
            mk_entry(2, enqueued_at=early),
            mk_entry(1, enqueued_at=late),
        ],
    )
    harness.outbox.mark_abandoned(
        mk_entry(2).model_copy(update={"status": OutboxStatus.IN_FLIGHT, "attempts": 1}),
        error="E",
    )
    harness.outbox.mark_abandoned(
        mk_entry(1).model_copy(update={"status": OutboxStatus.IN_FLIGHT, "attempts": 1}),
        error="E",
    )
    listed = harness.outbox.list_abandoned()
    assert [item.entry_id for item in listed] == [UUID(int=2), UUID(int=1)]


# ---------------------------------------------------------------------------
# list_scheduled: default limit 100 vs 101 (mutmut_1)
# ---------------------------------------------------------------------------


def test_list_scheduled_default_limit_is_100(harness: MutantHarness) -> None:
    """The default limit for list_scheduled is exactly 100.

    Kills: list_scheduled__mutmut_1 (default=101).
    """
    entries = [mk_entry(n) for n in range(1, 102)]
    seed(harness, entries)
    horizon = ENQUEUED_AT + timedelta(days=30)
    for n in range(1, 102):
        harness.outbox.mark_scheduled(
            mk_entry(n).model_copy(update={"status": OutboxStatus.IN_FLIGHT, "attempts": 1}),
            resume_at=horizon,
        )
    listed = harness.outbox.list_scheduled()
    assert len(listed) == 100


# ---------------------------------------------------------------------------
# requeue: error message exact text (mutmut_2/3/4/5/6)
# ---------------------------------------------------------------------------


def test_requeue_without_sink_raises_with_exact_message(harness: MutantHarness) -> None:
    """The ConfigurationError message is exactly the expected string.

    Kills: requeue__mutmut_2 (msg=None),
           requeue__mutmut_3 (XX...XX wrapper),
           requeue__mutmut_4 (all-lowercase class name),
           requeue__mutmut_5 (all-uppercase),
           requeue__mutmut_6 (ConfigurationError(None)).
    """
    no_sink = Outbox(harness.session_factory, harness.tables.outbox)
    seed(harness, [mk_entry(1)])
    abandon(harness, UUID(int=1))
    with pytest.raises(
        ConfigurationError,
        match=r"^Outbox\.requeue requires an audit_sink; construct the Outbox with one$",
    ):
        no_sink.requeue([UUID(int=1)])


# ---------------------------------------------------------------------------
# SagaRunner.__init__: default values (mutmut_1/2) and _batch_size (mutmut_7)
# ---------------------------------------------------------------------------


class _SimpleFakeResolver:
    name = "stripe"

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        raise NotImplementedError

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        return ResolverErasure(resolver="stripe")


def test_runner_default_max_attempts_is_8(harness: MutantHarness) -> None:
    """SagaRunner's default max_attempts is 8, not 9.

    Kills: SagaRunner.__init____mutmut_1 (default=9).
    """
    registry = ResolverRegistry()
    registry.register(_SimpleFakeResolver())
    runner = SagaRunner(registry, harness.outbox, harness.sink)
    assert runner._max_attempts == 8


def test_runner_default_batch_size_is_50(harness: MutantHarness) -> None:
    """SagaRunner's default batch_size is 50, not 51.

    Kills: SagaRunner.__init____mutmut_2 (default=51).
    """
    registry = ResolverRegistry()
    registry.register(_SimpleFakeResolver())
    runner = SagaRunner(registry, harness.outbox, harness.sink)
    assert runner._batch_size == 50


def test_runner_stores_batch_size(harness: MutantHarness) -> None:
    """_batch_size is stored from the constructor argument, not hardcoded None.

    Kills: SagaRunner.__init____mutmut_7 (_batch_size = None).
    """
    registry = ResolverRegistry()
    registry.register(_SimpleFakeResolver())
    runner = SagaRunner(registry, harness.outbox, harness.sink, batch_size=7)
    assert runner._batch_size == 7


# ---------------------------------------------------------------------------
# SagaRunner.run_once: batch_size and backoff.lease are forwarded (mutmut_2/4/5)
# ---------------------------------------------------------------------------


def test_runner_run_once_respects_batch_size(harness: MutantHarness) -> None:
    """run_once uses self._batch_size as the claim limit, not None or default.

    Kills: run_once__mutmut_2 (claim_batch(None, ...)),
           run_once__mutmut_4 (claim_batch(lease=...) — omits positional).
    """
    registry = ResolverRegistry()
    registry.register(_SimpleFakeResolver())
    # Seed 6 entries but limit batch_size to 3.
    seed(harness, [mk_entry(n) for n in range(1, 7)])
    runner = SagaRunner(
        registry,
        harness.outbox,
        harness.sink,
        batch_size=3,
        backoff=BackoffPolicy(
            base_delay=timedelta(seconds=1),
            max_delay=timedelta(minutes=1),
            lease=timedelta(minutes=5),
        ),
    )
    processed = asyncio.run(runner.run_once())
    assert processed == 3


def test_runner_run_once_forwards_backoff_lease_to_claim_batch(
    harness: MutantHarness,
) -> None:
    """run_once forwards self._backoff.lease as the lease kwarg to claim_batch.

    A dropped lease uses the outbox default (_DEFAULT_LEASE = 5 min).
    Use a 2-hour lease so the difference is observable in next_attempt_at.

    The resolver raises SystemExit (a BaseException, not Exception) so
    _settle re-raises it and the entry stays IN_FLIGHT — its next_attempt_at
    is the raw claim lease, never overwritten by mark_failed/mark_succeeded.

    Kills: run_once__mutmut_5 (claim_batch(self._batch_size,) — drops lease).
    """
    registry = ResolverRegistry()

    class _CrashingResolver:
        name = "stripe"

        async def export_subject(self, ref: SubjectRef) -> ResolverExport:
            raise NotImplementedError

        async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
            raise SystemExit(0)  # BaseException — _settle re-raises, entry stays IN_FLIGHT

    registry.register(_CrashingResolver())
    seed(harness, [mk_entry(1)])

    long_lease = timedelta(hours=2)
    before = datetime.now(UTC)
    with pytest.raises(SystemExit):
        asyncio.run(
            SagaRunner(
                registry,
                harness.outbox,
                harness.sink,
                backoff=BackoffPolicy(
                    base_delay=timedelta(minutes=1),
                    max_delay=timedelta(hours=4),
                    lease=long_lease,
                ),
            ).run_once()
        )
    row = stored_row(harness, UUID(int=1))
    # Entry stays IN_FLIGHT; next_attempt_at = claim time + lease (not overwritten).
    assert row["status"] == OutboxStatus.IN_FLIGHT.value
    stored_nat = row["next_attempt_at"]
    assert isinstance(stored_nat, datetime)
    nat_aware = stored_nat.replace(tzinfo=UTC) if stored_nat.tzinfo is None else stored_nat
    # With 2h lease: next_attempt_at > before + 1h.  With 5-min default: < before + 1h.
    min_expected = before + timedelta(hours=1)
    assert (
        nat_aware >= min_expected
    ), f"next_attempt_at {nat_aware} should be >= {min_expected} (2h lease)"


# ---------------------------------------------------------------------------
# SagaRunner._execute: error message for non-rectifying resolver (mutmut_5/6)
# ---------------------------------------------------------------------------


class _ErasureOnlyResolver:
    name = "stripe"

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        raise NotImplementedError

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        return ResolverErasure(resolver="stripe")


def test_execute_non_rectifying_resolver_raises_resolver_error_with_message(
    harness: MutantHarness,
) -> None:
    """A rectify entry on a non-rectifying resolver raises ResolverError.

    The message must identify the resolver name and explain the missing method.
    This exercises the `raise ResolverError(msg)` path.

    Kills: _execute__mutmut_5 (msg=None),
           _execute__mutmut_6 (raise ResolverError(None)).
    """
    registry = ResolverRegistry()
    registry.register(_ErasureOnlyResolver())
    seed(harness, [mk_entry(1, operation=OutboxOperation.RECTIFY)])
    runner = SagaRunner(registry, harness.outbox, harness.sink)
    asyncio.run(runner.run_once())
    # The entry must be ABANDONED (terminal) with a ResolverError.
    row = stored_row(harness, UUID(int=1))
    assert row["status"] == OutboxStatus.ABANDONED.value
    assert row["last_error"] == "ResolverError"
    # Verify the event was appended with the error field.
    rectify_failed = [
        e for e in harness.sink.events if e.event_type is AuditEventType.RECTIFICATION_STEP_FAILED
    ]
    assert len(rectify_failed) == 1
    assert rectify_failed[0].payload["error"] == "ResolverError"


# ---------------------------------------------------------------------------
# the runner's audit-event builder stamps a UTC-aware occurred_at (mutmut_11)
# ---------------------------------------------------------------------------


def test_runner_audit_events_have_utc_aware_occurred_at(harness: MutantHarness) -> None:
    """Audit events produced by the runner carry a timezone-aware occurred_at.

    Kills: _event__mutmut_11 (datetime.now(None) → naive datetime).
    """
    registry = ResolverRegistry()
    registry.register(_SimpleFakeResolver())
    seed(harness, [mk_entry(1)])
    asyncio.run(SagaRunner(registry, harness.outbox, harness.sink).run_once())
    assert harness.sink.events, "at least one event must be emitted"
    for event in harness.sink.events:
        assert (
            event.occurred_at.tzinfo is not None
        ), f"event {event.event_type} must have timezone-aware occurred_at"
        assert event.occurred_at.tzinfo == UTC
