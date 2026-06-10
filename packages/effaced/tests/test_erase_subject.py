"""erase_subject() — atomic local phase, durable enqueue, audited outcomes."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import NamedTuple
from uuid import UUID

import pytest
from conftest import Base, FakeResolver, RecordingAuditSink, seed_two_subjects
from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    AuditEvent,
    AuditEventType,
    ConfigurationError,
    EffacedTables,
    ErasurePlanner,
    Outbox,
    OutboxEntry,
    ResolverError,
    ResolverRegistry,
    StepExecutor,
    SubjectGraph,
    SubjectRef,
    bind_tables,
    collect_data_map,
    resolve_subject_graph,
)
from effaced.adapters.sqlalchemy import ErasureExecutor
from effaced.erasure import ErasureStep

REFS = (
    SubjectRef(kind="crm", value="crm-1"),
    SubjectRef(kind="stripe", value="cus_1"),
)


class FailingExecutor:
    """Delegates to a real executor until it reaches the named table."""

    def __init__(self, inner: StepExecutor, fail_at: str) -> None:
        self._inner = inner
        self._fail_at = fail_at

    def execute(
        self, session: Session, graph: SubjectGraph, step: ErasureStep, subject_id: str
    ) -> int:
        if step.target == self._fail_at:
            msg = "injected fault"
            raise RuntimeError(msg)
        return self._inner.execute(session, graph, step, subject_id)


class ExplodingOutbox(Outbox):
    """An outbox whose enqueue always fails."""

    def enqueue(self, session: Session, entries: Sequence[object]) -> None:
        msg = "outbox down"
        raise RuntimeError(msg)


class CapturingOutbox(Outbox):
    """An outbox that records the entry models it is asked to persist."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.captured: list[OutboxEntry] = []

    def enqueue(self, session: Session, entries: Sequence[OutboxEntry]) -> None:
        self.captured.extend(entries)
        super().enqueue(session, entries)


class ExplodingSink(RecordingAuditSink):
    """A sink that refuses every append."""

    def append(self, event: object) -> None:
        msg = "sink down"
        raise RuntimeError(msg)


class FlakySink(RecordingAuditSink):
    """A sink that fails exactly once, on the n-th append."""

    def __init__(self, fail_on_call: int) -> None:
        super().__init__()
        self._calls = 0
        self._fail_on = fail_on_call

    def append(self, event: AuditEvent) -> None:
        self._calls += 1
        if self._calls == self._fail_on:
            msg = "sink hiccup"
            raise RuntimeError(msg)
        super().append(event)


