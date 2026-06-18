"""reflect_metadata lifts a live database's schema into MetaData, scoped by ``only``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table, create_engine
from sqlalchemy.pool import StaticPool

from effaced import reflect_metadata

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Engine


@pytest.fixture()
def engine() -> Iterator[Engine]:
    """An in-memory database with three related tables and one unrelated one."""
    source = MetaData()
    Table(
        "users",
        source,
        Column("id", Integer, primary_key=True),
        Column("email", String),
    )
    Table(
        "posts",
        source,
        Column("id", Integer, primary_key=True),
        Column("user_id", Integer, ForeignKey("users.id")),
    )
    Table(
        "audit_log",
        source,
        Column("id", Integer, primary_key=True),
        Column("note", String),
    )
    engine = create_engine("sqlite://", poolclass=StaticPool)
    source.create_all(engine)
    yield engine
    engine.dispose()


def test_reflects_all_tables_by_default(engine: Engine) -> None:
    metadata = reflect_metadata(engine)
    assert set(metadata.tables) == {"users", "posts", "audit_log"}


def test_only_scopes_reflection_to_named_tables(engine: Engine) -> None:
    metadata = reflect_metadata(engine, only=["users", "posts"])
    assert set(metadata.tables) == {"users", "posts"}
    assert "audit_log" not in metadata.tables


def test_reflected_foreign_keys_are_carried(engine: Engine) -> None:
    metadata = reflect_metadata(engine, only=["users", "posts"])
    posts = metadata.tables["posts"]
    referred = {fk.column.table.name for fk in posts.foreign_keys}
    assert referred == {"users"}


def test_returns_a_fresh_metadata_each_call(engine: Engine) -> None:
    first = reflect_metadata(engine, only=["users"])
    second = reflect_metadata(engine, only=["users"])
    assert first is not second
