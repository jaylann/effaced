"""RectificationExecutor — subject-scoped UPDATE statements sharing the erasure scoping."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

import pytest
from conftest import Base, seed_two_subjects
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from effaced import (
    ManifestError,
    PiiCategory,
    collect_data_map,
    resolve_subject_graph,
)
from effaced.adapters.sqlalchemy import RectificationExecutor
from effaced.rectification import RectificationStep, RectificationStepExecutor

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import MetaData, Table
    from sqlalchemy.orm import Session
    from sqlalchemy.orm import registry as orm_registry_type

    from effaced import SubjectGraph


class ExecutorHarness(NamedTuple):
    """The shared schema seeded with two subjects, plus a wired executor."""

    session: Session
    graph: SubjectGraph
    executor: RectificationExecutor


@pytest.fixture()
def harness(metadata: MetaData, orm_registry: orm_registry_type) -> Iterator[ExecutorHarness]:
    """Two-subject data on in-memory SQLite with a rectification executor."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    metadata.create_all(engine)
    data_map = collect_data_map(metadata)
    graph = resolve_subject_graph(data_map, orm_registry)
    with sessionmaker(engine)() as session:
        seed_two_subjects(session)
        yield ExecutorHarness(session, graph, RectificationExecutor(metadata))
    engine.dispose()


def rows(session: Session, table: Table) -> list[dict[str, object]]:
    return [dict(row) for row in session.execute(select(table)).mappings()]


def step(target: str, columns: tuple[str, ...]) -> RectificationStep:
    return RectificationStep(target=target, category=PiiCategory.CONTACT, columns=columns)


def test_scoped_update_touches_only_the_subject_with_int_pk_coercion(
    harness: ExecutorHarness,
) -> None:
    """The published str subject id is coerced onto the integer subject column."""
    affected = harness.executor.execute(
        harness.session, harness.graph, step("users", ("email",)), "1", "new@example.com"
    )
    assert affected == 1
    alice, bob = rows(harness.session, Base.metadata.tables["users"])
    assert alice["email"] == "new@example.com"
    assert alice["name"] == "Alice Doe"
    assert bob["email"] == "bob@example.com"


def test_multi_hop_update_scopes_to_the_subject(harness: ExecutorHarness) -> None:
    affected = harness.executor.execute(
        harness.session,
        harness.graph,
        step("order_items", ("gift_message",)),
        "1",
        "corrected message",
    )
    assert affected == 1
    first, second = rows(harness.session, Base.metadata.tables["order_items"])
    assert first["gift_message"] == "corrected message"
    assert second["gift_message"] == "a gift for bob"


def test_self_referential_table_updates_the_whole_chain_and_reports_rowcount(
    harness: ExecutorHarness,
) -> None:
    """Both of subject 1's comments (parent and reply) match; subject 2's does not."""
    affected = harness.executor.execute(
        harness.session, harness.graph, step("comments", ("parent_id",)), "1", 1
    )
    assert affected == 2
    by_id = {row["id"]: row for row in rows(harness.session, Base.metadata.tables["comments"])}
    assert by_id[1]["parent_id"] == 1
    assert by_id[2]["parent_id"] == 1
    assert by_id[3]["parent_id"] is None


def test_multiple_columns_share_the_single_corrected_value(harness: ExecutorHarness) -> None:
    """One correction is one value — every named column gets it (ADR 0013 bluntness)."""
    affected = harness.executor.execute(
        harness.session, harness.graph, step("users", ("email", "name")), "1", "shared"
    )
    assert affected == 1
    alice = rows(harness.session, Base.metadata.tables["users"])[0]
    assert alice["email"] == "shared"
    assert alice["name"] == "shared"


def test_unknown_subject_matches_nothing(harness: ExecutorHarness) -> None:
    affected = harness.executor.execute(
        harness.session, harness.graph, step("users", ("email",)), "99", "x"
    )
    assert affected == 0


def test_unknown_table_raises_manifest_error(harness: ExecutorHarness) -> None:
    with pytest.raises(ManifestError, match="ghosts"):
        harness.executor.execute(
            harness.session, harness.graph, step("ghosts", ("email",)), "1", "x"
        )


def test_unknown_column_raises_manifest_error(harness: ExecutorHarness) -> None:
    with pytest.raises(ManifestError, match="ghost_column"):
        harness.executor.execute(
            harness.session, harness.graph, step("users", ("ghost_column",)), "1", "x"
        )


def test_executor_satisfies_the_protocol(harness: ExecutorHarness) -> None:
    assert isinstance(harness.executor, RectificationStepExecutor)
