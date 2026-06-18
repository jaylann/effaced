"""Manifest-driven execution is byte-identical to the annotation path.

Issue #71. :meth:`effaced.EffacedStack.from_manifest` runs every engine from a
serialized manifest paired with a reflected live database, where
:meth:`~effaced.EffacedStack.from_base` runs them from an annotated declarative
base. This proves the two entry points produce *identical* erasure plans and
export bundles for the same schema — so a caller whose models live in another
service (or another language) gets exactly the deletions and exports the
in-process annotation path would.

For each generated schema the annotation path resolves the subject graph from
ORM mappers (:func:`effaced.resolve_subject_graph`) and the manifest path from
reflected foreign keys (:func:`effaced.resolve_subject_graph_from_fk`), after a
real ``DataMap.to_payload()`` -> ``DataMap.from_payload(...)`` round-trip and a
``reflect_metadata`` read-back off a SQLite engine the schema's tables were
created in. We assert byte-identical :class:`effaced.ErasurePlan` (so FK-safe
deletion order and every RETAIN/ANONYMIZE/DELETE step match) and byte-identical
:class:`effaced.ExportBundle` (so the exported surface and its Art. 15 metadata
match), for the same subject.

The annotation graph is resolved through a registry whose ``.metadata`` *is*
the schema's metadata — mirroring a declarative ``Base`` (where
``metadata is registry.metadata``), which is the invariant
:meth:`~effaced.EffacedStack.from_base` relies on. The shared
``annotated_schemas()`` strategy maps imperatively onto a detached
``registry().metadata``, so its ``GeneratedSchema.graph`` would resolve foreign
keys against an empty metadata; re-mapping here keeps the comparison faithful
to what production wires.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from conftest import RecordingAuditSink
from hypothesis import given, settings
from schema_strategies import SUBJECT_TABLE, GeneratedSchema, annotated_schemas, scaled_examples
from sqlalchemy import create_engine
from sqlalchemy.orm import registry, relationship, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    DataMap,
    ErasurePlanner,
    Exporter,
    SubjectGraph,
    reflect_metadata,
    resolve_subject_graph,
    resolve_subject_graph_from_fk,
)

if TYPE_CHECKING:
    from sqlalchemy import Engine

pytestmark = pytest.mark.property

_SUBJECT_SEED = 1
"""The integer subject seeded and erased/exported in every parity example.

