"""Composite subject identity keys — conformance proofs (ADR 0025, issue #129).

A subject whose identity spans several columns is matched on the *whole*
ordered key. These tests are the conformance evidence that matching the full
key isolates subjects that share one key element, that single-column schemas
stay byte-identical, that the canonical serialization is collision-free, and
that erase/export/verify all agree on a composite subject. They build focused
metadata and resolve the graph through the real
:func:`effaced.resolve_subject_graph_from_fk` path — the executor, exporter,
and verifier are pure functions of that graph plus a session, so the shared
conftest schema stays untouched.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import NamedTuple

import pytest
from sqlalchemy import (
    Column,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
)
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    AuditEvent,
    CompositeSubjectId,
    ErasureStep,
    ErasureStrategy,
    PiiCategory,
    RetentionPolicy,
    SubjectGraph,
    canonical_subject_id,
    collect_data_map,
    parse_canonical,
    pii,
    resolve_subject_graph_from_fk,
    subject_link,
)
from effaced.adapters.sqlalchemy import ErasureExecutor, ErasureVerifier
from effaced.adapters.sqlalchemy.scoping import subject_scope, subject_values
from effaced.exceptions import SubjectResolutionError
from effaced.export.exporter import Exporter


class CompositeHarness(NamedTuple):
    """A composite-subject schema on in-memory SQLite, resolved for real."""

    session: Session
    metadata: MetaData
    graph: SubjectGraph


class RecordingSink:
    """A minimal append-only audit sink for the export/verify proof."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self.events.append(event)


def _delete(target: str) -> ErasureStep:
    return ErasureStep(target=target, strategy=ErasureStrategy.DELETE)


def _composite_metadata(*, retain_child: bool = False) -> MetaData:
    """A ``(tenant, user_id)`` subject with a child reaching it over both keys.

    ``members`` is the subject table with composite primary key
    ``(tenant, user_id)``; ``notes`` belongs to a member through a composite
    foreign key. The child's annotated column is ``RETAIN`` when
    ``retain_child`` so retention preservation can be exercised too.
    """
    metadata = MetaData()
    Table(
        "members",
        metadata,
        Column("tenant", Integer, primary_key=True, autoincrement=False),
        Column("user_id", Integer, primary_key=True, autoincrement=False),
        Column("email", String(64), nullable=False, info=pii(PiiCategory.CONTACT)),
        info=subject_link("", subject_id_columns=("tenant", "user_id")),
    )
    note_spec = (
        pii(
            PiiCategory.BEHAVIORAL,
            erasure=ErasureStrategy.RETAIN,
            retention=RetentionPolicy(reason="statutory record"),
        )
        if retain_child
        else pii(PiiCategory.BEHAVIORAL)
    )
    Table(
        "notes",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=False),
        Column("m_tenant", Integer, nullable=False),
        Column("m_user", Integer, nullable=False),
        Column("body", String(64), nullable=False, info=note_spec),
        ForeignKeyConstraint(["m_tenant", "m_user"], ["members.tenant", "members.user_id"]),
        info=subject_link("members"),
    )
    return metadata


def _resolved(metadata: MetaData) -> SubjectGraph:
    """Resolve the composite-subject graph through the real FK resolver."""
    return resolve_subject_graph_from_fk(collect_data_map(metadata), metadata)


@pytest.fixture()
def harness() -> Iterator[CompositeHarness]:
    """A composite-subject schema seeded by the individual tests."""
    metadata = _composite_metadata()
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata.create_all(engine)
    graph = _resolved(metadata)
    with sessionmaker(engine)() as session:
        yield CompositeHarness(session, metadata, graph)
    engine.dispose()


def _rows(session: Session, table: Table) -> list[dict[str, object]]:
    return [dict(row) for row in session.execute(sa_select(table)).mappings()]


def _seed_shared_element(session: Session, metadata: MetaData) -> None:
    """Two subjects that differ in only ONE key element, plus their notes.

    Both members share ``tenant=1``; they differ only in ``user_id``. A
    note belongs to each. If matching scoped on ``tenant`` alone (a partial
    key), erasing one member would delete the other's note — the bleed this
    proves cannot happen.
    """
    members, notes = metadata.tables["members"], metadata.tables["notes"]
    session.execute(
        members.insert(),
        [
            {"tenant": 1, "user_id": 10, "email": "a@x"},
            {"tenant": 1, "user_id": 20, "email": "b@x"},
        ],
    )
    session.execute(
        notes.insert(),
        [
            {"id": 1, "m_tenant": 1, "m_user": 10, "body": "note-A"},
            {"id": 2, "m_tenant": 1, "m_user": 20, "body": "note-B"},
        ],
    )


def _subject(tenant: int, user_id: int) -> CompositeSubjectId:
    return CompositeSubjectId(values=(str(tenant), str(user_id)))


