"""ErasureExecutor — subject-scoped deletes, anonymizes, and retain counts.

Golden cases run against the shared conftest schema; the composite-key,
surrogate-uniqueness, and no-primary-key cases hand-build focused metadata
plus a matching :class:`SubjectGraph` (the executor is a pure function of
those plus a session, so the shared schema stays untouched).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import NamedTuple

import pytest
from conftest import Base, seed_two_subjects
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table, create_engine
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm import registry as orm_registry_type
from sqlalchemy.pool import StaticPool

from effaced import (
    AnonymizationError,
    ConfigurationError,
    ErasureStep,
    ErasureStrategy,
    JoinHop,
    ManifestError,
    StepExecutor,
    SubjectGraph,
    TableAccessPlan,
    collect_data_map,
    resolve_subject_graph,
)
from effaced.adapters.sqlalchemy import ErasureExecutor


class ExecutorHarness(NamedTuple):
    """The shared schema seeded with two subjects, plus a wired executor."""

    session: Session
    graph: SubjectGraph
    executor: ErasureExecutor


@pytest.fixture()
def harness(metadata: MetaData, orm_registry: orm_registry_type) -> Iterator[ExecutorHarness]:
    """Two-subject data on in-memory SQLite with a default executor."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata.create_all(engine)
    data_map = collect_data_map(metadata)
    graph = resolve_subject_graph(data_map, orm_registry)
    with sessionmaker(engine)() as session:
        seed_two_subjects(session)
        yield ExecutorHarness(session, graph, ErasureExecutor(metadata))
    engine.dispose()


def rows(session: Session, table: Table) -> list[dict[str, object]]:
    return [dict(row) for row in session.execute(sa_select(table)).mappings()]


def delete_step(target: str) -> ErasureStep:
    return ErasureStep(target=target, strategy=ErasureStrategy.DELETE)


def test_multi_hop_delete_scopes_to_the_subject(harness: ExecutorHarness) -> None:
    """order_items reaches the subject via order.user — only A's rows go."""
    affected = harness.executor.execute(
        harness.session, harness.graph, delete_step("order_items"), "1"
    )
    assert affected == 1
    remaining = rows(harness.session, Base.metadata.tables["order_items"])
    assert [row["id"] for row in remaining] == [2]


def test_self_referential_comment_chain_deletes_in_one_step(harness: ExecutorHarness) -> None:
    affected = harness.executor.execute(
        harness.session, harness.graph, delete_step("comments"), "1"
    )
    assert affected == 2
    remaining = rows(harness.session, Base.metadata.tables["comments"])
    assert [row["id"] for row in remaining] == [3]


def test_anonymize_replaces_declared_columns_only(harness: ExecutorHarness) -> None:
    step = ErasureStep(
        target="users", strategy=ErasureStrategy.ANONYMIZE, columns=("email", "name")
    )
    affected = harness.executor.execute(harness.session, harness.graph, step, "1")
    assert affected == 1
    alice, bob = rows(harness.session, Base.metadata.tables["users"])
    assert alice["email"] != "alice@example.com"
    assert alice["name"] != "Alice Doe"
    assert alice["theme"] == "dark"
    assert alice["id"] == 1
    assert bob == {"id": 2, "email": "bob@example.com", "name": "Bob Roe", "theme": "light"}


def test_retain_counts_rows_without_touching_them(harness: ExecutorHarness) -> None:
    step = ErasureStep(
        target="invoices", strategy=ErasureStrategy.RETAIN, columns=("billing_address",)
    )
    affected = harness.executor.execute(harness.session, harness.graph, step, "1")
    assert affected == 1
    invoices = rows(harness.session, Base.metadata.tables["invoices"])
    assert invoices == [
        {"id": 1, "user_id": 1, "billing_address": "1 Alice Street"},
        {"id": 2, "user_id": 2, "billing_address": "2 Bob Street"},
    ]


def test_unknown_subject_matches_nothing(harness: ExecutorHarness) -> None:
    affected = harness.executor.execute(harness.session, harness.graph, delete_step("orders"), "99")
    assert affected == 0
    assert len(rows(harness.session, Base.metadata.tables["orders"])) == 2


def test_unknown_table_raises_manifest_error(harness: ExecutorHarness) -> None:
    with pytest.raises(ManifestError, match="ghosts"):
        harness.executor.execute(harness.session, harness.graph, delete_step("ghosts"), "1")


def test_external_step_is_refused(harness: ExecutorHarness) -> None:
    step = ErasureStep(target="stripe", strategy=ErasureStrategy.DELETE, external=True)
    with pytest.raises(ConfigurationError, match="external"):
        harness.executor.execute(harness.session, harness.graph, step, "1")


def test_executor_satisfies_the_protocol(harness: ExecutorHarness) -> None:
    assert isinstance(harness.executor, StepExecutor)


# --- hand-built focused schemas --------------------------------------------


