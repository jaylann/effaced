"""Proof that the quickstart is executable: it imports, and the three
trigger points run end-to-end against a real Postgres."""

from __future__ import annotations

import importlib
import os
import sys
from types import ModuleType

import pytest
from fastapi.testclient import TestClient

from effaced import AuditEventType


def _fresh_import(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Import the example app from scratch, Stripe-free and deterministic."""
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    monkeypatch.delenv("STRIPE_CUSTOMER_ID", raising=False)
    for name in ("app", "models"):
        sys.modules.pop(name, None)
    return importlib.import_module("app")


def test_quickstart_imports_and_exposes_the_three_trigger_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _fresh_import(monkeypatch)
    paths = {getattr(route, "path", None) for route in module.app.routes}
    assert {"/me/consent", "/me/export", "/me"} <= paths


def test_settings_driven_registration_records_the_stripe_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stripe-free, the registry is empty and the skip is recorded, not silent."""
    module = _fresh_import(monkeypatch)
    assert module.registry.all() == ()
    outcome = module.build.outcomes[0]
    assert outcome.name == "stripe"
    assert outcome.registered is False
    assert outcome.missing_keys == ("STRIPE_API_KEY",)


def _assert_consent_recorded(client: TestClient, module: ModuleType) -> None:
    """Trigger point 1: consent (Art. 7)."""
    response = client.post(
        "/me/consent",
        json={"purpose": "newsletter", "granted": True, "policy_version": "2026-06"},
        headers={"X-User-Id": "1"},
    )
    assert response.status_code == 200
    events = {event.event_type for event in module.audit.read("1")}
    assert AuditEventType.CONSENT_GRANTED in events


def _assert_export_complete(client: TestClient) -> None:
    """Trigger point 2: export (Art. 15)."""
    response = client.get("/me/export", headers={"X-User-Id": "1"})
    assert response.status_code == 200
    bundle = response.json()
    assert bundle["subject_id"] == "1"
    assert bundle["incomplete_sources"] == []
    values = {record["value"] for record in bundle["records"]}
    assert "alice@example.com" in values
    assert "1 Demo Street, Berlin" in values


def _assert_erasure_outcome(client: TestClient, module: ModuleType, models: ModuleType) -> None:
    """Trigger point 3: erasure (Art. 17) — anonymize, retain, no bleed."""
    response = client.delete("/me", headers={"X-User-Id": "1"})
    assert response.status_code == 200
    result = response.json()
    assert result["subject_id"] == "1"
    assert result["enqueued_external"] == []  # no Stripe configured
    assert result["anonymized"] == {"users": 1}
    assert result["retained"] == {"invoices": 1}

    with module.session_factory() as session:
        erased = session.get(models.User, 1)
        assert erased is not None  # row survives: `theme` is not PII-owned
        assert erased.email != "alice@example.com"
        invoice = session.get(models.Invoice, 1)
        assert invoice is not None
        assert invoice.billing_address == "1 Demo Street, Berlin"  # RETAIN
        untouched = session.get(models.User, 2)
        assert untouched is not None
        assert untouched.email == "bob@example.com"  # no cross-subject bleed

    events = {event.event_type for event in module.audit.read("1")}
    assert AuditEventType.ERASURE_LOCAL_COMPLETED in events


@pytest.mark.integration
def test_three_trigger_points_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    url = os.environ.get("EFFACED_TEST_DATABASE_URL")
    if not url:
        pytest.skip("EFFACED_TEST_DATABASE_URL not set")
    monkeypatch.setenv("DATABASE_URL", url)
    module = _fresh_import(monkeypatch)
    models = sys.modules["models"]
    module.Base.metadata.drop_all(module.engine)  # leftovers from aborted runs
    try:
        with TestClient(module.app) as client:  # lifespan creates tables + seeds user 1
            _assert_consent_recorded(client, module)
            _assert_export_complete(client)

            # A second subject that the erasure must not touch.
            with module.session_factory.begin() as session:
                session.add(models.User(id=2, email="bob@example.com", display_name="Bob"))
                session.add(models.Invoice(id=2, user_id=2, billing_address="2 Other Road"))

            _assert_erasure_outcome(client, module, models)
    finally:
        module.Base.metadata.drop_all(module.engine)
        module.engine.dispose()
