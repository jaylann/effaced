"""The three trigger points — the entire integration surface.

Run with: uvicorn app:app --reload  (requires fastapi + uvicorn)
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from models import Base
from sqlalchemy.orm import Session

from effaced import (
    ConsentLedger,
    ConsentRecord,
    ErasurePlanner,
    Exporter,
    ResolverRegistry,
    collect_data_map,
)
from effaced_stripe import StripeResolver

app = FastAPI()

data_map = collect_data_map(Base.metadata)

registry = ResolverRegistry()
registry.register(StripeResolver(api_key="rk_test_..."))

exporter = Exporter(data_map, registry)
planner = ErasurePlanner(data_map, registry)
consent = ConsentLedger()


def get_session() -> Session:  # placeholder: use your real session dependency
    raise NotImplementedError


@app.post("/me/consent")
async def record_consent(purpose: str, granted: bool) -> dict[str, str]:
    """Trigger point 1: record consent (grant or withdrawal — same call)."""
    record = ConsentRecord(
        subject_id="current-user-id",
        purpose=purpose,
        policy_version="2026-06",
        granted=granted,
        recorded_at=datetime.now(UTC),
        source="api",
    )
    await consent.record(get_session(), record)
    return {"status": "recorded"}


@app.get("/me/export")
async def export_me() -> dict[str, object]:
    """Trigger point 2: Art. 15 export across DB + Stripe."""
    bundle = await exporter.export_subject(get_session(), "current-user-id")
    return bundle.model_dump(mode="json")


@app.delete("/me")
async def erase_me() -> dict[str, object]:
    """Trigger point 3: Art. 17 erasure — atomic locally, saga externally."""
    result = await planner.erase_subject(get_session(), "current-user-id")
    return result.model_dump(mode="json")
