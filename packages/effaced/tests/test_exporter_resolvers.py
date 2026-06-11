"""Exporter resolver fan-out — kind matching, fault injection, loop misuse."""

from __future__ import annotations

import asyncio

import pytest
from conftest import Base, RecordingAuditSink, seed_two_subjects
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from effaced import (
    AuditEventType,
    ExportBundle,
    Exporter,
    ExportRecord,
    PiiCategory,
    ResolverErasure,
    ResolverError,
    ResolverExport,
    ResolverRegistry,
    SubjectRef,
    collect_data_map,
    resolve_subject_graph,
)

LOCAL_RECORD_COUNT = 4


class StaticResolver:
    """A resolver double returning one fixed record per call."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.calls: list[SubjectRef] = []

    @property
    def name(self) -> str:
        return self._name

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        self.calls.append(ref)
        record = ExportRecord(
            source=self._name,
            field="profile",
            category=PiiCategory.CONTACT,
            value=f"{self._name}:{ref.value}",
        )
        return ResolverExport(resolver=self._name, records=(record,))

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        raise NotImplementedError


class ExplodingResolver:
    """A resolver double whose export always fails."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        msg = "external system unavailable"
        raise RuntimeError(msg)

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        raise NotImplementedError


def build(
    engine: Engine,
    sink: RecordingAuditSink,
    *resolvers: StaticResolver | ExplodingResolver,
) -> Exporter:
    """An exporter on the seeded shared schema with the given resolvers."""
    with sessionmaker(engine)() as session:
        seed_two_subjects(session)
    registry = ResolverRegistry()
    for resolver in resolvers:
        registry.register(resolver)
    data_map = collect_data_map(Base.metadata)
    graph = resolve_subject_graph(data_map, Base.registry)
    return Exporter(data_map, graph, Base.metadata, sink, registry)


def export(engine: Engine, exporter: Exporter, refs: tuple[SubjectRef, ...]) -> ExportBundle:
    with sessionmaker(engine)() as session:
        return exporter.export_subject(session, "1", refs=refs)


def ref(kind: str, value: str = "ext-1") -> SubjectRef:
    return SubjectRef(kind=kind, value=value)


def test_resolver_records_merge_in_registration_order(sqlite_engine: Engine) -> None:
    exporter = build(
        sqlite_engine, RecordingAuditSink(), StaticResolver("crm"), StaticResolver("mail")
    )
    bundle = export(sqlite_engine, exporter, (ref("mail"), ref("crm")))
    external = [record.source for record in bundle.records[LOCAL_RECORD_COUNT:]]
    assert external == ["crm", "mail"]
    assert bundle.incomplete_sources == ()


def test_failing_resolver_lands_in_incomplete_sources(sqlite_engine: Engine) -> None:
    """Fault injection: the bundle completes, the failure is loud, not fatal."""
    sink = RecordingAuditSink()
    exporter = build(sqlite_engine, sink, StaticResolver("crm"), ExplodingResolver("bad"))
    bundle = export(sqlite_engine, exporter, (ref("crm"), ref("bad")))
    assert bundle.incomplete_sources == ("bad",)
    assert [r.source for r in bundle.records[LOCAL_RECORD_COUNT:]] == ["crm"]
    completed = sink.events[-1]
    assert completed.event_type is AuditEventType.EXPORT_COMPLETED
    assert completed.payload["incomplete_source_count"] == 1
    assert completed.payload["incomplete_sources"] == "bad"


def test_local_records_survive_resolver_failure(sqlite_engine: Engine) -> None:
    exporter = build(sqlite_engine, RecordingAuditSink(), ExplodingResolver("bad"))
    bundle = export(sqlite_engine, exporter, (ref("bad"),))
    assert len(bundle.records) == LOCAL_RECORD_COUNT
    assert bundle.incomplete_sources == ("bad",)


def test_resolver_without_matching_ref_is_skipped_not_incomplete(
    sqlite_engine: Engine,
) -> None:
    """No identity in a system is a complete answer, not a failure."""
    sink = RecordingAuditSink()
    exporter = build(sqlite_engine, sink, StaticResolver("crm"), StaticResolver("stripe"))
    bundle = export(sqlite_engine, exporter, (ref("crm"),))
    assert bundle.incomplete_sources == ()
    assert [r.source for r in bundle.records[LOCAL_RECORD_COUNT:]] == ["crm"]
    assert sink.events[-1].payload["skipped_resolvers"] == "stripe"


def test_ref_without_resolver_raises_before_any_audit_event(
    sqlite_engine: Engine,
) -> None:
    sink = RecordingAuditSink()
    exporter = build(sqlite_engine, sink, StaticResolver("crm"))
    with pytest.raises(ResolverError, match="ghost"):
        export(sqlite_engine, exporter, (ref("ghost"),))
    assert sink.events == []


def test_multiple_refs_fan_out_to_same_resolver(sqlite_engine: Engine) -> None:
    crm = StaticResolver("crm")
    exporter = build(sqlite_engine, RecordingAuditSink(), crm)
    bundle = export(sqlite_engine, exporter, (ref("crm", "ext-1"), ref("crm", "ext-2")))
    assert [r.value for r in crm.calls] == ["ext-1", "ext-2"]
    external = [record.value for record in bundle.records[LOCAL_RECORD_COUNT:]]
    assert external == ["crm:ext-1", "crm:ext-2"]


def test_no_registry_export_works_inside_a_running_loop(sqlite_engine: Engine) -> None:
    """Without fan-out there is no internal loop, so the run_sync path works."""
    with sessionmaker(sqlite_engine)() as session:
        seed_two_subjects(session)
    data_map = collect_data_map(Base.metadata)
    graph = resolve_subject_graph(data_map, Base.registry)
    exporter = Exporter(data_map, graph, Base.metadata, RecordingAuditSink())

    async def run() -> ExportBundle:
        with sessionmaker(sqlite_engine)() as session:
            return exporter.export_subject(session, "1")

    bundle = asyncio.run(run())
    assert len(bundle.records) == LOCAL_RECORD_COUNT


def test_fan_out_on_a_loop_thread_raises_runtime_error(sqlite_engine: Engine) -> None:
    """ADR 0006: resolver fan-out on an event-loop thread is the documented misuse."""
    exporter = build(sqlite_engine, RecordingAuditSink(), StaticResolver("crm"))

    async def run() -> ExportBundle:
        with sessionmaker(sqlite_engine)() as session:
            return exporter.export_subject(session, "1", refs=(ref("crm"),))

    with pytest.raises(RuntimeError):
        asyncio.run(run())
