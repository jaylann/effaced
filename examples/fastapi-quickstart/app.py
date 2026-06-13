"""The integration is five lines — wire a stack, mount the router.

Setup and run instructions live in README.md next to this file:
point ``DATABASE_URL`` at Postgres, then ``uvicorn app:app --reload``.

Everything below that isn't the five lines is either demo glue (schema
creation and seeding stand in for your migrations) or the two things
that stay yours by design: the resolver registration and the auth
dependency that says who the subject is (ADR 0020).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Header
from models import Base, Invoice, User
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from effaced import EffacedStack, ResolverSpec, SubjectRef, registry_from_settings
from effaced_fastapi import EffacedFastAPI, SagaWorker, Subject
from effaced_stripe import StripeResolver

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


def current_subject(user_id: Annotated[str, Header(alias="X-User-Id")]) -> Subject:
    """Stand-in for your real auth dependency (the seeded demo user is 1).

    Your auth answers who the subject is; the refs say where else they
    live — a real app would look up the Stripe customer id it stored at
    signup instead of reading it from the environment.
    """
    customer_id = os.environ.get("STRIPE_CUSTOMER_ID")
    if stripe_key and customer_id:
        return Subject(subject_id=user_id, refs=(SubjectRef(kind="stripe", value=customer_id),))
    return Subject(subject_id=user_id)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create the schema, seed one demo user, drain the outbox while serving.

    Schema creation stands in for your migrations. The app owns its
    lifespan here, so the saga worker is composed directly; an app
    without its own lifespan would just pass ``gdpr.lifespan()``.
    """
    Base.metadata.create_all(engine)
    with session_factory.begin() as session:
        if session.get(User, 1) is None:
            session.add(User(id=1, email="alice@example.com", display_name="Alice"))
            session.add(Invoice(id=1, user_id=1, billing_address="1 Demo Street, Berlin"))
    worker = SagaWorker(stack.saga_runner)
    worker.start()
    yield
    worker.stop()


# The integration: one wired stack, one router around your auth.
stack = EffacedStack.from_base(Base, session_factory, registry=registry)
gdpr = EffacedFastAPI(stack=stack)
app = FastAPI(lifespan=lifespan)
app.include_router(gdpr.router(subject=current_subject), prefix="/me")

audit = stack.audit_sink
"""The append-only trail — every consent, export, and erasure lands here."""
