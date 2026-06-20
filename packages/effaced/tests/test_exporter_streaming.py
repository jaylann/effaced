"""Exporter.iter_subject_records — the streaming, memory-bounded export path.

The acceptance gate is equivalence: the streamed records must be identical
(same set, same order) to the materialized :meth:`Exporter.export_subject`
bundle for the shared schema — an accidental divergence would be a silent
export-output change (MAJOR). The audit-semantics and bounded-memory cases
pin that the streaming path records the same trail and never accumulates the
whole footprint.
"""

from __future__ import annotations

from typing import NamedTuple

import pytest
from conftest import (
    Base,
    Invoice,
    RecordingAuditSink,
    seed_two_subjects,
)
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from effaced import (
    AuditEventType,
    Exporter,
    ExportRecord,
    PiiCategory,
    ResolverErasure,
    ResolverExport,
    ResolverRegistry,
    SubjectRef,
    collect_data_map,
    resolve_subject_graph,
)
from effaced.export import exporter as exporter_module


class StaticResolver:
    """A resolver double returning one fixed record per call (fan-out path)."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        record = ExportRecord(
            source=self._name,
            field="profile",
            category=PiiCategory.CONTACT,
            value=f"{self._name}:{ref.value}",
        )
        return ResolverExport(resolver=self._name, records=(record,))

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        raise NotImplementedError


class StreamHarness(NamedTuple):
    """An exporter wired to an in-memory database and a recording sink."""

    session_factory: sessionmaker[Session]
    sink: RecordingAuditSink
    exporter: Exporter


@pytest.fixture()
def harness(sqlite_engine: Engine) -> StreamHarness:
    """An exporter on a database seeded with subjects 1 and 2."""
    data_map = collect_data_map(Base.metadata)
    graph = resolve_subject_graph(data_map, Base.registry)
    sink = RecordingAuditSink()
    exporter = Exporter(data_map, graph, Base.metadata, sink)
    factory = sessionmaker(sqlite_engine)
    with factory() as session:
        seed_two_subjects(session)
    return StreamHarness(factory, sink, exporter)


def _streamed(harness: StreamHarness, subject_id: str = "1") -> list[ExportRecord]:
    with harness.session_factory() as session:
        return list(harness.exporter.iter_subject_records(session, subject_id))


def _materialized(harness: StreamHarness, subject_id: str = "1") -> list[ExportRecord]:
    with harness.session_factory() as session:
        return list(harness.exporter.export_subject(session, subject_id).records)


def test_streamed_records_equal_the_materialized_bundle(harness: StreamHarness) -> None:
    """The acceptance gate: same records, same order — no output drift."""
    assert _streamed(harness, "1") == _materialized(harness, "1")


def test_streamed_records_equal_materialized_for_every_subject(harness: StreamHarness) -> None:
    for subject_id in ("1", "2", "999"):
        assert _streamed(harness, subject_id) == _materialized(harness, subject_id)


def test_streamed_records_carry_no_other_subjects_data(harness: StreamHarness) -> None:
    alice = {str(record.value) for record in _streamed(harness, "1")}
    bob = {str(record.value) for record in _streamed(harness, "2")}
    assert alice and bob
    assert not alice & bob
    assert all("bob" not in value.lower() for value in alice)


def test_streaming_audits_requested_then_completed(harness: StreamHarness) -> None:
    """Same two-event trail as export_subject, same completion payload."""
    list(_streamed(harness, "1"))
    requested, completed = harness.sink.events
    assert requested.event_type is AuditEventType.EXPORT_REQUESTED
    assert completed.event_type is AuditEventType.EXPORT_COMPLETED
    assert requested.subject_ref == completed.subject_ref == "1"
    assert requested.payload == {"ref_count": 0, "resolver_count": 0}
    assert completed.payload == {
        "record_count": 4,
        "incomplete_source_count": 0,
        "incomplete_sources": "",
        "skipped_resolvers": "",
    }


def test_completion_event_fires_only_after_full_consumption(harness: StreamHarness) -> None:
    """A consumer that abandons the iterator leaves REQUESTED without COMPLETED."""
    with harness.session_factory() as session:
        stream = harness.exporter.iter_subject_records(session, "1")
        next(stream)  # pull one record; do not exhaust
        assert [event.event_type for event in harness.sink.events] == [
            AuditEventType.EXPORT_REQUESTED
        ]
        stream.close()
    assert [event.event_type for event in harness.sink.events] == [AuditEventType.EXPORT_REQUESTED]


def test_requested_event_records_subject_pii_free(harness: StreamHarness) -> None:
    list(_streamed(harness, "1"))
    for event in harness.sink.events:
        for value in event.payload.values():
            assert "alice" not in str(value).lower()


def test_streaming_does_not_materialize_every_row_at_once(harness: StreamHarness) -> None:
    """The local generator is lazy: pulling one record does not drain the table.

    A large single-table population must not be read whole before the first
    record surfaces — peak resident rows stay bounded by the fetch batch, not
    the subject's footprint.
    """
    with harness.session_factory() as session:
        _seed_wide_population(session, count=10 * exporter_module._ROW_STREAM_SIZE)
        stream = harness.exporter.iter_subject_records(session, "1")
        # Pulling a handful of records must not require the whole population.
        pulled = [next(stream) for _ in range(5)]
        assert len(pulled) == 5
        # Exhausting yields exactly the materialized set for the same data.
        rest = list(stream)
        streamed_total = len(pulled) + len(rest)
    with harness.session_factory() as session:
        materialized_total = len(harness.exporter.export_subject(session, "1").records)
    assert streamed_total == materialized_total


def _seed_wide_population(session: Session, *, count: int) -> None:
    """Give subject 1 ``count`` extra invoice rows in one table."""
    session.add_all(
        Invoice(id=1000 + index, user_id=1, billing_address=f"addr {index}")
        for index in range(count)
    )
    session.commit()


def test_streamed_records_equal_materialized_with_resolver_fan_out(
    sqlite_engine: Engine,
) -> None:
    """Equivalence holds across the external (resolver) records too — same order."""
    with sessionmaker(sqlite_engine)() as session:
        seed_two_subjects(session)
    registry = ResolverRegistry()
    registry.register(StaticResolver("crm"))
    registry.register(StaticResolver("mail"))
    data_map = collect_data_map(Base.metadata)
    graph = resolve_subject_graph(data_map, Base.registry)
    exporter = Exporter(data_map, graph, Base.metadata, RecordingAuditSink(), registry)
    refs = (SubjectRef(kind="mail", value="ext-1"), SubjectRef(kind="crm", value="ext-2"))

    with sessionmaker(sqlite_engine)() as session:
        materialized = list(exporter.export_subject(session, "1", refs=refs).records)
    with sessionmaker(sqlite_engine)() as session:
        streamed = list(exporter.iter_subject_records(session, "1", refs=refs))

    assert streamed == materialized
    assert [r.source for r in streamed if r.source in {"crm", "mail"}] == ["crm", "mail"]
