"""Rectifier.rectify_subject — category-keyed local UPDATE statements, saga fan-out, value-free audit."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple

import pytest
from conftest import Base, FakeResolver, RecordingAuditSink, seed_two_subjects
from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    AuditEventType,
    ConfigurationError,
    Correction,
    EffacedTables,
    ErasureStrategy,
    Outbox,
    PiiCategory,
    Rectifier,
    ResolverError,
    ResolverRectification,
    ResolverRegistry,
    RetentionPolicy,
    SubjectRef,
    bind_tables,
    collect_data_map,
    pii,
    resolve_subject_graph,
    subject_link,
)
from effaced.adapters.sqlalchemy import RectificationExecutor
from effaced.rectification import RectificationStep, RectificationStepExecutor

if TYPE_CHECKING:
    from effaced.manifest import SubjectGraph

REFS = (
    SubjectRef(kind="crm", value="crm-1"),
    SubjectRef(kind="stripe", value="cus_1"),
)

CORRECTIONS = (
    Correction(category=PiiCategory.CONTACT, value="corrected@example.com"),
    Correction(category=PiiCategory.IDENTITY, value="Corrected Name"),
    Correction(category=PiiCategory.FINANCIAL, value="9 Corrected Street"),
    Correction(category=PiiCategory.COMMUNICATION, value="a corrected message"),
)


class RectifyingFake:
    """A resolver double that implements ``rectify_subject`` — never called here."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def export_subject(self, ref: SubjectRef) -> object:
        raise NotImplementedError

    async def erase_subject(self, ref: SubjectRef) -> object:
        raise NotImplementedError

    async def rectify_subject(
        self, ref: SubjectRef, corrections: tuple[Correction, ...]
    ) -> ResolverRectification:
        return ResolverRectification(resolver=self._name)


class FailingRectifyExecutor:
    """Delegates to a real executor until it reaches the named table."""

    def __init__(self, inner: RectificationStepExecutor, fail_at: str) -> None:
        self._inner = inner
        self._fail_at = fail_at

    def execute(
        self,
        session: Session,
        graph: SubjectGraph,
        step: RectificationStep,
        subject_id: str,
        value: str | int | float | bool,
    ) -> int:
        if step.target == self._fail_at:
            msg = "injected fault"
            raise RuntimeError(msg)
        return self._inner.execute(session, graph, step, subject_id, value)


class Harness(NamedTuple):
    """A fully wired rectifier over the seeded two-subject schema."""

    session_factory: sessionmaker[Session]
    tables: EffacedTables
    sink: RecordingAuditSink
    rectifier: Rectifier
    executor: RectificationExecutor
    outbox: Outbox