class HandBuilt(NamedTuple):
    """A focused schema with its engine-backed session and graph."""

    session: Session
    metadata: MetaData
    graph: SubjectGraph


def _hand_built(metadata: MetaData, graph: SubjectGraph) -> Iterator[HandBuilt]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata.create_all(engine)
    with sessionmaker(engine)() as session:
        yield HandBuilt(session, metadata, graph)
    engine.dispose()


@pytest.fixture()
def composite() -> Iterator[HandBuilt]:
    """A child table reaching its subject over a composite foreign key."""
    metadata = MetaData()
    Table(
        "tenants",
        metadata,
        Column("region", String(8), primary_key=True),
        Column("number", Integer, primary_key=True),
    )
    Table(
        "devices",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("tenant_region", String(8)),
        Column("tenant_number", Integer),
    )
    graph = SubjectGraph(
        subject_table="tenants",
        subject_id_column="number",
        accesses=(
            TableAccessPlan(
                table="devices",
                hops=(
                    JoinHop(
                        source_table="devices",
                        source_columns=("tenant_region", "tenant_number"),
                        target_table="tenants",
                        target_columns=("region", "number"),
                    ),
                ),
                fully_pii_owned=True,
            ),
            TableAccessPlan(table="tenants", fully_pii_owned=True),
        ),
    )
    yield from _hand_built(metadata, graph)


def test_composite_foreign_key_scoping(composite: HandBuilt) -> None:
    tenants, devices = composite.metadata.tables["tenants"], composite.metadata.tables["devices"]
    composite.session.execute(
        tenants.insert(),
        [{"region": "eu", "number": 1}, {"region": "us", "number": 2}],
    )
    composite.session.execute(
        devices.insert(),
        [
            {"id": 1, "tenant_region": "eu", "tenant_number": 1},
            {"id": 2, "tenant_region": "us", "tenant_number": 2},
        ],
    )
    executor = ErasureExecutor(composite.metadata)
    affected = executor.execute(composite.session, composite.graph, delete_step("devices"), "1")
    assert affected == 1
    assert [row["id"] for row in rows(composite.session, devices)] == [2]


@pytest.fixture()
def notes() -> Iterator[HandBuilt]:
    """A subject with several anonymizable rows in one child table."""
    metadata = MetaData()
    Table("people", metadata, Column("id", Integer, primary_key=True))
    Table(
        "notes",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("person_id", Integer, ForeignKey("people.id")),
        Column("body", String(64), unique=True),
    )
    graph = SubjectGraph(
        subject_table="people",
        subject_id_column="id",
        accesses=(
            TableAccessPlan(
                table="notes",
                hops=(
                    JoinHop(
                        source_table="notes",
                        source_columns=("person_id",),
                        target_table="people",
                        target_columns=("id",),
                    ),
                ),
            ),
            TableAccessPlan(table="people"),
        ),
    )
    yield from _hand_built(metadata, graph)


def test_anonymize_draws_a_fresh_surrogate_per_row(notes: HandBuilt) -> None:
    """Unique constraints must keep holding — one shared surrogate would not."""
    people, notes_table = notes.metadata.tables["people"], notes.metadata.tables["notes"]
    notes.session.execute(people.insert(), [{"id": 1}])
    notes.session.execute(
        notes_table.insert(),
        [{"id": 1, "person_id": 1, "body": "first"}, {"id": 2, "person_id": 1, "body": "second"}],
    )
    step = ErasureStep(target="notes", strategy=ErasureStrategy.ANONYMIZE, columns=("body",))
    affected = ErasureExecutor(notes.metadata).execute(notes.session, notes.graph, step, "1")
    assert affected == 2
    first, second = (row["body"] for row in rows(notes.session, notes_table))
    assert first != second
    assert {first, second}.isdisjoint({"first", "second"})


@pytest.fixture()
def keyless() -> Iterator[HandBuilt]:
    """A subject-linked table without a primary key."""
    metadata = MetaData()
    Table("people", metadata, Column("id", Integer, primary_key=True))
    Table("scratch", metadata, Column("person_id", Integer), Column("blob", String(64)))
    graph = SubjectGraph(
        subject_table="people",
        subject_id_column="id",
        accesses=(
            TableAccessPlan(
                table="scratch",
                hops=(
                    JoinHop(
                        source_table="scratch",
                        source_columns=("person_id",),
                        target_table="people",
                        target_columns=("id",),
                    ),
                ),
            ),
            TableAccessPlan(table="people"),
        ),
    )
    yield from _hand_built(metadata, graph)


def test_anonymize_without_a_primary_key_fails_loudly(keyless: HandBuilt) -> None:
    step = ErasureStep(target="scratch", strategy=ErasureStrategy.ANONYMIZE, columns=("blob",))
    with pytest.raises(AnonymizationError, match="primary key"):
        ErasureExecutor(keyless.metadata).execute(keyless.session, keyless.graph, step, "1")