class Harness(NamedTuple):
    """A fully wired planner over the seeded two-subject schema."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    sink: RecordingAuditSink
    planner: ErasurePlanner
    executor: ErasureExecutor
    outbox: Outbox


def build_harness(
    *,
    executor: StepExecutor | None = None,
    outbox_cls: type[Outbox] = Outbox,
    sink: RecordingAuditSink | None = None,
    resolvers: tuple[str, ...] = ("crm", "stripe"),
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
    for name in resolvers:
        registry.register(FakeResolver(name))
    data_map = collect_data_map(Base.metadata)
    real_executor = ErasureExecutor(Base.metadata)
    outbox = outbox_cls(session_factory, tables.outbox)
    recording = sink if sink is not None else RecordingAuditSink()
    planner = ErasurePlanner(
        data_map,
        resolve_subject_graph(data_map, Base.registry),
        registry,
        executor=executor if executor is not None else real_executor,
        outbox=outbox,
        audit_sink=recording,
    )
    return Harness(session_factory, tables, recording, planner, real_executor, outbox)


@pytest.fixture()
def harness() -> Harness:
    return build_harness()


def table_rows(session: Session, name: str) -> list[dict[str, object]]:
    table = Base.metadata.tables[name]
    statement = select(table).order_by(*table.primary_key.columns)
    return [dict(row) for row in session.execute(statement).mappings()]


def outbox_rows(harness: Harness, session: Session) -> list[dict[str, object]]:
    return [dict(row) for row in session.execute(select(harness.tables.outbox)).mappings()]


def event_types(harness: Harness) -> list[AuditEventType]:
    return [event.event_type for event in harness.sink.events]


def test_happy_path_counts_and_retained_invoice(harness: Harness) -> None:
    with harness.session_factory() as session:
        result = harness.planner.erase_subject(session, "1", refs=REFS)
        session.commit()
    assert result.subject_id == "1"
    assert result.deleted == {"comments": 2, "order_items": 1, "orders": 1}
    assert result.anonymized == {"users": 1}
    assert result.retained == {"invoices": 1}
    assert result.enqueued_external == ("crm", "stripe")
    assert result.completed_at.tzinfo is not None
    with harness.session_factory() as session:
        alice, bob = table_rows(session, "users")
        assert alice["email"] != "alice@example.com"
        assert alice["name"] != "Alice Doe"
        assert alice["theme"] == "dark"
        assert bob["email"] == "bob@example.com"
        invoices = table_rows(session, "invoices")
        assert {"id": 1, "user_id": 1, "billing_address": "1 Alice Street"} in invoices


def test_no_cross_subject_bleed(harness: Harness) -> None:
    with harness.session_factory() as session:
        before = {
            name: [row for row in table_rows(session, name) if row.get("user_id") == 2]
            for name in ("invoices", "orders", "comments")
        }
        harness.planner.erase_subject(session, "1", refs=REFS)
        session.commit()
    with harness.session_factory() as session:
        for name, rows in before.items():
            assert [row for row in table_rows(session, name) if row.get("user_id") == 2] == rows
        assert [row["id"] for row in table_rows(session, "order_items")] == [2]


def test_audit_sequence_and_payloads(harness: Harness) -> None:
    with harness.session_factory() as session:
        harness.planner.erase_subject(session, "1", refs=REFS)
        session.commit()
    events = harness.sink.events
    assert event_types(harness) == [
        AuditEventType.ERASURE_REQUESTED,
        *[AuditEventType.ERASURE_STEP_SUCCEEDED] * 5,
        AuditEventType.ERASURE_LOCAL_COMPLETED,
    ]
    assert all(event.subject_ref == "1" for event in events)
    assert events[0].payload == {"local_steps": 5, "external_steps": 2, "refs": 2}
    succeeded = [event.payload for event in events[1:6]]
    assert {"target": "invoices", "strategy": "retain", "rows": 1} in succeeded
    assert {"target": "users", "strategy": "anonymize", "rows": 1} in succeeded
    assert {"target": "comments", "strategy": "delete", "rows": 2} in succeeded
    assert events[-1].payload == {
        "deleted": 4,
        "anonymized": 1,
        "retained": 1,
        "enqueued": 2,
        "skipped_resolvers": "",
    }


def test_refs_route_to_the_resolver_named_by_their_kind(harness: Harness) -> None:
    """ADR 0008: kind == resolver name; several matching refs ⇒ several entries."""
    refs = (*REFS, SubjectRef(kind="stripe", value="cus_2"))
    with harness.session_factory() as session:
        harness.planner.erase_subject(session, "1", refs=refs)
        rows = outbox_rows(harness, session)
        assert {(row["resolver"], row["ref_value"]) for row in rows} == {
            ("crm", "crm-1"),
            ("stripe", "cus_1"),
            ("stripe", "cus_2"),
        }
        assert all(row["status"] == "pending" for row in rows)
        assert len({row["entry_id"] for row in rows}) == 3
        session.commit()


def test_unmatched_ref_kind_fails_loudly_before_any_event(harness: Harness) -> None:
    refs = (SubjectRef(kind="stripe_customer", value="cus_1"),)
    with (
        harness.session_factory() as session,
        pytest.raises(ResolverError, match="stripe_customer"),
    ):
        harness.planner.erase_subject(session, "1", refs=refs)
    assert harness.sink.events == []
    with harness.session_factory() as session:
        assert len(table_rows(session, "comments")) == 3


def test_resolver_without_matching_ref_is_skipped_and_audited(harness: Harness) -> None:
    """ADR 0008: no matching ref is a complete answer, recorded, not an error."""
    with harness.session_factory() as session:
        result = harness.planner.erase_subject(session, "1", refs=REFS[:1])
        session.commit()
    assert result.enqueued_external == ("crm",)
    assert harness.sink.events[-1].payload["skipped_resolvers"] == "stripe"
    with harness.session_factory() as session:
        assert [row["resolver"] for row in outbox_rows(harness, session)] == ["crm"]


def test_rollback_discards_rows_and_outbox_but_keeps_audit(harness: Harness) -> None:
    with harness.session_factory() as session:
        harness.planner.erase_subject(session, "1", refs=REFS)
        session.rollback()
    with harness.session_factory() as session:
        users = table_rows(session, "users")
        assert len(users) == 2
        assert users[0]["email"] == "alice@example.com"
        assert len(table_rows(session, "comments")) == 3
        assert outbox_rows(harness, session) == []
    assert AuditEventType.ERASURE_LOCAL_COMPLETED in event_types(harness)


def test_mid_stream_failure_is_audited_and_propagates(harness: Harness) -> None:
    data_map = collect_data_map(Base.metadata)
    failing = ErasurePlanner(
        data_map,
        resolve_subject_graph(data_map, Base.registry),
        executor=FailingExecutor(harness.executor, fail_at="orders"),
        outbox=harness.outbox,
        audit_sink=harness.sink,
    )
    with harness.session_factory() as session:
        with pytest.raises(RuntimeError, match="injected fault"):
            failing.erase_subject(session, "1")
        session.rollback()
    assert event_types(harness)[-1] == AuditEventType.ERASURE_STEP_FAILED
    assert harness.sink.events[-1].payload == {
        "target": "orders",
        "strategy": "delete",
        "error": "RuntimeError",
    }
    with harness.session_factory() as session:
        assert len(table_rows(session, "comments")) == 3
        assert len(table_rows(session, "order_items")) == 2
        assert outbox_rows(harness, session) == []


def test_enqueue_failure_is_audited_as_outbox_step() -> None:
    harness = build_harness(outbox_cls=ExplodingOutbox, resolvers=("crm",))
    with harness.session_factory() as session:
        with pytest.raises(RuntimeError, match="outbox down"):
            harness.planner.erase_subject(session, "1", refs=REFS[:1])
        session.rollback()
    assert harness.sink.events[-1].event_type == AuditEventType.ERASURE_STEP_FAILED
    assert harness.sink.events[-1].payload == {
        "target": "outbox",
        "strategy": "enqueue",
        "error": "RuntimeError",
    }


def test_unwired_planner_refuses_loudly() -> None:
    data_map = collect_data_map(Base.metadata)
    planner = ErasurePlanner(data_map, resolve_subject_graph(data_map, Base.registry))
    engine = create_engine("sqlite://", poolclass=StaticPool)
    with (
        sessionmaker(engine)() as session,
        pytest.raises(ConfigurationError, match=r"executor.*outbox.*audit_sink"),
    ):
        planner.erase_subject(session, "1")
    engine.dispose()


def test_failing_sink_stops_the_erasure_before_any_row_changes() -> None:
    harness = build_harness(sink=ExplodingSink(), resolvers=())
    with harness.session_factory() as session:
        with pytest.raises(RuntimeError, match="sink down"):
            harness.planner.erase_subject(session, "1")
        session.rollback()
    with harness.session_factory() as session:
        assert len(table_rows(session, "comments")) == 3
        assert table_rows(session, "users")[0]["email"] == "alice@example.com"


def test_transient_sink_failure_after_a_step_audits_it_as_failed() -> None:
    """A step whose outcome cannot be recorded counts as failed (ADR 0009).

    The third append (the second step's success record) hiccups; the
    failure event for that step still lands because the sink recovers.
    """
    flaky = FlakySink(fail_on_call=3)
    harness = build_harness(sink=flaky, resolvers=())
    with harness.session_factory() as session:
        with pytest.raises(RuntimeError, match="sink hiccup"):
            harness.planner.erase_subject(session, "1")
        session.rollback()
    assert event_types(harness) == [
        AuditEventType.ERASURE_REQUESTED,
        AuditEventType.ERASURE_STEP_SUCCEEDED,
        AuditEventType.ERASURE_STEP_FAILED,
    ]
    failed = harness.sink.events[-1].payload
    assert failed["target"] == "invoices"
    assert failed["error"] == "RuntimeError"
    with harness.session_factory() as session:
        assert len(table_rows(session, "comments")) == 3


def test_rerun_for_an_erased_subject_is_a_no_op_success(harness: Harness) -> None:
    """Deleted tables stay empty; the surviving (anonymized/retained) rows
    re-match by subject id and are reported again; matched external work is
    re-enqueued with fresh idempotency keys (resolvers converge on it)."""
    with harness.session_factory() as session:
        harness.planner.erase_subject(session, "1", refs=REFS)
        session.commit()
    with harness.session_factory() as session:
        rerun = harness.planner.erase_subject(session, "1", refs=REFS)
        session.commit()
    assert rerun.deleted == {"comments": 0, "order_items": 0, "orders": 0}
    assert rerun.anonymized == {"users": 1}
    assert rerun.retained == {"invoices": 1}
    assert rerun.enqueued_external == ("crm", "stripe")
    with harness.session_factory() as session:
        rows = outbox_rows(harness, session)
        assert len(rows) == 4
        assert len({UUID(str(row["entry_id"])) for row in rows}) == 4


def test_audit_events_are_stamped_utc(harness: Harness) -> None:
    with harness.session_factory() as session:
        harness.planner.erase_subject(session, "1", refs=REFS)
        session.commit()
    assert harness.sink.events
    for event in harness.sink.events:
        assert event.occurred_at.utcoffset() == timedelta(0)


def test_outbox_entries_are_stamped_utc() -> None:
    harness = build_harness(outbox_cls=CapturingOutbox)
    with harness.session_factory() as session:
        harness.planner.erase_subject(session, "1", refs=REFS)
        session.commit()
    captured = harness.outbox.captured  # type: ignore[attr-defined]
    assert len(captured) == 2
    for entry in captured:
        assert entry.enqueued_at.utcoffset() == timedelta(0)


def test_multiple_skipped_resolvers_are_comma_joined(harness: Harness) -> None:
    """No refs at all: both registered resolvers are skipped, joined with ','."""
    with harness.session_factory() as session:
        harness.planner.erase_subject(session, "1")
        session.commit()
    assert harness.sink.events[-1].payload["skipped_resolvers"] == "crm,stripe"


def test_wiring_refusal_names_exactly_the_missing_pieces() -> None:
    """The refusal lists precisely the absent collaborators, comma-joined."""
    data_map = collect_data_map(Base.metadata)
    graph = resolve_subject_graph(data_map, Base.registry)
    full = build_harness()
    cases: dict[str, dict[str, object]] = {
        "executor": {"outbox": full.outbox, "audit_sink": full.sink},
        "outbox": {"executor": full.executor, "audit_sink": full.sink},
        "audit_sink": {"executor": full.executor, "outbox": full.outbox},
        "executor, outbox": {"audit_sink": full.sink},
    }
    engine = create_engine("sqlite://", poolclass=StaticPool)
    try:
        for missing, wiring in cases.items():
            planner = ErasurePlanner(data_map, graph, **wiring)  # type: ignore[arg-type]
            with (
                sessionmaker(engine)() as session,
                pytest.raises(ConfigurationError) as err,
            ):
                planner.erase_subject(session, "1")
            assert str(err.value) == f"erase_subject needs a planner wired with: {missing}"
    finally:
        engine.dispose()
