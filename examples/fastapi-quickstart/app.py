"""The three trigger points — the entire integration surface.

Run with: uvicorn app:app --reload  (requires fastapi + uvicorn)

The effaced engines are sync (ADR 0006): in async routes dispatch them
via ``run_in_threadpool``; plain ``def`` routes need nothing — FastAPI
runs them in its threadpool automatically.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from models import Base
from sqlalchemy.orm import Session, sessionmaker

from effaced import (
    ConsentLedger,
    ConsentRecord,
    DatabaseAuditSink,
    ErasurePlanner,
    Exporter,
    ResolverRegistry,
    bind_tables,
    collect_data_map,
)
from effaced_stripe import StripeResolver

app = FastAPI()

data_map = collect_data_map(Base.metadata)
tables = bind_tables(Base.metadata)  # effaced-owned tables ride your migrations

registry = ResolverRegistry()
registry.register(StripeResolver(api_key="rk_test_..."))


def get_session() -> Session:  # placeholder: use your real session dependency
    raise NotImplementedError


session_factory: sessionmaker[Session] = sessionmaker()  # placeholder: bind your engine

exporter = Exporter(data_map, registry)
planner = ErasurePlanner(data_map, registry)
audit = DatabaseAuditSink(session_factory, tables.audit_events)
consent = ConsentLedger(tables.consent_records, audit)


@app.post("/me/consent")
def record_consent(purpose: str, granted: bool) -> dict[str, str]:
    """Trigger point 1: record consent (grant or withdrawal — same call).

    A plain ``def`` route — FastAPI runs it in its threadpool, so the
    sync call needs no wrapping.
    """
    record = ConsentRecord(
        subject_id="current-user-id",
        purpose=purpose,
        policy_version="2026-06",
        granted=granted,
        recorded_at=datetime.now(UTC),
        source="api",
    )
    consent.record(get_session(), record)
    return {"status": "recorded"}


@app.get("/me/export")
async def export_me() -> dict[str, object]:
    """Trigger point 2: Art. 15 export across DB + Stripe."""
    bundle = await run_in_threadpool(exporter.export_subject, get_session(), "current-user-id")
    return bundle.model_dump(mode="json")


@app.delete("/me")
async def erase_me() -> dict[str, object]:
    """Trigger point 3: Art. 17 erasure — atomic locally, saga externally."""
    result = await run_in_threadpool(planner.erase_subject, get_session(), "current-user-id")
    return result.model_dump(mode="json")
