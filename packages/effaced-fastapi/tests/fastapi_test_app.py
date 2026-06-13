"""Shared schema and wiring helpers for the effaced-fastapi tests.

Not a conftest: helpers are imported explicitly (unique basename, shared
pytest namespace). The schema mirrors the core suite's shape — an
anonymized subject table plus a retained linked table — small enough to
assert exact erasure outcomes.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Any, ClassVar

from fastapi import FastAPI
from sqlalchemy import Engine, ForeignKey, MetaData, StaticPool, create_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)

from effaced import (
    AuditEvent,
    EffacedStack,
    ErasureStrategy,
    PiiCategory,
    Resolver,
    RetentionPolicy,
    pii,
    subject_link,
)
from effaced_fastapi import EffacedFastAPI, Subject, SubjectProvider


class RecordingSink:
    """In-memory ``AuditSink`` fake — SQLite's single writer blocks a second connection."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self.events.append(event)


class Base(DeclarativeBase):
    metadata = MetaData()


class User(Base):
    __tablename__ = "users"
    __table_args__: ClassVar[dict[str, Any]] = {"info": subject_link("")}

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(info=pii(PiiCategory.CONTACT))
    name: Mapped[str] = mapped_column(info=pii(PiiCategory.IDENTITY))
    theme: Mapped[str]


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__: ClassVar[dict[str, Any]] = {"info": subject_link("user")}

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    billing_address: Mapped[str] = mapped_column(
        info=pii(
            PiiCategory.FINANCIAL,
            erasure=ErasureStrategy.RETAIN,
            retention=RetentionPolicy(reason="invoice retention", duration=timedelta(days=3650)),
        )
    )

    user: Mapped[User] = relationship()


def make_engine() -> Engine:
    """An in-memory SQLite engine safe across threadpool threads."""
    return create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )


def build_stack(engine: Engine, *, resolvers: Sequence[Resolver] = ()) -> EffacedStack:
    """Wire a stack with a recording sink, then create all tables.

    ``create_all`` runs after ``from_base`` so the freshly mounted
    ``effaced_*`` tables are included.
    """
    stack = EffacedStack.from_base(
        Base, sessionmaker(engine), resolvers=resolvers, audit_sink=RecordingSink()
    )
    Base.metadata.create_all(engine)
    return stack


def seed_two_users(stack: EffacedStack) -> None:
    """Subjects 1 and 2, each with an invoice."""
    with stack.session_factory.begin() as session:
        session.add_all(
            [
                User(id=1, email="alice@example.com", name="Alice Doe", theme="dark"),
                User(id=2, email="bob@example.com", name="Bob Roe", theme="light"),
                Invoice(id=1, user_id=1, billing_address="1 Alice Street"),
                Invoice(id=2, user_id=2, billing_address="2 Bob Street"),
            ]
        )


def subject_one() -> Subject:
    """The default provider: the request is about subject 1, no external refs."""
    return Subject(subject_id="1")


def build_app(
    gdpr: EffacedFastAPI,
    *,
    provider: SubjectProvider = subject_one,
    prefix: str = "/me",
    restriction: bool = False,
) -> FastAPI:
    """Mount the router on a fresh app under the given prefix."""
    app = FastAPI()
    app.include_router(gdpr.router(subject=provider, restriction=restriction), prefix=prefix)
    return app


def recorded_event_types(stack: EffacedStack) -> set[str]:
    """The event-type values the stack's recording sink has seen."""
    sink = stack.audit_sink
    assert isinstance(sink, RecordingSink)
    return {event.event_type.value for event in sink.events}