``schema.subject_identity`` maps it to the identifier the drawn schema needs —
a bare ``str`` for a single-column subject, a
:class:`~effaced.CompositeSubjectId` for a composite-subject draw (ADR 0025) —
so the parity comparison holds for both shapes."""


def _table_name_path(table: str, parents: dict[str, str]) -> str:
    """Rewrite a relationship-name subject path to the FK resolver's table path.

    ``annotated_schemas()`` authors ``subject_link`` paths as relationship
    attribute names (``"parent.parent"``); the FK resolver names target tables
    instead (``"t1.t0"``). Walk the parent chain to the subject table.
    """
    segments: list[str] = []
    name = table
    while name != SUBJECT_TABLE:
        parent = parents[name]
        segments.append(parent)
        name = parent
    return ".".join(segments)


def _annotation_graph(schema: GeneratedSchema) -> SubjectGraph:
    """Resolve the ORM subject graph the way ``from_base`` would in production.

    Re-maps the generated tables onto a registry whose ``.metadata`` holds them
    (a declarative ``Base`` invariant), so ``resolve_subject_graph`` reads the
    real foreign-key constraints for FK-safe ordering.
    """
    mappers = registry(metadata=schema.metadata)
    names = [entry.name for entry in schema.data_map.tables]
    classes = {name: type(f"Parity_{name}", (), {}) for name in names}
    for name in names:
        table = schema.metadata.tables[name]
        properties: dict[str, object] = {}
        if name in schema.parents:
            parent = schema.parents[name]
            foreign_keys = (
                [table.c.pid, table.c.pid2] if parent in schema.composite_tables else [table.c.pid]
            )
            properties["parent"] = relationship(
                classes[parent], foreign_keys=foreign_keys, overlaps="parent"
            )
        mappers.map_imperatively(classes[name], table, properties=properties)
    mappers.configure()
    return resolve_subject_graph(schema.data_map, mappers)


def _manifest_path(schema: GeneratedSchema, engine: Engine) -> tuple[DataMap, SubjectGraph]:
    """Round-trip the manifest and resolve the graph from reflected foreign keys.

    Serializes the data map, rewrites each subject path to the FK resolver's
    table-name form, loads it back through ``DataMap.from_payload`` (the real
    migrate + validate), reflects exactly the manifest's tables off ``engine``,
    and resolves the graph from the reflected constraints — the
    ``from_manifest`` pipeline, without the engine wiring.
    """
    payload: dict[str, Any] = schema.data_map.to_payload()
    for entry in payload["tables"]:
        link = entry["subject_link"]
        if link is not None and link["path"]:
            link["path"] = _table_name_path(entry["name"], schema.parents)
    data_map = DataMap.from_payload(payload)
    metadata = reflect_metadata(engine, only=[table.name for table in data_map.tables])
    return data_map, resolve_subject_graph_from_fk(data_map, metadata)


@given(schema=annotated_schemas())
@settings(max_examples=scaled_examples(4), deadline=None)
def test_manifest_plan_is_byte_identical_to_annotation_plan(schema: GeneratedSchema) -> None:
    """The erasure plan is identical whether built from annotations or a manifest.

    Byte-identical plans mean the same FK-safe deletion order and the same
    per-column RETAIN/ANONYMIZE/DELETE steps — so RETAIN preservation and
    delete ordering hold identically across both entry points.
    """
    annotation_graph = _annotation_graph(schema)
    engine = create_engine("sqlite://", poolclass=StaticPool)
    schema.metadata.create_all(engine)
    manifest_map, manifest_graph = _manifest_path(schema, engine)

    subject_id = schema.subject_identity(_SUBJECT_SEED)
    annotation_plan = ErasurePlanner(schema.data_map, annotation_graph).plan(subject_id)
    manifest_plan = ErasurePlanner(manifest_map, manifest_graph).plan(subject_id)

    assert manifest_plan.model_dump() == annotation_plan.model_dump()
    # The deletion order itself matches, not merely the step set.
    assert manifest_graph.deletion_order == annotation_graph.deletion_order
    engine.dispose()


@given(schema=annotated_schemas())
@settings(max_examples=scaled_examples(4), deadline=None)
def test_manifest_export_is_byte_identical_to_annotation_export(schema: GeneratedSchema) -> None:
    """The export bundle is identical whether built from annotations or a manifest.

    Every exported value and its Art. 15 metadata match; only ``generated_at``
    (the assembly timestamp, not subject data) is excluded from the comparison.
    """
    annotation_graph = _annotation_graph(schema)
    engine = create_engine("sqlite://", poolclass=StaticPool)
    schema.metadata.create_all(engine)
    manifest_map, manifest_graph = _manifest_path(schema, engine)
    session_factory = sessionmaker(engine)
    with session_factory() as session:
        schema.seed(session, _SUBJECT_SEED)
        session.commit()

    subject_id = schema.subject_identity(_SUBJECT_SEED)
    with session_factory() as session:
        annotation_bundle = Exporter(
            schema.data_map, annotation_graph, schema.metadata, RecordingAuditSink()
        ).export_subject(session, subject_id)
        manifest_metadata = reflect_metadata(
            engine, only=[entry.name for entry in manifest_map.tables]
        )
        manifest_bundle = Exporter(
            manifest_map, manifest_graph, manifest_metadata, RecordingAuditSink()
        ).export_subject(session, subject_id)

    assert manifest_bundle.model_dump(exclude={"generated_at"}) == annotation_bundle.model_dump(
        exclude={"generated_at"}
    )
    engine.dispose()
