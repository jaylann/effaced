"""EffacedStack.from_base wires every component correctly."""

from __future__ import annotations

import pytest
from conftest import Base, RecordingAuditSink, StatefulResolver, seed_two_subjects
from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    AuditEventType,
    ConfigurationError,
    DatabaseAuditSink,
    EffacedStack,
    OutboxStatus,
    ResolverRegistry,
    SubjectRef,
)


def _stack(engine: Engine, **kwargs: object) -> EffacedStack:
    return EffacedStack.from_base(
        Base,
        sessionmaker(engine),
        audit_sink=RecordingAuditSink(),
        **kwargs,  # type: ignore[arg-type]  # kwargs forwarded verbatim in tests
    )


def test_from_base_wires_all_handles(sqlite_engine: Engine) -> None:
    stack = _stack(sqlite_engine)
    assert stack.metadata is Base.metadata
    assert {entry.name for entry in stack.data_map.tables} >= {"users", "invoices"}
    assert stack.tables.audit_events.name == "effaced_audit_events"
    assert stack.registry.all() == ()


def test_from_base_engines_share_the_wiring(sqlite_engine: Engine) -> None:
    """The handles are live: export and erase run end-to-end, audited."""
    stack = _stack(sqlite_engine)
    session_factory = stack.session_factory
    with session_factory() as session:
        seed_two_subjects(session)
    with session_factory.begin() as session:
        bundle = stack.exporter.export_subject(session, "1")
        assert any(record.value == "alice@example.com" for record in bundle.records)
    with session_factory.begin() as session:
        result = stack.planner.erase_subject(session, "1")
        assert result.anonymized.get("users") == 1
    sink = stack.audit_sink
    assert isinstance(sink, RecordingAuditSink)
    event_types = {event.event_type for event in sink.events}
    assert AuditEventType.EXPORT_COMPLETED in event_types
    assert AuditEventType.ERASURE_LOCAL_COMPLETED in event_types


def test_from_base_registers_resolvers(sqlite_engine: Engine) -> None:
    resolver = StatefulResolver("crm", {"c-1"})
    stack = _stack(sqlite_engine, resolvers=(resolver,))
    assert stack.registry.get("crm") is resolver


def test_from_base_enqueues_external_refs(sqlite_engine: Engine) -> None:
    """Erasure with a ref lands an outbox entry — outbox and planner share wiring."""
    stack = _stack(sqlite_engine, resolvers=(StatefulResolver("crm", {"c-1"}),))
    # The fixture ran create_all before from_base mounted the owned tables;
    # a second (idempotent) create_all adds the missing effaced_* tables.
    Base.metadata.create_all(sqlite_engine)
    with stack.session_factory() as session:
        seed_two_subjects(session)
    with stack.session_factory.begin() as session:
        result = stack.planner.erase_subject(
            session, "1", refs=(SubjectRef(kind="crm", value="c-1"),)
        )
    assert result.enqueued_external == ("crm",)
    assert stack.outbox.status_counts()[OutboxStatus.PENDING] == 1


def test_from_base_rejects_resolvers_and_registry_together(sqlite_engine: Engine) -> None:
    with pytest.raises(ConfigurationError):
        _stack(
            sqlite_engine,
            resolvers=(StatefulResolver("crm", set()),),
            registry=ResolverRegistry(),
        )


def test_from_base_accepts_prebuilt_registry(sqlite_engine: Engine) -> None:
    registry = ResolverRegistry()
    resolver = StatefulResolver("crm", set())
    registry.register(resolver)
    stack = _stack(sqlite_engine, registry=registry)
    assert stack.registry is registry


def test_from_base_default_audit_sink_is_database_backed(sqlite_engine: Engine) -> None:
    stack = EffacedStack.from_base(Base, sessionmaker(sqlite_engine))
    assert isinstance(stack.audit_sink, DatabaseAuditSink)


def test_from_base_executes_no_ddl() -> None:
    """Construction never creates tables — the owned tables ride migrations."""
    empty_engine = create_engine("sqlite://", poolclass=StaticPool)
    _stack(empty_engine)
    assert "effaced_outbox" in Base.metadata.tables  # mounted on the metadata...
    assert not inspect(empty_engine).has_table("effaced_outbox")  # ...but no DDL ran
    empty_engine.dispose()
