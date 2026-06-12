"""The three trigger points — the entire integration surface.

Setup and run instructions live in README.md next to this file:
point ``DATABASE_URL`` at Postgres, then ``uvicorn app:app --reload``.

The effaced engines are sync (ADR 0006): in async routes dispatch them
via ``run_in_threadpool``; plain ``def`` routes need nothing — FastAPI
runs them in its threadpool automatically.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Header
from fastapi.concurrency import run_in_threadpool
from models import Base, Invoice, User
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from effaced import (
    ConsentLedger,
    ConsentRecord,
    DatabaseAuditSink,
    ErasureExecutor,
    ErasurePlanner,
    Exporter,
    Outbox,
    ResolverSpec,
    SubjectRef,
    bind_tables,
    collect_data_map,
    registry_from_settings,
    resolve_subject_graph,
)
from effaced_stripe import StripeResolver

data_map = collect_data_map(Base.metadata)
graph = resolve_subject_graph(data_map, Base.registry)
tables = bind_tables(Base.metadata)  # effaced-owned tables ride your migrations

# Declarative registration — the spec list is your auditable "where is my PII"
# declaration; it is config-driven, not auto-discovered. The Stripe resolver
# only joins when its key is configured, so the example runs end-to-end against
# a local Postgres alone. ``build.outcomes`` records what was wired and what was
# skipped — log it at startup as your registration audit trail.
resolver_specs = (
    ResolverSpec(
        name="stripe",
        settings_keys=("STRIPE_API_KEY",),
        build=lambda settings: StripeResolver(api_key=settings["STRIPE_API_KEY"]),
    ),
)
build = registry_from_settings(resolver_specs)
registry = build.registry
stripe_key = os.environ.get("STRIPE_API_KEY")

engine = create_engine(
    os.environ.get("DATABASE_URL", "postgresql+psycopg://effaced:effaced@localhost:5432/effaced")
)
session_factory = sessionmaker(engine)

audit = DatabaseAuditSink(session_factory, tables.audit_events)
outbox = Outbox(session_factory, tables.outbox)
exporter = Exporter(data_map, graph, Base.metadata, audit, registry)
planner = ErasurePlanner(
    data_map,
    graph,
    registry,
    executor=ErasureExecutor(Base.metadata),
    outbox=outbox,
    audit_sink=audit,
)
consent = ConsentLedger(tables.consent_records, audit)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create the schema and seed one demo user — stand-in for your migrations."""
    Base.metadata.create_all(engine)
    with session_factory.begin() as session:
        if session.get(User, 1) is None:
            session.add(User(id=1, email="alice@example.com", display_name="Alice"))
            session.add(Invoice(id=1, user_id=1, billing_address="1 Demo Street, Berlin"))
    yield


app = FastAPI(lifespan=lifespan)


def get_session() -> Iterator[Session]:
    """One transaction per request — commits on success, rolls back on error."""
    with session_factory.begin() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
# Stand-in for your real auth dependency; the seeded demo user is X-User-Id: 1.
UserId = Annotated[str, Header(alias="X-User-Id")]


def external_refs() -> tuple[SubjectRef, ...]:
    """Where the subject lives outside the database (kind == resolver name).

    A real app would look up the Stripe customer id it stored at signup.
    """
    customer_id = os.environ.get("STRIPE_CUSTOMER_ID")
    if stripe_key and customer_id:
        return (SubjectRef(kind="stripe", value=customer_id),)
    return ()


@app.post("/me/consent")
def record_consent(
    user_id: UserId, session: SessionDep, purpose: str, granted: bool
) -> dict[str, str]:
    """Trigger point 1: record consent (grant or withdrawal — same call).

    A plain ``def`` route — FastAPI runs it in its threadpool, so the
    sync call needs no wrapping.
    """
    record = ConsentRecord(
        subject_id=user_id,
        purpose=purpose,
        policy_version="2026-06",
        granted=granted,
        recorded_at=datetime.now(UTC),
        source="api",
    )
    consent.record(session, record)
    return {"status": "recorded"}


@app.get("/me/export")
async def export_me(user_id: UserId, session: SessionDep) -> dict[str, object]:
    """Trigger point 2: Art. 15 export across the DB and registered resolvers."""
    bundle = await run_in_threadpool(
        exporter.export_subject, session, user_id, refs=external_refs()
    )
    return bundle.model_dump(mode="json")


@app.delete("/me")
async def erase_me(user_id: UserId, session: SessionDep) -> dict[str, object]:
    """Trigger point 3: Art. 17 erasure — atomic locally, saga externally."""
    result = await run_in_threadpool(planner.erase_subject, session, user_id, refs=external_refs())
    return result.model_dump(mode="json")
