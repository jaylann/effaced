"""Retention-only erasure through the saga: park until horizon, verify, complete (ADR 0022)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from typing import NamedTuple
from uuid import UUID

import pytest
from conftest import RecordingAuditSink
from sqlalchemy import MetaData, create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    AuditEventType,
    BackoffPolicy,
    EffacedTables,
    Outbox,
    OutboxEntry,
    OutboxStatus,
    ResolverErasure,
    ResolverError,
    ResolverExport,
    ResolverRegistry,
    ResolverScheduledErasure,
    SagaRunner,
    SubjectRef,
    bind_tables,
)

BACKOFF = BackoffPolicy(
    base_delay=timedelta(minutes=1),
    max_delay=timedelta(minutes=8),
    lease=timedelta(minutes=5),
)

HORIZON = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)


def scheduled(expires_at: datetime = HORIZON) -> ResolverScheduledErasure:
    return ResolverScheduledErasure(resolver="vendor", expires_at=expires_at)


ABSENT = ResolverScheduledErasure(resolver="vendor", already_absent=True)


class ScheduleScriptedResolver:
    """A retention-only resolver double replaying a script of outcomes.

    Each scripted item is a :class:`ResolverScheduledErasure` to return or
    an exception to raise; once the script is exhausted every further call
    reports ``already_absent=True`` (the vendor purged). ``erase_subject``
    raises by contract — the saga must never call it for this resolver.
    """

    def __init__(
        self, name: str = "vendor", script: tuple[ResolverScheduledErasure | Exception, ...] = ()
    ) -> None:
        self._name = name
        self._script = list(script)
        self.schedule_calls: list[SubjectRef] = []
        self.erase_calls: list[SubjectRef] = []

    @property
    def name(self) -> str:
        return self._name

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        raise NotImplementedError

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        self.erase_calls.append(ref)
        raise ResolverError("retention-only: cannot delete on demand")

    async def schedule_erasure(self, ref: SubjectRef) -> ResolverScheduledErasure:
        self.schedule_calls.append(ref)
        if not self._script:
            return ResolverScheduledErasure(resolver=self._name, already_absent=True)
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class DeletingResolver:
    """An ordinary delete-on-demand resolver double."""

    def __init__(self, name: str = "stripe") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        raise NotImplementedError

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        return ResolverErasure(resolver=self._name)


class RunnerHarness(NamedTuple):
    """A runner over a fresh in-memory database and a recording sink."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    outbox: Outbox
    sink: RecordingAuditSink
    registry: ResolverRegistry


