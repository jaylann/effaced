"""Properties: re-execution converges to once; any failure script converges."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from uuid import UUID, uuid4

import pytest
from conftest import RecordingAuditSink, StatefulResolver
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import MetaData, create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    AuditEventType,
    BackoffPolicy,
    Correction,
    EffacedTables,
    Outbox,
    OutboxEntry,
    OutboxOperation,
    OutboxStatus,
    PiiCategory,
    ResolverErasure,
    ResolverError,
    ResolverExport,
    ResolverRectification,
    ResolverRegistry,
    ResolverScheduledErasure,
    SagaRunner,
    SubjectRef,
    bind_tables,
)

pytestmark = pytest.mark.property


class ScriptedFlakyResolver(StatefulResolver):
    """Fails a scripted number of calls per ref, then erases like a real system."""

    def __init__(self, name: str, failures: dict[str, int]) -> None:
        super().__init__(name, set(failures))
        self.remaining = dict(failures)

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        if self.remaining[ref.value] > 0:
            self.remaining[ref.value] -= 1
            msg = "transient outage"
            raise TimeoutError(msg)
        return await super().erase_subject(ref)


class World(NamedTuple):
    """One isolated database + runner + fake external system."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    outbox: Outbox
    sink: RecordingAuditSink
    resolver: StatefulResolver
    saga: SagaRunner


