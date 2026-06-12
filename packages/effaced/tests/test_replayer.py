"""Replayer.replay — re-applying committed erasures after a restore (ADR 0018)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, NamedTuple
from uuid import UUID

import pytest
from conftest import (
    Base,
    FailingExecutor,
    FakeResolver,
    RecordingAuditSink,
    seed_two_subjects,
)
from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    AuditEvent,
    AuditEventType,
    EffacedTables,
    ErasurePlanner,
    Outbox,
    Replayer,
    ReplayPlan,
    ResolverRegistry,
    SubjectRef,
    bind_tables,
    collect_data_map,
    resolve_subject_graph,
)
from effaced.adapters.sqlalchemy import ErasureExecutor

if TYPE_CHECKING:
    from collections.abc import Callable

    from effaced import ErasureResult, StepExecutor


class ExplodingSink(RecordingAuditSink):
    """A sink that refuses every append — replay must mutate nothing."""

    def append(self, event: AuditEvent) -> None:
        msg = "sink down"
        raise RuntimeError(msg)


class Harness(NamedTuple):
    """A wired planner + replayer over the seeded two-subject schema."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    sink: RecordingAuditSink
    planner: ErasurePlanner
    replayer: Replayer


def build_harness(
    *,
    executor: StepExecutor | None = None,
    refs_for: Callable[[str], tuple[SubjectRef, ...]] | None = None,
) -> Harness:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    effaced_metadata = MetaData()
    tables = bind_tables(effaced_metadata)
    Base.metadata.create_all(engine)
    effaced_metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    with session_factory() as session:
        seed_two_subjects(session)
    registry = ResolverRegistry()
    registry.register(FakeResolver("crm"))
    data_map = collect_data_map(Base.metadata)
    sink = RecordingAuditSink()
    planner = ErasurePlanner(
        data_map,
        resolve_subject_graph(data_map, Base.registry),
        registry,
        executor=executor if executor is not None else ErasureExecutor(Base.metadata),
        outbox=Outbox(session_factory, tables.outbox),
        audit_sink=sink,
    )
    replayer = Replayer(planner, sink, refs_for=refs_for)
    return Harness(session_factory, tables, sink, planner, replayer)


@pytest.fixture()
def harness() -> Harness:
    return build_harness()


def erase(harness: Harness, subject_id: str) -> None:
    """One committed erasure through the real planner."""
    with harness.session_factory() as session:
        harness.planner.erase_subject(session, subject_id)
        session.commit()


def restore(harness: Harness) -> None:
    """Simulate a backup restore: wipe the schema tables, re-seed the backup."""
    with harness.session_factory() as session:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
    with harness.session_factory() as session:
        seed_two_subjects(session)


def replay(harness: Harness, plan: ReplayPlan) -> tuple[ErasureResult, ...]:
    with harness.session_factory() as session:
        results = harness.replayer.replay(session, plan)
        session.commit()
    return results


def rows(harness: Harness, table_name: str) -> list[dict[str, object]]:
    table = Base.metadata.tables[table_name]
    with harness.session_factory() as session:
        return [dict(row) for row in session.execute(select(table)).mappings()]


def snapshot(harness: Harness) -> dict[str, list[dict[str, object]]]:
    return {name: rows(harness, name) for name in Base.metadata.tables}


def outbox_rows(harness: Harness) -> list[dict[str, object]]:
    with harness.session_factory() as session:
        return [dict(row) for row in session.execute(select(harness.tables.outbox)).mappings()]


def erased_then_restored(harness: Harness, *subjects: str) -> ReplayPlan:
    """Erase subjects, restore the backup, derive the plan from the survivor."""
    backup_taken_at = datetime.now(UTC)
    for subject_id in subjects:
        erase(harness, subject_id)
    surviving_trail = tuple(harness.sink.events)
    restore(harness)
    return harness.replayer.plan(surviving_trail, backup_taken_at=backup_taken_at)


def test_replay_erases_resurrected_rows_again(harness: Harness) -> None:
    """Row-deleted tables empty out again; anonymized columns are rewritten."""
    plan = erased_then_restored(harness, "1")
    replay(harness, plan)
    assert [row["user_id"] for row in rows(harness, "orders")] == [2]
    assert [row["user_id"] for row in rows(harness, "comments")] == [2]
    assert [row["id"] for row in rows(harness, "order_items")] == [2]
    (alice,) = [row for row in rows(harness, "users") if row["id"] == 1]
    assert alice["email"] != "alice@example.com"
    assert alice["name"] != "Alice Doe"
    assert alice["theme"] == "dark"  # unannotated columns are untouched


def test_other_subject_rows_survive_byte_identical(harness: Harness) -> None:
    """No cross-subject bleed: subject 2's rows and noise tables are untouched."""
    plan = erased_then_restored(harness, "1")
    before = snapshot(harness)
    replay(harness, plan)
    after = snapshot(harness)
    for noise in ("app_settings", "tags", "user_tags"):
        assert after[noise] == before[noise]
    assert [row for row in after["users"] if row["id"] == 2] == [
        row for row in before["users"] if row["id"] == 2
    ]
    assert after["orders"] == [row for row in before["orders"] if row["user_id"] == 2]
    assert [row for row in after["comments"] if row["user_id"] == 2] == [
        row for row in before["comments"] if row["user_id"] == 2
    ]
    assert [row for row in after["invoices"] if row["id"] == 2] == [
        row for row in before["invoices"] if row["id"] == 2
    ]
    assert [row for row in after["order_items"] if row["id"] == 2] == [
        row for row in before["order_items"] if row["id"] == 2
    ]