def build_harness(
    *,
    executor: RectificationStepExecutor | None = None,
    sink: RecordingAuditSink | None = None,
    resolvers: tuple[object, ...] | None = None,
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
    for resolver in (
        resolvers if resolvers is not None else (RectifyingFake("crm"), RectifyingFake("stripe"))
    ):
        registry.register(resolver)  # type: ignore[arg-type]
    data_map = collect_data_map(Base.metadata)
    real_executor = RectificationExecutor(Base.metadata)
    outbox = Outbox(session_factory, tables.outbox)
    recording = sink if sink is not None else RecordingAuditSink()
    rectifier = Rectifier(
        data_map,
        resolve_subject_graph(data_map, Base.registry),
        registry,
        executor=executor if executor is not None else real_executor,
        outbox=outbox,
        audit_sink=recording,
    )
    return Harness(session_factory, tables, recording, rectifier, real_executor, outbox)


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


def test_corrects_every_local_column_of_the_category_including_multi_hop(
    harness: Harness,
) -> None:
    with harness.session_factory() as session:
        result = harness.rectifier.rectify_subject(session, "1", CORRECTIONS, refs=REFS)
        session.commit()
    assert result.subject_id == "1"
    assert result.rectified == {"order_items": 1, "invoices": 1, "users": 2}
    assert result.enqueued_external == ("crm", "stripe")
    assert result.skipped_resolvers == ()
    assert result.completed_at.tzinfo is not None
    with harness.session_factory() as session:
        alice, bob = table_rows(session, "users")
        assert alice["email"] == "corrected@example.com"
        assert alice["name"] == "Corrected Name"
        assert alice["theme"] == "dark"
        assert bob["email"] == "bob@example.com"
        first_item, second_item = table_rows(session, "order_items")
        assert first_item["gift_message"] == "a corrected message"
        assert second_item["gift_message"] == "a gift for bob"


def test_retain_columns_are_rectified_too(harness: Harness) -> None:
    """ADR 0013: erasure strategy never gates rectification — RETAIN included."""
    with harness.session_factory() as session:
        harness.rectifier.rectify_subject(session, "1", CORRECTIONS)
        session.commit()
    with harness.session_factory() as session:
        first, second = table_rows(session, "invoices")
        assert first["billing_address"] == "9 Corrected Street"
        assert second["billing_address"] == "2 Bob Street"


class StrategyBase(DeclarativeBase):
    metadata = MetaData()


class StrategyPerson(StrategyBase):
    """One category spread over DELETE, ANONYMIZE, and RETAIN columns."""

    __tablename__ = "strategy_people"
    __table_args__: ClassVar[dict[str, Any]] = {"info": subject_link("")}

    id: Mapped[int] = mapped_column(primary_key=True)
    email_delete: Mapped[str] = mapped_column(info=pii(PiiCategory.CONTACT))
    email_anon: Mapped[str] = mapped_column(
        info=pii(PiiCategory.CONTACT, erasure=ErasureStrategy.ANONYMIZE)
    )
    email_retain: Mapped[str] = mapped_column(
        info=pii(
            PiiCategory.CONTACT,
            erasure=ErasureStrategy.RETAIN,
            retention=RetentionPolicy(reason="legal duty", duration=timedelta(days=1)),
        )
    )


def test_anonymize_and_retain_strategies_do_not_gate_rectification() -> None:
    """One CONTACT correction rewrites the DELETE, ANONYMIZE, and RETAIN columns alike."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    effaced_metadata = MetaData()
    tables = bind_tables(effaced_metadata)
    StrategyBase.metadata.create_all(engine)
    effaced_metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    data_map = collect_data_map(StrategyBase.metadata)
    rectifier = Rectifier(
        data_map,
        resolve_subject_graph(data_map, StrategyBase.registry),
        executor=RectificationExecutor(StrategyBase.metadata),
        outbox=Outbox(session_factory, tables.outbox),
        audit_sink=RecordingAuditSink(),
    )
    with session_factory() as session:
        session.add(
            StrategyPerson(id=1, email_delete="a@old", email_anon="b@old", email_retain="c@old")
        )
        session.commit()
    with session_factory() as session:
        result = rectifier.rectify_subject(
            session, "1", (Correction(category=PiiCategory.CONTACT, value="new@example.com"),)
        )
        session.commit()
    assert result.rectified == {"strategy_people": 1}
    with session_factory() as session:
        (row,) = session.execute(select(StrategyBase.metadata.tables["strategy_people"])).mappings()
        assert row["email_delete"] == "new@example.com"
        assert row["email_anon"] == "new@example.com"
        assert row["email_retain"] == "new@example.com"
    engine.dispose()


def test_category_matching_nothing_local_is_not_an_error(harness: Harness) -> None:
    corrections = (Correction(category=PiiCategory.LOCATION, value="Berlin"),)
    with harness.session_factory() as session:
        result = harness.rectifier.rectify_subject(session, "1", corrections)
        session.commit()
    assert result.rectified == {}
    assert harness.sink.events[0].payload == {
        "categories": "location",
        "local_steps": 0,
        "external_steps": 0,
    }
    assert harness.sink.events[-1].event_type is AuditEventType.RECTIFICATION_LOCAL_COMPLETED


def test_audit_sequence_and_payloads(harness: Harness) -> None:
    with harness.session_factory() as session:
        harness.rectifier.rectify_subject(session, "1", CORRECTIONS, refs=REFS)
        session.commit()
    events = harness.sink.events
    assert event_types(harness) == [
        AuditEventType.RECTIFICATION_REQUESTED,
        *[AuditEventType.RECTIFICATION_STEP_SUCCEEDED] * 4,
        AuditEventType.RECTIFICATION_LOCAL_COMPLETED,
    ]
    assert all(event.subject_ref == "1" for event in events)
    assert events[0].payload == {
        "categories": "communication,contact,financial,identity",
        "local_steps": 4,
        "external_steps": 2,
    }
    succeeded = [event.payload for event in events[1:5]]
    assert {"target": "order_items", "category": "communication", "rows": 1} in succeeded
    assert {"target": "invoices", "category": "financial", "rows": 1} in succeeded
    assert {"target": "users", "category": "contact", "rows": 1} in succeeded
    assert {"target": "users", "category": "identity", "rows": 1} in succeeded
    assert events[-1].payload == {
        "rectified": 4,
        "enqueued": 2,
        "skipped_resolvers": "",
    }


def test_no_audit_payload_ever_carries_a_corrected_value(harness: Harness) -> None:
    """ADR 0013: old and new values never appear in any event."""
    with harness.session_factory() as session:
        harness.rectifier.rectify_subject(session, "1", CORRECTIONS, refs=REFS)
        session.commit()
    corrected = {correction.value for correction in CORRECTIONS}
    assert harness.sink.events
    for event in harness.sink.events:
        for value in event.payload.values():
            assert value not in corrected


def test_step_failure_is_audited_and_re_raises(harness: Harness) -> None:
    data_map = collect_data_map(Base.metadata)
    failing = Rectifier(
        data_map,
        resolve_subject_graph(data_map, Base.registry),
        executor=FailingRectifyExecutor(harness.executor, fail_at="users"),
        outbox=harness.outbox,
        audit_sink=harness.sink,
    )
    with harness.session_factory() as session:
        with pytest.raises(RuntimeError, match="injected fault"):
            failing.rectify_subject(session, "1", CORRECTIONS)
        session.rollback()
    assert event_types(harness)[-1] == AuditEventType.RECTIFICATION_STEP_FAILED
    assert harness.sink.events[-1].payload == {"target": "users", "error": "RuntimeError"}
    with harness.session_factory() as session:
        assert table_rows(session, "users")[0]["email"] == "alice@example.com"


def test_unmatched_ref_kind_fails_loudly_before_any_event(harness: Harness) -> None:
    refs = (SubjectRef(kind="ghost", value="g-1"),)
    with (
        harness.session_factory() as session,
        pytest.raises(ResolverError, match="ghost"),
    ):
        harness.rectifier.rectify_subject(session, "1", CORRECTIONS, refs=refs)
    assert harness.sink.events == []
    with harness.session_factory() as session:
        assert table_rows(session, "users")[0]["email"] == "alice@example.com"
        assert outbox_rows(harness, session) == []


def test_non_rectifying_resolver_is_skipped_and_recorded() -> None:
    """A registered resolver without rectify_subject is an honest skip, not an error."""
    harness = build_harness(resolvers=(FakeResolver("crm"), RectifyingFake("stripe")))
    with harness.session_factory() as session:
        result = harness.rectifier.rectify_subject(session, "1", CORRECTIONS, refs=REFS)
        session.commit()
    assert result.enqueued_external == ("stripe",)
    assert result.skipped_resolvers == ("crm",)
    assert harness.sink.events[-1].payload["skipped_resolvers"] == "crm"
    with harness.session_factory() as session:
        assert [row["resolver"] for row in outbox_rows(harness, session)] == ["stripe"]


def test_resolver_without_matching_ref_is_skipped(harness: Harness) -> None:
    with harness.session_factory() as session:
        result = harness.rectifier.rectify_subject(session, "1", CORRECTIONS, refs=REFS[:1])
        session.commit()
    assert result.enqueued_external == ("crm",)
    assert result.skipped_resolvers == ("stripe",)
    assert harness.sink.events[-1].payload["skipped_resolvers"] == "stripe"


def test_enqueued_entries_carry_operation_and_corrections(harness: Harness) -> None:
    with harness.session_factory() as session:
        harness.rectifier.rectify_subject(session, "1", CORRECTIONS, refs=REFS)
        rows = outbox_rows(harness, session)
        assert {(row["resolver"], row["ref_value"]) for row in rows} == {
            ("crm", "crm-1"),
            ("stripe", "cus_1"),
        }
        assert all(row["operation"] == "rectify" for row in rows)
        assert all(row["status"] == "pending" for row in rows)
        payloads = [row["payload"] for row in rows]
        for payload in payloads:
            assert isinstance(payload, dict)
            assert [Correction.model_validate(c) for c in payload["corrections"]] == list(
                CORRECTIONS
            )
        session.rollback()


def test_empty_corrections_raise_before_any_event(harness: Harness) -> None:
    with harness.session_factory() as session, pytest.raises(ValueError, match="correction"):
        harness.rectifier.rectify_subject(session, "1", ())
    assert harness.sink.events == []


def test_duplicate_categories_raise_before_any_event(harness: Harness) -> None:
    corrections = (
        Correction(category=PiiCategory.CONTACT, value="a@example.com"),
        Correction(category=PiiCategory.CONTACT, value="b@example.com"),
    )
    with harness.session_factory() as session, pytest.raises(ValueError, match="contact"):
        harness.rectifier.rectify_subject(session, "1", corrections)
    assert harness.sink.events == []


def test_rollback_discards_rows_and_outbox_but_keeps_audit(harness: Harness) -> None:
    with harness.session_factory() as session:
        harness.rectifier.rectify_subject(session, "1", CORRECTIONS, refs=REFS)
        session.rollback()
    with harness.session_factory() as session:
        users = table_rows(session, "users")
        assert users[0]["email"] == "alice@example.com"
        assert outbox_rows(harness, session) == []
    assert AuditEventType.RECTIFICATION_LOCAL_COMPLETED in event_types(harness)


def test_unwired_rectifier_refuses_loudly() -> None:
    data_map = collect_data_map(Base.metadata)
    rectifier = Rectifier(data_map, resolve_subject_graph(data_map, Base.registry))
    engine = create_engine("sqlite://", poolclass=StaticPool)
    with (
        sessionmaker(engine)() as session,
        pytest.raises(ConfigurationError, match=r"executor.*outbox.*audit_sink"),
    ):
        rectifier.rectify_subject(session, "1", CORRECTIONS)
    engine.dispose()


def test_no_cross_subject_bleed(harness: Harness) -> None:
    with harness.session_factory() as session:
        before = {
            name: table_rows(session, name)
            for name in ("users", "invoices", "orders", "order_items", "comments")
        }
        harness.rectifier.rectify_subject(session, "1", CORRECTIONS, refs=REFS)
        session.commit()
    with harness.session_factory() as session:
        for name, rows in before.items():
            after = table_rows(session, name)
            assert len(after) == len(rows)
            for old, new in zip(rows, after, strict=True):
                if old.get("user_id") == 2 or (name == "users" and old["id"] == 2):
                    assert new == old
