"""Shared fixtures: a small annotated schema covering every declaration kind."""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from typing import Any, ClassVar

import pytest
from sqlalchemy import Column, Engine, ForeignKey, Integer, MetaData, Table, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, registry, relationship

from effaced import (
    AuditEvent,
    ErasureStrategy,
    LegalBasis,
    PiiCategory,
    RetentionPolicy,
    pii,
    subject_link,
)


class RecordingAuditSink:
    """In-memory ``AuditSink`` fake that records every appended event.

    Lets unit tests assert on the audit mirror without a second database
    connection (which SQLite's single write lock would block on).
    """

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        """Record one event in arrival order."""
        self.events.append(event)

    def read(self, subject_ref: str) -> Sequence[AuditEvent]:
        """Return the subject's events, oldest first."""
        matching = (event for event in self.events if event.subject_ref == subject_ref)
        return tuple(sorted(matching, key=lambda event: event.occurred_at))


class Base(DeclarativeBase):
    metadata = MetaData()


class User(Base):
    __tablename__ = "users"
    __table_args__: ClassVar[dict[str, Any]] = {"info": subject_link("")}

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(
        info=pii(PiiCategory.CONTACT, legal_basis=LegalBasis.CONTRACT, purpose="account login")
    )
    name: Mapped[str] = mapped_column(info=pii(PiiCategory.IDENTITY))
    theme: Mapped[str]  # not PII — must never appear in the manifest


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__: ClassVar[dict[str, Any]] = {"info": subject_link("user")}

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    billing_address: Mapped[str] = mapped_column(
        info=pii(
            PiiCategory.FINANCIAL,
            erasure=ErasureStrategy.RETAIN,
            retention=RetentionPolicy(reason="§147 AO invoice retention"),
        )
    )

    user: Mapped[User] = relationship()


class Order(Base):
    """A single-hop table — reaches the subject through one relationship."""

    __tablename__ = "orders"
    __table_args__: ClassVar[dict[str, Any]] = {"info": subject_link("user")}

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    user: Mapped[User] = relationship()


class OrderItem(Base):
    """A multi-hop table — reaches the subject via order.user."""

    __tablename__ = "order_items"
    __table_args__: ClassVar[dict[str, Any]] = {"info": subject_link("order.user")}

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))

    order: Mapped[Order] = relationship()


class Comment(Base):
    """A subject-linked table with a self-referential foreign key."""

    __tablename__ = "comments"
    __table_args__: ClassVar[dict[str, Any]] = {"info": subject_link("user")}

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("comments.id"))

    user: Mapped[User] = relationship()


user_tags = Table(
    "user_tags",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id"), primary_key=True),
)
"""Unannotated association table — exists only for many-to-many error tests."""


class Tag(Base):
    """Unannotated table whose only path to users is a many-to-many."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)

    users: Mapped[list[User]] = relationship(secondary=user_tags)


class AppSetting(Base):
    """A table with no PII at all — must not appear in the manifest."""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str]
    value: Mapped[str]


@pytest.fixture()
def metadata() -> MetaData:
    """The annotated test schema's metadata."""
    return Base.metadata


@pytest.fixture()
def orm_registry() -> registry:
    """The annotated test schema's ORM registry (mappers + metadata)."""
    return Base.registry


@pytest.fixture()
def pg_engine() -> Iterator[Engine]:
    """An engine on the integration-test Postgres; skips when no URL is set."""
    url = os.environ.get("EFFACED_TEST_DATABASE_URL")
    if not url:
        pytest.skip("EFFACED_TEST_DATABASE_URL not set")
    engine = create_engine(url)
    yield engine
    engine.dispose()