@pytest.fixture()
def harness() -> Iterator[RunnerHarness]:
    """An outbox + registry + sink on a fresh in-memory SQLite database."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    yield RunnerHarness(
        session_factory=session_factory,
        tables=tables,
        outbox=Outbox(session_factory, tables.outbox),
        sink=RecordingAuditSink(),
        registry=ResolverRegistry(),
    )
    engine.dispose()


def runner(harness: RunnerHarness, *, max_attempts: int = 8) -> SagaRunner:
    return SagaRunner(
        harness.registry,
        harness.outbox,
        harness.sink,
        max_attempts=max_attempts,
        backoff=BACKOFF,
    )


def entry(number: int, *, subject_id: str = "1", resolver: str = "vendor") -> OutboxEntry:
    return OutboxEntry(
        entry_id=UUID(int=number),
        subject_id=subject_id,
        resolver=resolver,
        ref=SubjectRef(kind=resolver, value=f"rec_{number}"),
        enqueued_at=datetime(2026, 6, 1, 12, 0, number, tzinfo=UTC),
    )


def seed(harness: RunnerHarness, *entries: OutboxEntry) -> None:
    with harness.session_factory() as session:
        harness.outbox.enqueue(session, list(entries))
        session.commit()


def rows_by_id(harness: RunnerHarness) -> dict[UUID, dict[str, object]]:
    with harness.session_factory() as session:
        result = session.execute(select(harness.tables.outbox)).mappings()
        return {row["entry_id"]: dict(row) for row in result}


def make_due(harness: RunnerHarness) -> None:
    """Expire every pending gate (backoff or horizon) so the next claim runs."""
    table = harness.tables.outbox
    past = datetime.now(UTC) - timedelta(seconds=1)
    terminal = (OutboxStatus.SUCCEEDED.value, OutboxStatus.ABANDONED.value)
    with harness.session_factory() as session:
        session.execute(
            update(table).where(table.c.status.notin_(terminal)).values(next_attempt_at=past)
        )
        session.commit()


def events_of(harness: RunnerHarness, event_type: AuditEventType) -> list[dict[str, object]]:
    return [dict(e.payload) for e in harness.sink.events if e.event_type is event_type]


def test_schedule_parks_the_entry_and_audits_the_horizon(harness: RunnerHarness) -> None:
    """A future horizon parks SCHEDULED with the horizon as the claim gate."""
    resolver = ScheduleScriptedResolver(script=(scheduled(),))
    harness.registry.register(resolver)
    seed(harness, entry(1))

    assert asyncio.run(runner(harness).run_once()) == 1

    row = rows_by_id(harness)[UUID(int=1)]
    assert row["status"] == OutboxStatus.SCHEDULED.value
    assert row["attempts"] == 0  # fresh verification budget, the requeue precedent
    assert row["last_error"] is None
    gate = row["next_attempt_at"]
    assert isinstance(gate, datetime)
    assert gate.replace(tzinfo=UTC) == HORIZON
    assert resolver.erase_calls == []  # never the on-demand path

    (payload,) = events_of(harness, AuditEventType.ERASURE_EXPIRY_SCHEDULED)
    assert payload == {
        "target": "vendor",
        "external": True,
        "expires_at": HORIZON.isoformat(),
        "prior_attempts": 1,
    }
    assert events_of(harness, AuditEventType.ERASURE_STEP_SUCCEEDED) == []
    assert events_of(harness, AuditEventType.ERASURE_COMPLETED) == []


def test_parked_entry_is_not_claimable_before_the_horizon(harness: RunnerHarness) -> None:
    resolver = ScheduleScriptedResolver(script=(scheduled(),))
    harness.registry.register(resolver)
    seed(harness, entry(1))

    asyncio.run(runner(harness).run_once())
    assert asyncio.run(runner(harness).run_once()) == 0
    assert len(resolver.schedule_calls) == 1


def test_verified_expiry_after_the_horizon_completes_honestly(harness: RunnerHarness) -> None:
    """Park -> horizon passes -> re-verify -> verified absence -> completed once."""
    resolver = ScheduleScriptedResolver(script=(scheduled(), ABSENT))
    harness.registry.register(resolver)
    seed(harness, entry(1))

    asyncio.run(runner(harness).run_once())  # parks
    make_due(harness)  # the horizon passes
    assert asyncio.run(runner(harness).run_once()) == 1  # verifies

    assert rows_by_id(harness)[UUID(int=1)]["status"] == OutboxStatus.SUCCEEDED.value
    assert len(resolver.schedule_calls) == 2
    (payload,) = events_of(harness, AuditEventType.ERASURE_STEP_SUCCEEDED)
    assert payload == {
        "target": "vendor",
        "external": True,
        "verified_expiry": True,
        "already_absent": True,
        "attempts": 1,  # the park reset the budget; the verify claim is attempt 1
    }
    assert "strategy" not in payload  # nothing was deleted by us
    assert events_of(harness, AuditEventType.ERASURE_COMPLETED) == [{}]


def test_a_slipped_horizon_reparks_loudly(harness: RunnerHarness) -> None:
    """Each fresh horizon is a fresh audited fact, never a silent wait."""
    later = HORIZON + timedelta(days=30)
    resolver = ScheduleScriptedResolver(script=(scheduled(), scheduled(later)))
    harness.registry.register(resolver)
    seed(harness, entry(1))

    asyncio.run(runner(harness).run_once())
    make_due(harness)
    asyncio.run(runner(harness).run_once())

    row = rows_by_id(harness)[UUID(int=1)]
    assert row["status"] == OutboxStatus.SCHEDULED.value
    events = events_of(harness, AuditEventType.ERASURE_EXPIRY_SCHEDULED)
    assert [e["expires_at"] for e in events] == [HORIZON.isoformat(), later.isoformat()]
    assert events_of(harness, AuditEventType.ERASURE_COMPLETED) == []


def test_a_scheduled_sibling_blocks_completion_for_a_mixed_subject(
    harness: RunnerHarness,
) -> None:
    """ERASURE_COMPLETED waits for the parked entry's verified expiry."""
    vendor = ScheduleScriptedResolver(script=(scheduled(), ABSENT))
    harness.registry.register(vendor)
    harness.registry.register(DeletingResolver("stripe"))
    seed(harness, entry(1), entry(2, resolver="stripe"))

    asyncio.run(runner(harness).run_once())
    rows = rows_by_id(harness)
    assert rows[UUID(int=1)]["status"] == OutboxStatus.SCHEDULED.value
    assert rows[UUID(int=2)]["status"] == OutboxStatus.SUCCEEDED.value
    assert events_of(harness, AuditEventType.ERASURE_COMPLETED) == []

    make_due(harness)
    asyncio.run(runner(harness).run_once())
    assert rows_by_id(harness)[UUID(int=1)]["status"] == OutboxStatus.SUCCEEDED.value
    assert events_of(harness, AuditEventType.ERASURE_COMPLETED) == [{}]


