"""Exporter.export_subject — local collection, Art. 15 metadata, audit trail."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import NamedTuple

import pytest
from conftest import Base, RecordingAuditSink, seed_two_subjects
from sqlalchemy import Column, Engine, MetaData, String, Table, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import UserDefinedType

from effaced import (
    MANIFEST_SCHEMA_VERSION,
    AuditEventType,
    DataMap,
    ExportBundle,
    Exporter,
    LegalBasis,
    ManifestError,
    PiiCategory,
    PiiSpec,
    SubjectGraph,
    SubjectLink,
    SubjectResolutionError,
    TableAccessPlan,
    TableEntry,
    collect_data_map,
    resolve_subject_graph,
)
from effaced.manifest import ColumnEntry


class ExportHarness(NamedTuple):
    """An exporter wired to an in-memory database and a recording sink."""

    session_factory: sessionmaker[Session]
    sink: RecordingAuditSink
    exporter: Exporter


@pytest.fixture()
def unseeded(sqlite_engine: Engine) -> ExportHarness:
    """An exporter on an empty in-memory database."""
    data_map = collect_data_map(Base.metadata)
    graph = resolve_subject_graph(data_map, Base.registry)
    sink = RecordingAuditSink()
    exporter = Exporter(data_map, graph, Base.metadata, sink)
    return ExportHarness(sessionmaker(sqlite_engine), sink, exporter)


@pytest.fixture()
def harness(unseeded: ExportHarness) -> ExportHarness:
    """An exporter on a database seeded with subjects 1 and 2."""
    with unseeded.session_factory() as session:
        seed_two_subjects(session)
    return unseeded


def export(harness: ExportHarness, subject_id: str = "1") -> ExportBundle:
    with harness.session_factory() as session:
        return harness.exporter.export_subject(session, subject_id)


def test_golden_bundle_for_shared_schema(harness: ExportHarness) -> None:
    bundle = export(harness)
    by_key = {(record.source, record.field): record for record in bundle.records}
    assert set(by_key) == {
        ("users", "email"),
        ("users", "name"),
        ("invoices", "billing_address"),
        ("order_items", "gift_message"),
    }
    email = by_key["users", "email"]
    assert email.value == "alice@example.com"
    assert email.category is PiiCategory.CONTACT
    assert email.legal_basis is LegalBasis.CONTRACT
    assert email.purpose == "account login"
    assert email.retention_reason is None
    name = by_key["users", "name"]
    assert name.value == "Alice Doe"
    assert name.category is PiiCategory.IDENTITY
    assert name.legal_basis is None
    assert name.purpose is None
    billing = by_key["invoices", "billing_address"]
    assert billing.value == "1 Alice Street"
    assert billing.category is PiiCategory.FINANCIAL
    assert billing.retention_reason == "§147 AO invoice retention"
    gift = by_key["order_items", "gift_message"]
    assert gift.value == "a gift for alice"
    assert gift.category is PiiCategory.COMMUNICATION
    assert bundle.subject_id == "1"
    assert bundle.schema_version == MANIFEST_SCHEMA_VERSION
    assert bundle.incomplete_sources == ()


def test_nothing_from_unannotated_sources(harness: ExportHarness) -> None:
    bundle = export(harness)
    sources = {record.source for record in bundle.records}
    assert sources == {"users", "invoices", "order_items"}
    assert not any(record.field == "theme" for record in bundle.records)


def test_retained_field_is_still_exported(harness: ExportHarness) -> None:
    """RETAIN keeps data out of erasure, never out of an Art. 15 answer."""
    bundle = export(harness)
    retained = [record for record in bundle.records if record.retention_reason is not None]
    assert [(r.source, r.field) for r in retained] == [("invoices", "billing_address")]


def test_no_cross_subject_bleed(harness: ExportHarness) -> None:
    alice_values = {str(record.value) for record in export(harness, "1").records}
    bob_values = {str(record.value) for record in export(harness, "2").records}
    assert alice_values and bob_values
    assert not alice_values & bob_values
    assert all("bob" not in value.lower() for value in alice_values)
    assert all("alice" not in value.lower() for value in bob_values)


def test_unknown_subject_yields_empty_audited_bundle(harness: ExportHarness) -> None:
    bundle = export(harness, "999")
    assert bundle.records == ()
    assert bundle.incomplete_sources == ()
    assert [event.event_type for event in harness.sink.events] == [
        AuditEventType.EXPORT_REQUESTED,
        AuditEventType.EXPORT_COMPLETED,
    ]
    assert harness.sink.events[1].payload["record_count"] == 0


def test_export_on_empty_database(unseeded: ExportHarness) -> None:
    bundle = export(unseeded)
    assert bundle.records == ()
    assert bundle.incomplete_sources == ()


def test_generated_at_is_tz_aware_utc(harness: ExportHarness) -> None:
    bundle = export(harness)
    assert bundle.generated_at.tzinfo is not None
    assert bundle.generated_at.utcoffset() == timedelta(0)


def test_bundle_is_json_serializable(harness: ExportHarness) -> None:
    bundle = export(harness)
    payload = json.loads(json.dumps(bundle.model_dump(mode="json")))
    assert len(payload["records"]) == len(bundle.records)
    assert payload["subject_id"] == "1"


def test_session_is_read_only(harness: ExportHarness) -> None:
    before = _snapshot(harness.session_factory)
    with harness.session_factory() as session:
        harness.exporter.export_subject(session, "1")
        assert not session.new
        assert not session.dirty
        assert not session.deleted
    assert _snapshot(harness.session_factory) == before


def test_audit_requested_then_completed(harness: ExportHarness) -> None:
    export(harness)
    requested, completed = harness.sink.events
    assert requested.event_type is AuditEventType.EXPORT_REQUESTED
    assert completed.event_type is AuditEventType.EXPORT_COMPLETED
    assert requested.subject_ref == completed.subject_ref == "1"
    assert requested.occurred_at <= completed.occurred_at
    assert requested.payload == {"ref_count": 0, "resolver_count": 0}
    assert completed.payload == {
        "record_count": 4,
        "incomplete_source_count": 0,
        "incomplete_sources": "",
        "skipped_resolvers": "",
    }
    seeded_pii = ("alice", "bob", "Street", "gift")
    for event in harness.sink.events:
        for value in event.payload.values():
            assert not any(fragment.lower() in str(value).lower() for fragment in seeded_pii)


def test_mismatched_data_map_and_graph_raise_at_construction(
    harness: ExportHarness,
) -> None:
    data_map = collect_data_map(Base.metadata)
    graph = resolve_subject_graph(data_map, Base.registry)
    shrunk = DataMap(tables=data_map.tables[:-1])
    with pytest.raises(ManifestError, match="disagree"):
        Exporter(shrunk, graph, Base.metadata, harness.sink)


def test_table_missing_from_metadata_raises_at_construction(
    harness: ExportHarness,
) -> None:
    data_map = collect_data_map(Base.metadata)
    graph = resolve_subject_graph(data_map, Base.registry)
    with pytest.raises(ManifestError, match="not in the given metadata"):
        Exporter(data_map, graph, MetaData(), harness.sink)


def test_malformed_subject_id_raises_before_any_audit_event(
    harness: ExportHarness,
) -> None:
    with pytest.raises(SubjectResolutionError):
        export(harness, "not-a-number")
    assert harness.sink.events == []


class OpaqueId(UserDefinedType):
    """An id type effaced cannot interpret (``python_type`` is undefined)."""

    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        """Render as TEXT."""
        return "TEXT"


def test_uninterpretable_id_column_type_passes_subject_id_through() -> None:
    """No ``python_type`` on the id column: the dialect, not effaced, decides."""
    metadata = MetaData()
    members = Table(
        "members",
        metadata,
        Column("id", OpaqueId(), primary_key=True),
        Column("email", String()),
    )
    data_map = DataMap(
        tables=(
            TableEntry(
                name="members",
                subject_link=SubjectLink(path=""),
                columns=(ColumnEntry(name="email", spec=PiiSpec(category=PiiCategory.CONTACT)),),
            ),
        )
    )
    graph = SubjectGraph(
        subject_table="members",
        subject_id_column="id",
        accesses=(TableAccessPlan(table="members"),),
    )
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata.create_all(engine)
    exporter = Exporter(data_map, graph, metadata, RecordingAuditSink())
    with sessionmaker(engine)() as session:
        session.execute(members.insert().values(id="m-1", email="member@example.com"))
        session.execute(members.insert().values(id="m-2", email="other@example.com"))
        session.commit()
        bundle = exporter.export_subject(session, "m-1")
    assert [(record.source, record.field, record.value) for record in bundle.records] == [
        ("members", "email", "member@example.com")
    ]
    engine.dispose()


def _snapshot(session_factory: sessionmaker[Session]) -> list[tuple[tuple[object, ...], ...]]:
    """Every row of every table, deterministically ordered."""
    with session_factory() as session:
        return [
            tuple(tuple(row) for row in session.execute(statement))
            for table in Base.metadata.sorted_tables
            for statement in (table.select().order_by(*table.primary_key.columns),)
        ]
