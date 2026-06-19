"""The integration is five lines — wire a stack, mount the router.

Setup and run instructions live in README.md next to this file:
point ``DATABASE_URL`` at Postgres, then ``uvicorn app:app --reload``.

Everything below that isn't the five lines is either demo glue (schema
creation and seeding stand in for your migrations) or the two things
that stay yours by design: the resolver registration and the auth
dependency that says who the subject is (ADR 0020).
"""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException
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


def _subject_for(user_id: str) -> Subject:
    """Build the request's Subject once the caller's identity is settled.

    Your auth answers *who* the subject is; the refs say where else they
    live — a real app would look up the Stripe customer id it stored at
    signup instead of reading it from the environment.
    """
    customer_id = os.environ.get("STRIPE_CUSTOMER_ID")
    if stripe_key and customer_id:
        return Subject(subject_id=user_id, refs=(SubjectRef(kind="stripe", value=customer_id),))
    return Subject(subject_id=user_id)


# --- INSECURE: DEMO ONLY -----------------------------------------------------
# This trusts whatever id the caller writes into the X-User-Id header, so any
# caller can export or erase ANY subject — an insecure direct object reference
# (IDOR). It exists so the curl examples in README.md run without a login step.
# DO NOT copy this into a real app: the router authorizes nothing (ADR 0020),
# so this dependency is the only access control there is. Use `secure_subject`
# below, or any auth that proves the caller IS the subject.
def current_subject(user_id: Annotated[str, Header(alias="X-User-Id")]) -> Subject:
    """DEMO ONLY — INSECURE. Trusts the X-User-Id header verbatim (IDOR)."""
    return _subject_for(user_id)


# --- SECURE: derive the subject from a verified credential -------------------
# The pattern to copy. The subject is taken from a credential the caller cannot
# forge — here a `<user_id>.<hex-hmac>` bearer token signed with a server-side
# secret, standing in for your real session/JWT verification. The signed user
# id IS the subject, so there is no separate id for the caller to tamper with;
# a bad signature is rejected before any subject is resolved. If your token and
# the route carried the subject id independently, you would compare them and
# reject a mismatch here — that comparison is the IDOR guard.
SESSION_SECRET = os.environ.get("SESSION_SECRET", "demo-secret-change-me").encode()


def _verify_session_token(token: str) -> str:
    """Return the user id a valid token attests to, else reject the request."""
    # rpartition, not partition: the signature is the LAST segment, and a subject
    # id routinely contains dots (an email, a composite key), so splitting on the
    # first dot would corrupt the user_id and 401 a legitimate dotted subject.
    user_id, sep, signature = token.rpartition(".")
    expected = hmac.new(SESSION_SECRET, user_id.encode(), hashlib.sha256).hexdigest()
    if not sep or not user_id or not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="invalid session token")
    return user_id


def secure_subject(
    authorization: Annotated[str, Header(alias="Authorization")],
) -> Subject:
    """Resolve the subject from a verified bearer token — never a raw id.

    The caller proves who they are with a signed token; the verified user
    id becomes the subject. A forged or absent token is rejected, so this
    dependency can only ever return the authenticated caller's own subject.
    """
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    return _subject_for(_verify_session_token(token))


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
# DEMO ONLY — INSECURE: trusts X-User-Id so the README curl examples run.
app.include_router(gdpr.router(subject=current_subject), prefix="/me")
# The pattern to copy: the same router behind verified-credential auth.
app.include_router(gdpr.router(subject=secure_subject), prefix="/secure/me")

audit = stack.audit_sink
"""The append-only trail — every consent, export, and erasure lands here."""
