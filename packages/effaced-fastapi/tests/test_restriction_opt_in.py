"""The opt-in restriction endpoints: record, lift, scoped status."""

from __future__ import annotations

from fastapi.testclient import TestClient
from fastapi_test_app import (
    build_app,
    build_stack,
    make_engine,
    recorded_event_types,
    seed_two_users,
)

from effaced_fastapi import EffacedFastAPI


def make_client() -> tuple[TestClient, EffacedFastAPI]:
    gdpr = EffacedFastAPI(stack=build_stack(make_engine()))
    seed_two_users(gdpr.stack)
    return TestClient(build_app(gdpr, restriction=True)), gdpr


def test_place_and_lift_a_global_restriction() -> None:
    client, gdpr = make_client()
    response = client.post(
        "/me/restriction", json={"restricted": True, "reason": "accuracy contested"}
    )
    assert response.status_code == 200
    assert response.json()["subject_id"] == "1"
    assert client.get("/me/restriction").json() is True
    client.post("/me/restriction", json={"restricted": False})
    assert client.get("/me/restriction").json() is False
    assert "restriction_placed" in recorded_event_types(gdpr.stack)
    assert "restriction_lifted" in recorded_event_types(gdpr.stack)


def test_purpose_scoped_status() -> None:
    client, _ = make_client()
    client.post("/me/restriction", json={"restricted": True, "purpose": "ads"})
    assert client.get("/me/restriction", params={"purpose": "ads"}).json() is True
    # A purpose-scoped restriction is not a global one (status(purpose=None)
    # reads only global records — ADR 0014).
    assert client.get("/me/restriction").json() is False