def test_the_park_resets_the_budget_and_keeps_the_struggle_in_the_event(
    harness: RunnerHarness,
) -> None:
    """Transient failures before the schedule surface as prior_attempts."""
    resolver = ScheduleScriptedResolver(script=(TimeoutError(), scheduled()))
    harness.registry.register(resolver)
    seed(harness, entry(1))

    asyncio.run(runner(harness).run_once())  # transient -> FAILED, attempts=1
    assert rows_by_id(harness)[UUID(int=1)]["status"] == OutboxStatus.FAILED.value
    make_due(harness)
    asyncio.run(runner(harness).run_once())  # schedules on attempt 2 -> parks

    row = rows_by_id(harness)[UUID(int=1)]
    assert row["status"] == OutboxStatus.SCHEDULED.value
    assert row["attempts"] == 0
    (payload,) = events_of(harness, AuditEventType.ERASURE_EXPIRY_SCHEDULED)
    assert payload["prior_attempts"] == 2


def test_a_past_horizon_is_clamped_to_one_backoff_step(harness: RunnerHarness) -> None:
    """A stale horizon must not hot-loop the entry."""
    stale = datetime.now(UTC) - timedelta(days=1)
    resolver = ScheduleScriptedResolver(script=(scheduled(stale),))
    harness.registry.register(resolver)
    seed(harness, entry(1))

    before = datetime.now(UTC)
    asyncio.run(runner(harness).run_once())

    row = rows_by_id(harness)[UUID(int=1)]
    assert row["status"] == OutboxStatus.SCHEDULED.value
    gate = row["next_attempt_at"]
    assert isinstance(gate, datetime)
    expected = before + BACKOFF.delay(1)
    assert abs(gate.replace(tzinfo=UTC) - expected) < timedelta(seconds=5)
    assert asyncio.run(runner(harness).run_once()) == 0  # not due despite the past horizon
    # The audited fact stays the vendor's horizon, not the clamped gate.
    (payload,) = events_of(harness, AuditEventType.ERASURE_EXPIRY_SCHEDULED)
    assert payload["expires_at"] == stale.isoformat()


def test_already_absent_on_first_schedule_succeeds_without_parking(
    harness: RunnerHarness,
) -> None:
    """The vendor never held (or already purged) the subject: verified, done."""
    harness.registry.register(ScheduleScriptedResolver(script=(ABSENT,)))
    seed(harness, entry(1))

    assert asyncio.run(runner(harness).run_once()) == 1

    assert rows_by_id(harness)[UUID(int=1)]["status"] == OutboxStatus.SUCCEEDED.value
    assert events_of(harness, AuditEventType.ERASURE_EXPIRY_SCHEDULED) == []
    (payload,) = events_of(harness, AuditEventType.ERASURE_STEP_SUCCEEDED)
    assert payload["verified_expiry"] is True
    assert events_of(harness, AuditEventType.ERASURE_COMPLETED) == [{}]


def test_resolver_error_on_the_schedule_path_abandons_loudly(harness: RunnerHarness) -> None:
    """Fault injection: the schedule path keeps the ADR 0010 taxonomy."""
    harness.registry.register(ScheduleScriptedResolver(script=(ResolverError("revoked key"),)))
    seed(harness, entry(1))

    assert asyncio.run(runner(harness).run_once()) == 1

    assert rows_by_id(harness)[UUID(int=1)]["status"] == OutboxStatus.ABANDONED.value
    (payload,) = events_of(harness, AuditEventType.ERASURE_STEP_FAILED)
    assert payload["error"] == "ResolverError"
    assert payload["abandoned"] is True
    assert events_of(harness, AuditEventType.ERASURE_COMPLETED) == []


def test_transient_failure_during_verification_retries(harness: RunnerHarness) -> None:
    """A flaky verify after the horizon stays on the retry curve, then converges."""
    resolver = ScheduleScriptedResolver(script=(scheduled(), TimeoutError(), ABSENT))
    harness.registry.register(resolver)
    seed(harness, entry(1))

    asyncio.run(runner(harness).run_once())  # parks
    make_due(harness)
    asyncio.run(runner(harness).run_once())  # transient during verify
    assert rows_by_id(harness)[UUID(int=1)]["status"] == OutboxStatus.FAILED.value
    make_due(harness)
    asyncio.run(runner(harness).run_once())  # verify converges

    assert rows_by_id(harness)[UUID(int=1)]["status"] == OutboxStatus.SUCCEEDED.value
    assert events_of(harness, AuditEventType.ERASURE_COMPLETED) == [{}]


def test_expires_at_payload_is_a_parseable_utc_instant(harness: RunnerHarness) -> None:
    """The audited horizon is normalized to UTC regardless of the vendor's zone."""
    vendor_local = HORIZON.astimezone(timezone(timedelta(hours=2)))
    harness.registry.register(ScheduleScriptedResolver(script=(scheduled(vendor_local),)))
    seed(harness, entry(1))

    asyncio.run(runner(harness).run_once())

    (payload,) = events_of(harness, AuditEventType.ERASURE_EXPIRY_SCHEDULED)
    parsed = datetime.fromisoformat(str(payload["expires_at"]))
    assert parsed == HORIZON
    assert parsed.utcoffset() == timedelta(0)