def test_no_cross_subject_bleed_when_one_key_element_is_shared(harness: CompositeHarness) -> None:
    """Erasing one composite subject never touches another sharing one element."""
    _seed_shared_element(harness.session, harness.metadata)
    executor = ErasureExecutor(harness.metadata)
    deleted = executor.execute(harness.session, harness.graph, _delete("notes"), _subject(1, 10))
    members_deleted = executor.execute(
        harness.session, harness.graph, _delete("members"), _subject(1, 10)
    )
    assert (deleted, members_deleted) == (1, 1)
    # The sibling sharing tenant=1 but a different user_id is untouched.
    assert [row["id"] for row in _rows(harness.session, harness.metadata.tables["notes"])] == [2]
    surviving = _rows(harness.session, harness.metadata.tables["members"])
    assert [(row["tenant"], row["user_id"]) for row in surviving] == [(1, 20)]


def test_idempotent_re_erase_matches_a_single_erase(harness: CompositeHarness) -> None:
    """Re-erasing an already-erased composite subject is a no-op (zero rows)."""
    _seed_shared_element(harness.session, harness.metadata)
    executor = ErasureExecutor(harness.metadata)
    first = executor.execute(harness.session, harness.graph, _delete("notes"), _subject(1, 10))
    second = executor.execute(harness.session, harness.graph, _delete("notes"), _subject(1, 10))
    assert (first, second) == (1, 0)


def test_retain_child_survives_composite_erase() -> None:
    """A RETAIN column on a composite subject's child is never deleted."""
    metadata = _composite_metadata(retain_child=True)
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata.create_all(engine)
    graph = _resolved(metadata)
    with sessionmaker(engine)() as session:
        _seed_shared_element(session, metadata)
        retained = ErasureExecutor(metadata).execute(
            session,
            graph,
            ErasureStep(target="notes", strategy=ErasureStrategy.RETAIN, columns=("body",)),
            _subject(1, 10),
        )
        assert retained == 1  # counted, never deleted
        assert {row["id"] for row in _rows(session, metadata.tables["notes"])} == {1, 2}
    engine.dispose()


def test_exporter_and_verifier_agree_on_a_composite_subject(harness: CompositeHarness) -> None:
    """Export sees exactly the composite subject's rows; verify confirms the erase."""
    _seed_shared_element(harness.session, harness.metadata)
    audit = RecordingSink()
    data_map = collect_data_map(harness.metadata)
    exporter = Exporter(data_map, harness.graph, harness.metadata, audit)
    bundle = exporter.export_subject(harness.session, _subject(1, 10))
    bodies = {record.value for record in bundle.records if record.field == "body"}
    assert bodies == {"note-A"}  # never note-B
    assert bundle.subject_id == _subject(1, 10)  # echoed back unchanged

    executor = ErasureExecutor(harness.metadata)
    executor.execute(harness.session, harness.graph, _delete("notes"), _subject(1, 10))
    executor.execute(harness.session, harness.graph, _delete("members"), _subject(1, 10))
    verifier = ErasureVerifier(data_map, harness.graph, harness.metadata, audit_sink=audit)
    verification = verifier.verify_subject_erased(harness.session, _subject(1, 10))
    assert verification.verified
    assert verification.subject_id == _subject(1, 10)
    # The sibling's row is still present and untouched by the verification.
    assert {row["id"] for row in _rows(harness.session, harness.metadata.tables["notes"])} == {2}


def test_canonical_serialization_is_collision_free() -> None:
    """Distinct composite keys never serialize to the same canonical string."""
    a = CompositeSubjectId(values=("a", "b:c"))
    b = CompositeSubjectId(values=("a:b", "c"))
    assert canonical_subject_id(a) != canonical_subject_id(b)
    # Separator and escape characters inside a value round-trip intact.
    weird = CompositeSubjectId(values=("x\x1fy", "z\x1bw"))
    assert parse_canonical(canonical_subject_id(weird)) == weird
    # A bare str is its own canonical form — single-column byte-compat.
    assert canonical_subject_id("42") == "42"


def test_single_column_predicate_is_byte_identical() -> None:
    """A one-column subject yields the exact SQL a plain string always did."""
    metadata = MetaData()
    Table(
        "users",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=False),
        Column("email", String(64), nullable=False, info=pii(PiiCategory.CONTACT)),
        info=subject_link(""),
    )
    graph = _resolved(metadata)
    predicate = subject_scope(metadata, graph, "users", "42")
    compiled = str(predicate.compile(compile_kwargs={"literal_binds": True}))
    assert compiled == "users.id = 42"


def test_arity_mismatch_fails_loudly() -> None:
    """A composite identifier whose arity disagrees with the columns raises."""
    with pytest.raises(SubjectResolutionError, match="matches the whole ordered key"):
        subject_values(("tenant", "user_id"), "just-one")
    with pytest.raises(SubjectResolutionError, match="matches the whole ordered key"):
        subject_values(("tenant", "user_id"), CompositeSubjectId(values=("only-one",)))
