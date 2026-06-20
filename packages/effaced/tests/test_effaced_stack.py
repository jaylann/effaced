"""EffacedStack.from_base and from_manifest wire every component correctly."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import Base, RecordingAuditSink, StatefulResolver, seed_two_subjects
from sqlalchemy import Engine, create_engine, inspect, select
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
    collect_data_map,
)
from effaced.adapters.sqlalchemy.storage.subject_erasures_table import (
    SUBJECT_ERASURE_COMPLETED,
)

# Each conftest table's parent on the path to the subject, for rewriting the
# manifest's relationship-name subject paths into the FK resolver's table-name
# form (``order.user`` -> ``orders.users``).
_PARENTS = {"invoices": "users", "orders": "users", "order_items": "orders", "comments": "users"}


def _table_name_path(table: str, parents: dict[str, str]) -> str:
    """The dotted table-name path from one table up to the subject table."""
    segments: list[str] = []
    name = table
    while name in parents:
        parent = parents[name]
        segments.append(parent)
        name = parent
    return ".".join(segments)


def _manifest_payload() -> dict[str, Any]:
    """The conftest schema's manifest, with subject paths as target-table names."""
    payload: dict[str, Any] = collect_data_map(Base.metadata).to_payload()
    for entry in payload["tables"]:
        link = entry["subject_link"]
        if link is not None and link["path"]:
            link["path"] = _table_name_path(entry["name"], _PARENTS)
    return payload


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
    # from_base mounts the owned tables (incl. effaced_subject_erasures, which
    # the planner's default SubjectErasureLock writes) after the fixture's
    # create_all; a second idempotent create_all materializes them.
    Base.metadata.create_all(sqlite_engine)
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


def test_from_base_erasure_is_guarded_by_default(sqlite_engine: Engine) -> None:
    """The stack wires the SubjectErasureLock by default (ADR 0026).

    Erasing through the stack writes a tombstone and marks it completed — the
    proof the guard is active out of the box, not opt-in. (SQLite drops
    ``FOR UPDATE``, so the *serialization* is proven in the Postgres suite;
    here we assert the wiring took effect via the durable tombstone.)
    """
    stack = _stack(sqlite_engine)
    Base.metadata.create_all(sqlite_engine)
    with stack.session_factory() as session:
        seed_two_subjects(session)
    with stack.session_factory.begin() as session:
        stack.planner.erase_subject(session, "1")
    with stack.session_factory() as session:
        rows = [
            dict(row) for row in session.execute(select(stack.tables.subject_erasures)).mappings()
        ]
    assert len(rows) == 1
    assert rows[0]["subject_ref"] == "1"
    assert rows[0]["status"] == SUBJECT_ERASURE_COMPLETED
    assert rows[0]["erased_at"] is not None


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


def _manifest_stack(engine: Engine, **kwargs: object) -> EffacedStack:
    return EffacedStack.from_manifest(
        sessionmaker(engine),
        engine,
        _manifest_payload(),
        audit_sink=RecordingAuditSink(),
        **kwargs,  # type: ignore[arg-type]  # kwargs forwarded verbatim in tests
    )


def test_from_manifest_wires_all_handles(sqlite_engine: Engine) -> None:
    stack = _manifest_stack(sqlite_engine)
    assert {entry.name for entry in stack.data_map.tables} >= {"users", "invoices"}
    assert stack.tables.audit_events.name == "effaced_audit_events"
    assert stack.registry.all() == ()


def test_from_manifest_reflects_only_the_manifest_tables(sqlite_engine: Engine) -> None:
    """Reflection is scoped to the manifest — unannotated tables stay out of the graph."""
    stack = _manifest_stack(sqlite_engine)
    reflected = set(stack.metadata.tables)
    # app_settings and tags hold no PII and are absent from the manifest, so
    # they are never reflected into the graph.
    assert "app_settings" not in reflected
    assert "tags" not in reflected
    assert {"users", "invoices", "orders", "order_items", "comments"} <= reflected


def test_from_manifest_engines_share_the_wiring(sqlite_engine: Engine) -> None:
    """The handles are live: export and erase run end-to-end, audited."""
    stack = _manifest_stack(sqlite_engine)
    # Materialize the owned tables the stack mounted (the planner's default
    # SubjectErasureLock writes effaced_subject_erasures).
    Base.metadata.create_all(sqlite_engine)
    with stack.session_factory() as session:
        seed_two_subjects(session)
    with stack.session_factory.begin() as session:
        bundle = stack.exporter.export_subject(session, "1")
        assert any(record.value == "alice@example.com" for record in bundle.records)
    with stack.session_factory.begin() as session:
        result = stack.planner.erase_subject(session, "1")
        assert result.anonymized.get("users") == 1
    sink = stack.audit_sink
    assert isinstance(sink, RecordingAuditSink)
    event_types = {event.event_type for event in sink.events}
    assert AuditEventType.EXPORT_COMPLETED in event_types
    assert AuditEventType.ERASURE_LOCAL_COMPLETED in event_types


def test_from_manifest_matches_from_base(sqlite_engine: Engine) -> None:
    """The same schema yields identical erasure plans through either entry point."""
    base_stack = _stack(sqlite_engine)
    manifest_stack = _manifest_stack(sqlite_engine)
    assert (
        manifest_stack.planner.plan("1").model_dump() == base_stack.planner.plan("1").model_dump()
    )


def test_from_manifest_rejects_resolvers_and_registry_together(sqlite_engine: Engine) -> None:
    with pytest.raises(ConfigurationError):
        _manifest_stack(
            sqlite_engine,
            resolvers=(StatefulResolver("crm", set()),),
            registry=ResolverRegistry(),
        )


def test_from_manifest_validates_config_before_reflecting() -> None:
    """The resolvers-vs-registry guard fails fast, before any reflection runs.

    The engine has no tables, so reflecting the manifest would raise
    ``ManifestError``; an invalid resolvers+registry combo must raise
    ``ConfigurationError`` first — pinning that bad config is rejected before
    any resolution work touches the database.
    """
    empty_engine = create_engine("sqlite://", poolclass=StaticPool)
    with pytest.raises(ConfigurationError):
        EffacedStack.from_manifest(
            sessionmaker(empty_engine),
            empty_engine,
            _manifest_payload(),
            resolvers=(StatefulResolver("crm", set()),),
            registry=ResolverRegistry(),
        )
    empty_engine.dispose()


def test_from_base_validates_config_before_collecting(sqlite_engine: Engine) -> None:
    """The resolvers-vs-registry guard fails fast, before data-map collection."""
    with pytest.raises(ConfigurationError):
        EffacedStack.from_base(
            Base,
            sessionmaker(sqlite_engine),
            resolvers=(StatefulResolver("crm", set()),),
            registry=ResolverRegistry(),
        )


def test_from_manifest_registers_resolvers(sqlite_engine: Engine) -> None:
    resolver = StatefulResolver("crm", {"c-1"})
    stack = _manifest_stack(sqlite_engine, resolvers=(resolver,))
    assert stack.registry.get("crm") is resolver


def test_from_manifest_default_audit_sink_is_database_backed(sqlite_engine: Engine) -> None:
    stack = EffacedStack.from_manifest(
        sessionmaker(sqlite_engine), sqlite_engine, _manifest_payload()
    )
    assert isinstance(stack.audit_sink, DatabaseAuditSink)
