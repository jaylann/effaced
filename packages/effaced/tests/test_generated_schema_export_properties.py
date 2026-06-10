"""Export proofs on arbitrary schemas: no bleed, Art. 15 metadata round-trips."""

from __future__ import annotations

import pytest
from conftest import RecordingAuditSink
from hypothesis import given, settings
from hypothesis import strategies as st
from schema_strategies import GeneratedSchema, annotated_schemas, scaled_examples
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import ExportBundle, Exporter

SUBJECT_COUNT = 3


def export_bundle(schema: GeneratedSchema, subject_id: int) -> ExportBundle:
    """Seed every subject, then export one of them."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    schema.metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    with session_factory() as session:
        for seeded in range(1, SUBJECT_COUNT + 1):
            schema.seed(session, seeded)
        session.commit()
    exporter = Exporter(schema.data_map, schema.graph, schema.metadata, RecordingAuditSink())
    with session_factory() as session:
        bundle = exporter.export_subject(session, str(subject_id))
    engine.dispose()
    return bundle


pytestmark = pytest.mark.property


@given(
    schema=annotated_schemas(),
    subject_id=st.integers(min_value=1, max_value=SUBJECT_COUNT),
)
@settings(max_examples=scaled_examples(4), deadline=None)
def test_export_on_any_schema_never_bleeds(schema: GeneratedSchema, subject_id: int) -> None:
    """The bundle holds every annotated cell of one subject and nobody else's."""
    bundle = export_bundle(schema, subject_id)
    expected_records = sum(
        schema.rows[name] * len(specs) for name, specs in schema.pii_columns.items()
    )
    assert len(bundle.records) == expected_records
    assert bundle.incomplete_sources == ()
    own = f"<s{subject_id}>"
    others = {f"<s{seeded}>" for seeded in range(1, SUBJECT_COUNT + 1)} - {own}
    for record in bundle.records:
        value = str(record.value)
        assert own in value
        assert not any(other in value for other in others)


@given(schema=annotated_schemas())
@settings(max_examples=scaled_examples(4), deadline=None)
def test_export_surfaces_retention_reasons_and_art15_metadata(
    schema: GeneratedSchema,
) -> None:
    """Each record carries its column's declared category, basis, purpose, reason."""
    bundle = export_bundle(schema, 1)
    pii_columns = schema.pii_columns
    for record in bundle.records:
        spec = pii_columns[record.source][record.field]
        assert record.category is spec.category
        assert record.legal_basis is spec.legal_basis
        assert record.purpose == spec.purpose
        expected_reason = spec.retention.reason if spec.retention else None
        assert record.retention_reason == expected_reason