def build_world(
    resolver: StatefulResolver,
    *,
    max_attempts: int = 8,
    backoff: BackoffPolicy | None = None,
) -> World:
    """A fresh in-memory world per hypothesis example (no shared fixtures)."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    outbox = Outbox(session_factory, tables.outbox)
    sink = RecordingAuditSink()
    registry = ResolverRegistry()
    registry.register(resolver)
    saga = SagaRunner(registry, outbox, sink, max_attempts=max_attempts, backoff=backoff)
    return World(session_factory, tables, outbox, sink, resolver, saga)


def enqueue(world: World, *entries: OutboxEntry) -> None:
    with world.session_factory() as session:
        world.outbox.enqueue(session, list(entries))
        session.commit()


def entry(subject_id: str, value: str) -> OutboxEntry:
    return OutboxEntry(
        entry_id=uuid4(),
        subject_id=subject_id,
        resolver="stripe",
        ref=SubjectRef(kind="stripe", value=value),
        enqueued_at=datetime.now(UTC),
    )


def statuses(world: World) -> dict[UUID, str]:
    with world.session_factory() as session:
        rows = session.execute(select(world.tables.outbox)).mappings()
        return {row["entry_id"]: str(row["status"]) for row in rows}


def simulate_crash_before_bookkeeping(world: World, entry_id: UUID) -> None:
    """Rewind a settled entry to a dead runner's state: claimed, lease expired."""
    table = world.tables.outbox
    with world.session_factory() as session:
        session.execute(
            update(table)
            .where(table.c.entry_id == entry_id)
            .values(
                status=OutboxStatus.IN_FLIGHT.value,
                next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
        session.commit()


@given(executions=st.integers(min_value=1, max_value=5))
def test_executing_an_entry_n_times_converges_to_once(executions: int) -> None:
    """The entry id is the idempotency key: re-execution changes nothing."""
    world = build_world(StatefulResolver("stripe", {"cus_target", "cus_bystander"}))
    the_entry = entry("1", "cus_target")
    enqueue(world, the_entry)

    for round_number in range(executions):
        assert asyncio.run(world.saga.run_once()) == 1
        if round_number < executions - 1:
            simulate_crash_before_bookkeeping(world, the_entry.entry_id)

    # External state is exactly what a single execution produces...
    assert world.resolver.records == {"cus_bystander"}  # no bleed onto other records
    assert world.resolver.calls == ["cus_target"] * executions
    # ...the entry is terminally succeeded...
    assert statuses(world) == {the_entry.entry_id: OutboxStatus.SUCCEEDED.value}
    # ...and the trail records the outcome (duplicates allowed by design:
    # crash re-execution is at-least-once; assert state, not exact counts).
    succeeded = [
        e for e in world.sink.events if e.event_type is AuditEventType.ERASURE_STEP_SUCCEEDED
    ]
    assert len(succeeded) == executions
    assert succeeded[0].payload["already_absent"] is False
    assert all(e.payload["already_absent"] is True for e in succeeded[1:])
    completed = [e for e in world.sink.events if e.event_type is AuditEventType.ERASURE_COMPLETED]
    assert len(completed) >= 1
    assert all(e.subject_ref == "1" for e in completed)


@given(counts=st.lists(st.integers(min_value=1, max_value=4), min_size=1, max_size=3))
def test_every_subject_completes_exactly_once_and_only_for_itself(
    counts: list[int],
) -> None:
    """Draining any mix of subjects emits one ERASURE_COMPLETED per subject."""
    values = {
        f"subject-{i}": [f"cus_{i}_{j}" for j in range(count)] for i, count in enumerate(counts)
    }
    world = build_world(StatefulResolver("stripe", {v for refs in values.values() for v in refs}))
    for subject_id, refs in values.items():
        enqueue(world, *(entry(subject_id, value) for value in refs))

    while asyncio.run(world.saga.run_once()):
        pass

    assert world.resolver.records == set()
    assert all(status == OutboxStatus.SUCCEEDED.value for status in statuses(world).values())
    completed = [
        e.subject_ref for e in world.sink.events if e.event_type is AuditEventType.ERASURE_COMPLETED
    ]
    assert sorted(completed) == sorted(values)


@given(
    failures=st.lists(st.integers(min_value=0, max_value=4), min_size=1, max_size=3),
    max_attempts=st.integers(min_value=1, max_value=4),
)
def test_any_transient_failure_script_converges(failures: list[int], max_attempts: int) -> None:
    """Entries land SUCCEEDED iff their outage ends before retries run out.

    Attempts count claims (ADR 0010), so an entry whose first ``k`` calls
    fail succeeds on claim ``k + 1`` when ``k < max_attempts`` and is
    ABANDONED (loudly audited) otherwise. Transient failures in between
    leave no audit event.
    """
    script = {f"cus_{index}": count for index, count in enumerate(failures)}
    world = build_world(
        ScriptedFlakyResolver("stripe", script),
        max_attempts=max_attempts,
        backoff=BackoffPolicy(
            base_delay=timedelta(microseconds=1),
            max_delay=timedelta(microseconds=2),
            lease=timedelta(minutes=5),
        ),
    )
    entries = {value: entry(f"subject-{value}", value) for value in script}
    enqueue(world, *entries.values())

    rounds = 0
    while asyncio.run(world.saga.run_once()):
        rounds += 1
        assert rounds <= 10 * len(script) * max_attempts

    status_by_value = {value: statuses(world)[item.entry_id] for value, item in entries.items()}
    events_by_subject = {
        value: [e for e in world.sink.events if e.subject_ref == f"subject-{value}"]
        for value in script
    }
    for value, outages in script.items():
        if outages < max_attempts:
            assert status_by_value[value] == OutboxStatus.SUCCEEDED.value
            succeeded, completed = events_by_subject[value]
            assert succeeded.event_type is AuditEventType.ERASURE_STEP_SUCCEEDED
            assert succeeded.payload["attempts"] == outages + 1
            assert completed.event_type is AuditEventType.ERASURE_COMPLETED
        else:
            assert status_by_value[value] == OutboxStatus.ABANDONED.value
            (failed,) = events_by_subject[value]
            assert failed.event_type is AuditEventType.ERASURE_STEP_FAILED
            assert failed.payload["abandoned"] is True
            assert failed.payload["error"] == "TimeoutError"


# --- mixed-operation entries (ADR 0013) ---------------------------------------

CORRECTIONS = (Correction(category=PiiCategory.CONTACT, value="new@example.com"),)

TINY_BACKOFF = BackoffPolicy(
    base_delay=timedelta(microseconds=1),
    max_delay=timedelta(microseconds=2),
    lease=timedelta(minutes=5),
)


class DualFlakyResolver:
    """Erases and rectifies, failing a scripted number of calls per ref value.

    Refs whose value contains ``"poison"`` fail non-retryably instead —
    the immediate-abandonment path.
    """

    def __init__(self, name: str, failures: dict[str, int] | None = None) -> None:
        self._name = name
        self.remaining = dict(failures or {})

    @property
    def name(self) -> str:
        return self._name

    def _gate(self, ref: SubjectRef) -> None:
        if "poison" in ref.value:
            msg = "non-retryable by script"
            raise ResolverError(msg)
        if self.remaining.get(ref.value, 0) > 0:
            self.remaining[ref.value] -= 1
            msg = "transient outage"
            raise TimeoutError(msg)

    async def export_subject(self, ref: SubjectRef) -> object:
        raise NotImplementedError

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        self._gate(ref)
        return ResolverErasure(resolver=self._name)

    async def rectify_subject(
        self, ref: SubjectRef, corrections: tuple[Correction, ...]
    ) -> ResolverRectification:
        self._gate(ref)
        return ResolverRectification(resolver=self._name)


def build_dual_world(resolver: DualFlakyResolver, *, max_attempts: int = 8) -> World:
    """A fresh in-memory world around the dual-operation resolver."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    outbox = Outbox(session_factory, tables.outbox)
    sink = RecordingAuditSink()
    registry = ResolverRegistry()
    registry.register(resolver)
    saga = SagaRunner(registry, outbox, sink, max_attempts=max_attempts, backoff=TINY_BACKOFF)
    return World(session_factory, tables, outbox, sink, StatefulResolver("unused", set()), saga)


def op_entry(subject_id: str, value: str, operation: OutboxOperation) -> OutboxEntry:
    return OutboxEntry(
        entry_id=uuid4(),
        subject_id=subject_id,
        resolver="stripe",
        ref=SubjectRef(kind="stripe", value=value),
        operation=operation,
        corrections=CORRECTIONS if operation is OutboxOperation.RECTIFY else (),
        enqueued_at=datetime.now(UTC),
    )


def payload_rows(world: World) -> list[dict[str, object]]:
    with world.session_factory() as session:
        rows = session.execute(select(world.tables.outbox)).mappings()
        return [dict(row) for row in rows]


def assert_payload_invariant(world: World) -> None:
    """Terminal rows never retain a payload; FAILED rectify rows keep theirs."""
    terminal = {OutboxStatus.SUCCEEDED.value, OutboxStatus.ABANDONED.value}
    for row in payload_rows(world):
        if str(row["status"]) in terminal:
            assert row["payload"] is None
        elif (
            str(row["status"]) == OutboxStatus.FAILED.value
            and str(row["operation"]) == OutboxOperation.RECTIFY.value
        ):
            assert row["payload"] is not None


@given(
    plan=st.lists(
        st.tuples(st.sampled_from(OutboxOperation), st.integers(min_value=0, max_value=4)),
        min_size=1,
        max_size=4,
    ),
    max_attempts=st.integers(min_value=1, max_value=4),
)
def test_no_terminal_entry_retains_a_payload(
    plan: list[tuple[OutboxOperation, int]], max_attempts: int
) -> None:
    """SUCCEEDED/ABANDONED ⇒ payload NULL; FAILED rectify rows keep it for the retry."""
    failures = {f"cus_{index}": outages for index, (_, outages) in enumerate(plan)}
    world = build_dual_world(DualFlakyResolver("stripe", failures), max_attempts=max_attempts)
    entries = [
        op_entry(f"subject-{index}", f"cus_{index}", operation)
        for index, (operation, _) in enumerate(plan)
    ]
    enqueue(world, *entries)

    rounds = 0
    while asyncio.run(world.saga.run_once()):
        assert_payload_invariant(world)
        rounds += 1
        assert rounds <= 10 * len(plan) * max_attempts

    terminal = {OutboxStatus.SUCCEEDED.value, OutboxStatus.ABANDONED.value}
    for row in payload_rows(world):
        assert str(row["status"]) in terminal
        assert row["payload"] is None


@given(
    subjects=st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=2),
            st.integers(min_value=0, max_value=2),
            st.booleans(),
            st.booleans(),
        ),
        min_size=1,
        max_size=3,
    )
)
def test_completion_isolation_per_operation(
    subjects: list[tuple[int, int, bool, bool]],
) -> None:
    """ERASURE_COMPLETED iff every erase entry succeeded, never counting rectify
    entries — and symmetrically; at most once per subject+operation."""
    world = build_dual_world(DualFlakyResolver("stripe"))
    for index, (erases, rectifies, erase_poisoned, rectify_poisoned) in enumerate(subjects):
        subject = f"subject-{index}"
        for n in range(erases):
            marker = "poison" if erase_poisoned and n == 0 else "ok"
            enqueue(world, op_entry(subject, f"e_{index}_{n}_{marker}", OutboxOperation.ERASE))
        for n in range(rectifies):
            marker = "poison" if rectify_poisoned and n == 0 else "ok"
            enqueue(world, op_entry(subject, f"r_{index}_{n}_{marker}", OutboxOperation.RECTIFY))

    while asyncio.run(world.saga.run_once()):
        pass

    for index, (erases, rectifies, erase_poisoned, rectify_poisoned) in enumerate(subjects):
        subject = f"subject-{index}"
        events = [e.event_type for e in world.sink.events if e.subject_ref == subject]
        erase_completions = events.count(AuditEventType.ERASURE_COMPLETED)
        rectify_completions = events.count(AuditEventType.RECTIFICATION_COMPLETED)
        assert erase_completions == (1 if erases and not erase_poisoned else 0)
        assert rectify_completions == (1 if rectifies and not rectify_poisoned else 0)


# --- retention-only erasure honesty (ADR 0018) ---------------------------------

RETENTION_OUTCOMES = ("transient", "future", "past", "absent")


class RetentionScriptedResolver:
    """A retention-only double replaying a per-ref script of outcome kinds.

    ``"transient"`` raises ``TimeoutError``, ``"future"``/``"past"`` report
    a horizon on that side of now (both park the entry; a past horizon is
    clamped by the runner), ``"absent"`` verifies the data gone. An
    exhausted script keeps reporting absence — the vendor's retention job
    eventually purges.
    """

    def __init__(self, name: str, scripts: dict[str, list[str]]) -> None:
        self._name = name
        self._scripts = {value: list(script) for value, script in scripts.items()}
        self.parks = 0

    @property
    def name(self) -> str:
        return self._name

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        raise NotImplementedError

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        msg = "retention-only: cannot delete on demand"
        raise ResolverError(msg)

    async def schedule_erasure(self, ref: SubjectRef) -> ResolverScheduledErasure:
        script = self._scripts.get(ref.value, [])
        kind = script.pop(0) if script else "absent"
        if kind == "transient":
            msg = "transient outage"
            raise TimeoutError(msg)
        if kind == "absent":
            return ResolverScheduledErasure(resolver=self._name, already_absent=True)
        self.parks += 1
        offset = timedelta(days=30) if kind == "future" else -timedelta(days=1)
        return ResolverScheduledErasure(resolver=self._name, expires_at=datetime.now(UTC) + offset)


def build_retention_world(resolver: RetentionScriptedResolver, *, max_attempts: int) -> World:
    """A fresh in-memory world around the retention-only resolver."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata = MetaData()
    tables = bind_tables(metadata)
    metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    outbox = Outbox(session_factory, tables.outbox)
    sink = RecordingAuditSink()
    registry = ResolverRegistry()
    registry.register(resolver)
    saga = SagaRunner(registry, outbox, sink, max_attempts=max_attempts, backoff=TINY_BACKOFF)
    return World(session_factory, tables, outbox, sink, StatefulResolver("unused", set()), saga)


def vendor_entry(value: str) -> OutboxEntry:
    return OutboxEntry(
        entry_id=uuid4(),
        subject_id="subject-1",
        resolver="vault",
        ref=SubjectRef(kind="vault", value=value),
        enqueued_at=datetime.now(UTC),
    )


def force_due(world: World) -> None:
    """Expire every pending gate (backoff or horizon) so the next claim runs."""
    table = world.tables.outbox
    terminal = (OutboxStatus.SUCCEEDED.value, OutboxStatus.ABANDONED.value)
    with world.session_factory() as session:
        session.execute(
            update(table)
            .where(table.c.status.notin_(terminal))
            .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.commit()


def event_count(world: World, event_type: AuditEventType) -> int:
    return sum(1 for e in world.sink.events if e.event_type is event_type)


def expected_to_abandon(script: list[str], max_attempts: int) -> bool:
    """Mirror the budget: transients count claims, a park resets, absence ends.

    Between parks every claim is a transient (a horizon parks, absence is
    terminal), so an entry abandons exactly when some transient streak
    since the last park reaches ``max_attempts``. An exhausted script
    defaults to absence, so it never abandons past its scripted outcomes.
    """
    streak = 0
    for kind in script:
        if kind == "transient":
            streak += 1
            if streak >= max_attempts:
                return True
        elif kind == "absent":
            return False
        else:
            streak = 0
    return False


@settings(deadline=None, max_examples=25)
@given(
    scripts=st.lists(
        st.lists(st.sampled_from(RETENTION_OUTCOMES), max_size=4), min_size=1, max_size=3
    ),
    max_attempts=st.integers(min_value=1, max_value=4),
)
def test_completion_honesty_for_any_retention_only_script(
    scripts: list[list[str]], max_attempts: int
) -> None:
    """ERASURE_COMPLETED iff every entry reached SUCCEEDED — never while parked.

    The honesty property of ADR 0018: a SCHEDULED entry blocks completion,
    completion fires at most once per subject, every park leaves an
    ERASURE_EXPIRY_SCHEDULED event, and any script — exhaustion defaults to
    verified absence — converges to a terminal outbox.
    """
    resolver = RetentionScriptedResolver(
        "vault", {f"rec_{index}": script for index, script in enumerate(scripts)}
    )
    world = build_retention_world(resolver, max_attempts=max_attempts)
    entries = {index: vendor_entry(f"rec_{index}") for index in range(len(scripts))}
    enqueue(world, *entries.values())

    terminal = {OutboxStatus.SUCCEEDED.value, OutboxStatus.ABANDONED.value}
    for _ in range(10 * sum(len(script) + 1 for script in scripts) * max_attempts):
        asyncio.run(world.saga.run_once())
        completions = event_count(world, AuditEventType.ERASURE_COMPLETED)
        assert completions <= 1  # at most once per subject
        current = statuses(world)
        if OutboxStatus.SCHEDULED.value in current.values():
            assert completions == 0  # never complete while a horizon is pending
        if set(current.values()) <= terminal:
            break
        force_due(world)
    else:
        pytest.fail("eventual purge did not converge within the cycle budget")

    final = statuses(world)
    for index, script in enumerate(scripts):
        expected = (
            OutboxStatus.ABANDONED
            if expected_to_abandon(script, max_attempts)
            else OutboxStatus.SUCCEEDED
        )
        assert final[entries[index].entry_id] == expected.value
    all_succeeded = all(status == OutboxStatus.SUCCEEDED.value for status in final.values())
    assert event_count(world, AuditEventType.ERASURE_COMPLETED) == (1 if all_succeeded else 0)
    assert event_count(world, AuditEventType.ERASURE_EXPIRY_SCHEDULED) == resolver.parks
