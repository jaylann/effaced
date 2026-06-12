"""The load-bearing FastAPI assumption: a ``def`` route may depend on an async provider."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from fastapi.testclient import TestClient
from fastapi_test_app import build_app, build_stack, make_engine, seed_two_users

from effaced_fastapi import EffacedFastAPI, Subject


async def fake_auth(x_user_id: Annotated[str, Header()]) -> str:
    """Stand-in for an app's async auth dependency."""
    return x_user_id


async def async_provider(user_id: Annotated[str, Depends(fake_auth)]) -> Subject:
    """An async provider chained on another async dependency."""
    return Subject(subject_id=user_id)


def test_sync_routes_resolve_async_subject_providers() -> None:
    gdpr = EffacedFastAPI(stack=build_stack(make_engine()))
    seed_two_users(gdpr.stack)
    client = TestClient(build_app(gdpr, provider=async_provider))

    bundle = client.get("/me/export", headers={"X-User-Id": "2"}).json()
    values = {record["value"] for record in bundle["records"]}
    assert "bob@example.com" in values
    assert not any("alice" in str(value) for value in values)

    result = client.request("DELETE", "/me", headers={"X-User-Id": "2"}).json()
    assert result["subject_id"] == "2"
    assert result["anonymized"] == {"users": 1}
