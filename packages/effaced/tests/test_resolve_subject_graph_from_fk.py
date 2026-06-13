"""Tests for the mapper-free FK-based subject-graph resolver.

:func:`resolve_subject_graph_from_fk` is the sibling of
:func:`resolve_subject_graph` for adapters that have table metadata and
foreign-key constraints but no ORM registry (reflected schemas, hand-built
metadata, the Django adapter). These tests pin its hop resolution, FK-safe
ordering, ``fully_pii_owned`` classification, and loud failures, and prove
parity with the ORM resolver on a shared schema.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from effaced import (
    PiiCategory,
    collect_data_map,
    pii,
    resolve_subject_graph,
    resolve_subject_graph_from_fk,
    subject_link,
)
from effaced.exceptions import SubjectResolutionError


def _three_level_metadata() -> MetaData:
    """A users <- posts <- comments chain authored on a bare ``MetaData``."""
    metadata = MetaData()
    Table(
        "users",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("email", String, info=pii(PiiCategory.CONTACT)),
        info=subject_link(""),
    )
    Table(
        "posts",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("user_id", Integer, ForeignKey("users.id")),
        Column("body", String, info=pii(PiiCategory.BEHAVIORAL)),
        info=subject_link("users"),
    )
    Table(
        "comments",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("post_id", Integer, ForeignKey("posts.id")),
        Column("text", String, info=pii(PiiCategory.BEHAVIORAL)),
        info=subject_link("posts.users"),
    )
    return metadata


def test_resolves_chain_with_fk_safe_order() -> None:
    metadata = _three_level_metadata()
    graph = resolve_subject_graph_from_fk(collect_data_map(metadata), metadata)
    assert graph.subject_table == "users"
    assert graph.subject_id_column == "id"
    # children before parents, subject last
    assert graph.deletion_order == ("comments", "posts", "users")


def test_multi_hop_path_flattens_to_fk_column_pairs() -> None:
    metadata = _three_level_metadata()
    graph = resolve_subject_graph_from_fk(collect_data_map(metadata), metadata)
    comments = graph.access("comments")
    assert [
        (h.source_table, h.source_columns, h.target_table, h.target_columns) for h in comments.hops
    ] == [
        ("comments", ("post_id",), "posts", ("id",)),
        ("posts", ("user_id",), "users", ("id",)),
    ]
    assert graph.access("users").is_subject_table


def test_fully_pii_owned_reflects_unannotated_payload() -> None:
    metadata = MetaData()
    Table(
        "users",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("email", String, info=pii(PiiCategory.CONTACT)),
        Column("nickname", String),  # unannotated payload -> not fully owned
        info=subject_link(""),
    )
    graph = resolve_subject_graph_from_fk(collect_data_map(metadata), metadata)
    assert graph.access("users").fully_pii_owned is False


def test_missing_foreign_key_fails_loudly() -> None:
    metadata = MetaData()
    Table(
        "users",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("email", String, info=pii(PiiCategory.CONTACT)),
        info=subject_link(""),
    )
    Table(
        "events",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("note", String, info=pii(PiiCategory.BEHAVIORAL)),
        info=subject_link("users"),  # no FK column to users
    )
    with pytest.raises(SubjectResolutionError, match="no foreign key to 'users'"):
        resolve_subject_graph_from_fk(collect_data_map(metadata), metadata)


def test_path_naming_unknown_table_fails_loudly() -> None:
    metadata = MetaData()
    Table(
        "users",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("email", String, info=pii(PiiCategory.CONTACT)),
        info=subject_link(""),
    )
    Table(
        "posts",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("user_id", Integer, ForeignKey("users.id")),
        Column("body", String, info=pii(PiiCategory.BEHAVIORAL)),
        info=subject_link("ghosts"),  # not a table
    )
    with pytest.raises(SubjectResolutionError, match="not a table in the metadata"):
        resolve_subject_graph_from_fk(collect_data_map(metadata), metadata)


def test_parity_with_orm_resolver() -> None:
    """The FK resolver and the ORM resolver agree on the same schema."""

    class Base(DeclarativeBase):
        pass

    class User(Base):
        __tablename__ = "users"
        __table_args__: ClassVar[dict[str, Any]] = {"info": subject_link("")}
        id: Mapped[int] = mapped_column(primary_key=True)
        email: Mapped[str] = mapped_column(info=pii(PiiCategory.CONTACT))

    class Post(Base):
        __tablename__ = "posts"
        __table_args__: ClassVar[dict[str, Any]] = {"info": subject_link("user")}
        id: Mapped[int] = mapped_column(primary_key=True)
        user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
        body: Mapped[str] = mapped_column(info=pii(PiiCategory.BEHAVIORAL))
        user: Mapped[User] = relationship()

    data_map = collect_data_map(Base.metadata)
    orm_graph = resolve_subject_graph(data_map, Base.registry)

    # The FK resolver authors the path by target-table name instead.
    fk_metadata = MetaData()
    Table(
        "users",
        fk_metadata,
        Column("id", Integer, primary_key=True),
        Column("email", String, info=pii(PiiCategory.CONTACT)),
        info=subject_link(""),
    )
    Table(
        "posts",
        fk_metadata,
        Column("id", Integer, primary_key=True),
        Column("user_id", Integer, ForeignKey("users.id")),
        Column("body", String, info=pii(PiiCategory.BEHAVIORAL)),
        info=subject_link("users"),
    )
    fk_graph = resolve_subject_graph_from_fk(collect_data_map(fk_metadata), fk_metadata)

    assert fk_graph.subject_table == orm_graph.subject_table
    assert fk_graph.subject_id_column == orm_graph.subject_id_column
    assert fk_graph.deletion_order == orm_graph.deletion_order
    assert fk_graph.access("posts").hops == orm_graph.access("posts").hops
    assert fk_graph.access("posts").fully_pii_owned == orm_graph.access("posts").fully_pii_owned
