"""External refs flow from the Subject through erasure into the outbox and saga."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from fastapi_test_app import build_app, build_stack, make_engine, seed_two_users

from effaced import OutboxStatus, SubjectRef
from effaced.testing import InMemoryResolver
from effaced_fastapi import EffacedFastAPI, Subject


def subject_with_crm_ref() -> Subject:
    """Subject 1 also lives in the fake external system as ``crm-1``."""
    return Subject(subject_id="1", refs=(SubjectRef(kind="crm", value="crm-1"),))


def test_erase_enqueues_and_saga_converges() -> None:
    resolver = InMemoryResolver("crm")
    gdpr = EffacedFastAPI(stack=build_stack(make_engine(), resolvers=(resolver,)))
    seed_two_users(gdpr.stack)
    client = TestClient(build_app(gdpr, provider=subject_with_crm_ref))

    result = client.request("DELETE", "/me").json()
    assert result["enqueued_external"] == ["crm"]
    counts = gdpr.stack.outbox.status_counts()
    assert counts[OutboxStatus.PENDING] == 1

    drained = asyncio.run(gdpr.stack.saga_runner.run_once())
    assert drained == 1
    counts = gdpr.stack.outbox.status_counts()
    assert counts[OutboxStatus.SUCCEEDED] == 1
    assert counts[OutboxStatus.PENDING] == 0


def test_export_includes_resolver_sources() -> None:
    resolver = InMemoryResolver("crm")
    gdpr = EffacedFastAPI(stack=build_stack(make_engine(), resolvers=(resolver,)))
    seed_two_users(gdpr.stack)
    client = TestClient(build_app(gdpr, provider=subject_with_crm_ref))

    bundle = client.get("/me/export").json()
    assert bundle["incomplete_sources"] == []
    # The resolver holds nothing for crm-1 — an empty external export is
    # still a completed source, never a silent omission.
    assert any(record["value"] == "alice@example.com" for record in bundle["records"])
