"""Shared fixtures: a small annotated schema covering every declaration kind."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any, ClassVar

import pytest
from sqlalchemy import Engine, ForeignKey, MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from effaced import (
    ErasureStrategy,
    LegalBasis,
    PiiCategory,
    RetentionPolicy,
    pii,
    subject_link,
)


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
def pg_engine() -> Iterator[Engine]:
    """An engine on the integration-test Postgres; skips when no URL is set."""
    url = os.environ.get("EFFACED_TEST_DATABASE_URL")
    if not url:
        pytest.skip("EFFACED_TEST_DATABASE_URL not set")
    engine = create_engine(url)
    yield engine
    engine.dispose()
