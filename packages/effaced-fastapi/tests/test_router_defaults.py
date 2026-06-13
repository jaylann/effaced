"""The default router trio: consent, export, erase — live engines, audited."""

from __future__ import annotations

from fastapi.testclient import TestClient
from fastapi_test_app import (
    Invoice,
    User,
    build_app,
    build_stack,
    make_engine,
    recorded_event_types,
    seed_two_users,
)

from effaced_fastapi import EffacedFastAPI


def make_client() -> tuple[TestClient, EffacedFastAPI]:
    engine = make_engine()
    gdpr = EffacedFastAPI(stack=build_stack(engine))
    seed_two_users(gdpr.stack)
    return TestClient(build_app(gdpr)), gdpr


def test_consent_record_and_status_roundtrip() -> None:
    client, gdpr = make_client()
    body = {"purpose": "newsletter", "granted": True, "policy_version": "2026-06"}
    response = client.post("/me/consent", json=body)
    assert response.status_code == 200
    assert response.json()["subject_id"] == "1"
    assert response.json()["source"] == "api"
    assert client.get("/me/consent/newsletter").json() is True
    client.post("/me/consent", json={**body, "granted": False})
    assert client.get("/me/consent/newsletter").json() is False
    assert "consent_granted" in recorded_event_types(gdpr.stack)
    assert "consent_withdrawn" in recorded_event_types(gdpr.stack)


def test_export_returns_the_subjects_data_only() -> None:
    client, _ = make_client()
    bundle = client.get("/me/export").json()
    values = {record["value"] for record in bundle["records"]}
    assert "alice@example.com" in values
    assert "1 Alice Street" in values
    assert not any("bob" in str(value) for value in values)
    assert bundle["incomplete_sources"] == []


def test_erase_anonymizes_retains_and_never_bleeds() -> None:
    client, gdpr = make_client()
    result = client.request("DELETE", "/me").json()
    assert result == {
        "subject_id": "1",
        "completed_at": result["completed_at"],
        "deleted": {},
        "anonymized": {"users": 1},
        "retained": {"invoices": 1},
        "enqueued_external": [],
    }
    with gdpr.stack.session_factory() as session:
        alice = session.get(User, 1)
        assert alice is not None
        assert alice.email != "alice@example.com"  # anonymized in place
        assert session.get(Invoice, 1) is not None  # retained, never deleted
        bob = session.get(User, 2)
        assert bob is not None
        assert bob.email == "bob@example.com"  # no cross-subject bleed
    assert "erasure_local_completed" in recorded_event_types(gdpr.stack)


def test_restriction_routes_absent_by_default() -> None:
    client, _ = make_client()
    assert client.get("/me/restriction").status_code == 404
    assert client.post("/me/restriction", json={"restricted": True}).status_code == 404


def test_validation_is_rejected_before_any_engine_call() -> None:
    client, gdpr = make_client()
    response = client.post("/me/consent", json={"purpose": "", "granted": True})
    assert response.status_code == 422
    assert recorded_event_types(gdpr.stack) == set()