def test_retained_cells_survive_replay(harness: Harness) -> None:
    """RETAIN columns are preserved by the replayed erasure, like any erasure."""
    plan = erased_then_restored(harness, "1")
    replay(harness, plan)
    (invoice,) = [row for row in rows(harness, "invoices") if row["id"] == 1]
    assert invoice["billing_address"] == "1 Alice Street"


def test_replayed_event_precedes_the_new_erasure_sequence(harness: Harness) -> None:
    """ERASURE_REPLAYED is appended before the re-run's ERASURE_REQUESTED."""
    backup_taken_at = datetime.now(UTC)
    erase(harness, "1")
    surviving_trail = tuple(harness.sink.events)
    restore(harness)
    plan = harness.replayer.plan(surviving_trail, backup_taken_at=backup_taken_at)
    marker = len(harness.sink.events)
    replay(harness, plan)
    appended = harness.sink.events[marker:]
    assert appended[0].event_type is AuditEventType.ERASURE_REPLAYED
    assert appended[0].subject_ref == "1"
    assert appended[0].payload == {
        "backup_taken_at": backup_taken_at.isoformat(),
        "source_event_id": str(plan.entries[0].source_event_id),
        "completions": 1,
    }
    assert appended[1].event_type is AuditEventType.ERASURE_REQUESTED


def test_sink_failure_on_the_replayed_event_changes_nothing(harness: Harness) -> None:
    """Append-first: a down sink means no erasure work starts (ADR 0015 rule)."""
    plan = erased_then_restored(harness, "1")
    exploding = Replayer(harness.planner, ExplodingSink())
    before = snapshot(harness)
    with harness.session_factory() as session, pytest.raises(RuntimeError, match="sink down"):
        exploding.replay(session, plan)
    assert snapshot(harness) == before


def test_failure_mid_replay_fails_fast(harness: Harness) -> None:
    """The first failing subject re-raises; later entries are never started."""
    plan = erased_then_restored(harness, "1", "2")
    assert [entry.subject_id for entry in plan.entries] == ["1", "2"]
    data_map = collect_data_map(Base.metadata)
    failing = ErasurePlanner(
        data_map,
        resolve_subject_graph(data_map, Base.registry),
        executor=FailingExecutor(ErasureExecutor(Base.metadata), fail_at="users"),
        outbox=Outbox(harness.session_factory, harness.tables.outbox),
        audit_sink=harness.sink,
    )
    replayer = Replayer(failing, harness.sink)
    marker = len(harness.sink.events)
    with harness.session_factory() as session, pytest.raises(RuntimeError, match="injected"):
        replayer.replay(session, plan)
    appended = harness.sink.events[marker:]
    replayed = [e for e in appended if e.event_type is AuditEventType.ERASURE_REPLAYED]
    assert [e.subject_ref for e in replayed] == ["1"]  # subject 2 never started


def test_default_replay_is_local_only(harness: Harness) -> None:
    """Without refs_for, no external work is enqueued — resolvers are skipped."""
    plan = erased_then_restored(harness, "1")
    marker = len(harness.sink.events)
    replay(harness, plan)
    assert outbox_rows(harness) == []
    completed = next(
        e
        for e in harness.sink.events[marker:]
        if e.event_type is AuditEventType.ERASURE_LOCAL_COMPLETED
    )
    assert completed.payload["skipped_resolvers"] == "crm"
    assert completed.payload["enqueued"] == 0


def test_refs_provider_reenqueues_external_work() -> None:
    """With refs_for, the replayed erasure re-enqueues matched external steps."""
    harness = build_harness(
        refs_for=lambda subject_id: (SubjectRef(kind="crm", value=f"crm-{subject_id}"),)
    )
    plan = erased_then_restored(harness, "1")
    replay(harness, plan)
    (entry,) = outbox_rows(harness)
    assert entry["resolver"] == "crm"
    assert entry["ref_value"] == "crm-1"
    assert entry["subject_id"] == "1"


def test_indeterminate_subjects_are_never_executed(harness: Harness) -> None:
    """Surfaced subjects are the operator's call — replay touches nothing."""
    backup_taken_at = datetime.now(UTC)
    erase(harness, "1")  # a real sequence whose terminal events we filter away
    surviving = tuple(
        event
        for event in harness.sink.events
        if event.event_type is AuditEventType.ERASURE_REQUESTED
    )
    restore(harness)
    plan = harness.replayer.plan(surviving, backup_taken_at=backup_taken_at)
    assert plan.entries == ()
    assert plan.indeterminate == ("1",)
    before = snapshot(harness)
    marker = len(harness.sink.events)
    assert replay(harness, plan) == ()
    assert snapshot(harness) == before
    assert harness.sink.events[marker:] == []


def test_replay_twice_is_a_noop_success(harness: Harness) -> None:
    """Re-running a replay converges: zero deletes, evidence still appended."""
    plan = erased_then_restored(harness, "1")
    replay(harness, plan)
    (result,) = replay(harness, plan)
    assert sum(result.deleted.values()) == 0
    replayed = [e for e in harness.sink.events if e.event_type is AuditEventType.ERASURE_REPLAYED]
    assert len(replayed) == 2  # each run is evidence


def test_plan_method_delegates_to_the_pure_derivation(harness: Harness) -> None:
    """Replayer.plan and ReplayPlan.derive agree — one classification, two doors."""
    backup_taken_at = datetime(2026, 6, 1, tzinfo=UTC)
    events = (
        AuditEvent(
            event_id=UUID(int=1),
            event_type=AuditEventType.ERASURE_LOCAL_COMPLETED,
            subject_ref="1",
            occurred_at=datetime(2026, 6, 2, tzinfo=UTC),
        ),
    )
    assert harness.replayer.plan(events, backup_taken_at=backup_taken_at) == ReplayPlan.derive(
        events, backup_taken_at=backup_taken_at
    )
