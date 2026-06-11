"""Supabase-specific resolver behavior: mapping, transport wiring, error taxonomy."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fake_gotrue_transport import FakeGoTrueTransport

from effaced import PiiCategory, ResolverError, SubjectRef
from effaced_supabase import SupabaseAuthResolver
from effaced_supabase.auth_export_records import user_records

USER_ID = "11111111-2222-4333-8444-555555555555"
OTHER_ID = "99999999-8888-4777-8666-555555555555"

FULL_USER = {
    "email": "ada@example.com",
    "phone": "4915112345678",
    "user_metadata": {"nickname": "ada", "street": "1 Analytical Way"},
    "app_metadata": {"provider": "email", "plan": "pro"},
    "identities": [{"identity_data": {"email": "ada@example.com"}}],
}

BASE_URL = "https://project.supabase.co"
KEY = "service-role-test-key"


def make_resolver(fake: FakeGoTrueTransport) -> SupabaseAuthResolver:
    return SupabaseAuthResolver(BASE_URL, KEY, transport=fake)


def export(resolver: SupabaseAuthResolver, user_id: str = USER_ID):
    return asyncio.run(resolver.export_subject(SubjectRef(kind="supabase_auth", value=user_id)))


def erase(resolver: SupabaseAuthResolver, user_id: str = USER_ID):
    return asyncio.run(resolver.erase_subject(SubjectRef(kind="supabase_auth", value=user_id)))


def test_export_maps_email_and_phone_as_contact():
    bundle = export(make_resolver(FakeGoTrueTransport(users={USER_ID: FULL_USER})))
    assert {r.field: (r.value, r.category) for r in bundle.records} == {
        "user.email": ("ada@example.com", PiiCategory.CONTACT),
        "user.phone": ("4915112345678", PiiCategory.CONTACT),
    }
    assert all(r.source == "supabase_auth" for r in bundle.records)
    assert all(r.legal_basis is None for r in bundle.records)


def test_export_never_reads_user_or_app_metadata():
    """Caller-defined metadata is unknowable and must never reach an export."""
    bundle = export(make_resolver(FakeGoTrueTransport(users={USER_ID: FULL_USER})))
    fields = {r.field for r in bundle.records}
    assert fields == {"user.email", "user.phone"}
    values = " ".join(str(r.value) for r in bundle.records)
    assert "Analytical" not in values
    assert "pro" not in values


def test_export_skips_empty_string_fields():
    """GoTrue stores an unset phone/email as "" — empty means not held."""
    fake = FakeGoTrueTransport(users={USER_ID: {"email": "ada@example.com", "phone": ""}})
    bundle = export(make_resolver(fake))
    assert [(r.field, r.value) for r in bundle.records] == [("user.email", "ada@example.com")]


def test_export_skips_missing_and_non_string_fields():
    fake = FakeGoTrueTransport(users={USER_ID: {"email": None, "phone": 4915112345678}})
    bundle = export(make_resolver(fake))
    assert bundle.records == ()


def test_user_records_of_a_non_object_body_is_empty():
    """A body that is not a JSON object yields no records, never an error."""
    assert user_records(["not", "a", "user"]) == ()
    assert user_records(None) == ()


def test_erase_deletes_user_and_reports_detail():
    fake = FakeGoTrueTransport(users={USER_ID: FULL_USER})
    outcome = erase(make_resolver(fake))
    assert outcome.already_absent is False
    assert outcome.detail == "user deleted in supabase auth"
    assert USER_ID in fake.deleted


def test_erase_of_unknown_user_is_already_absent():
    outcome = erase(make_resolver(FakeGoTrueTransport()))
    assert outcome.already_absent is True
    assert outcome.detail == "user already absent in supabase auth"


def test_erase_touches_only_the_requested_user():
    fake = FakeGoTrueTransport(users={USER_ID: FULL_USER, OTHER_ID: {"email": "bob@example.com"}})
    resolver = make_resolver(fake)
    erase(resolver)
    other = export(resolver, OTHER_ID)
    assert [(r.field, r.value) for r in other.records] == [("user.email", "bob@example.com")]
    assert fake.deleted == {USER_ID}


def test_requests_carry_the_service_role_key_in_both_headers():
    """GoTrue authorizes the Bearer token; Supabase's gateway wants apikey too."""
    fake = FakeGoTrueTransport(users={USER_ID: FULL_USER})
    resolver = make_resolver(fake)
    export(resolver)
    erase(resolver)
    assert len(fake.requests) == 2
    for request in fake.requests:
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert request.headers["apikey"] == KEY


def test_requests_target_the_admin_users_path_even_with_trailing_slash():
    fake = FakeGoTrueTransport(users={USER_ID: FULL_USER})
    resolver = SupabaseAuthResolver(BASE_URL + "/", KEY, transport=fake)
    export(resolver)
    erase(resolver)
    assert [r.url.path for r in fake.requests] == [f"/auth/v1/admin/users/{USER_ID}"] * 2


@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_nonretryable_statuses_raise_resolver_error(status):
    resolver = make_resolver(FakeGoTrueTransport(error_status=status))
    with pytest.raises(ResolverError):
        export(resolver)
    with pytest.raises(ResolverError):
        erase(resolver)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_statuses_propagate_for_saga_retry(status):
    resolver = make_resolver(FakeGoTrueTransport(error_status=status))
    with pytest.raises(httpx.HTTPStatusError) as export_error:
        export(resolver)
    with pytest.raises(httpx.HTTPStatusError) as erase_error:
        erase(resolver)
    assert not isinstance(export_error.value, ResolverError)
    assert not isinstance(erase_error.value, ResolverError)


def test_connection_fault_propagates_for_saga_retry():
    resolver = make_resolver(FakeGoTrueTransport(connection_error=True))
    with pytest.raises(httpx.ConnectError):
        export(resolver)
    with pytest.raises(httpx.ConnectError):
        erase(resolver)


def test_ref_value_with_path_separators_cannot_target_another_user():
    """The id is percent-encoded into a single path segment.

    Without encoding, a ref value like ``"<id>/../<other>"`` would be
    path-normalized to the *other* user's endpoint before the request
    leaves the process — a wrong-target erasure.
    """
    fake = FakeGoTrueTransport(users={OTHER_ID: {"email": "bob@example.com"}})
    resolver = make_resolver(fake)
    outcome = erase(resolver, f"{USER_ID}/../{OTHER_ID}")
    assert outcome.already_absent is True
    assert fake.deleted == set()
    assert OTHER_ID in fake.users
    other_path = f"/auth/v1/admin/users/{OTHER_ID}".encode("ascii")
    assert all(request.url.raw_path != other_path for request in fake.requests)


def test_resolver_error_messages_never_leak_the_subject_ref():
    """The user id is a subject reference — it must not reach the message.

    The translation interpolates only the action and status code, never
    the ref; this pins that, so a future regression piping ``ref.value``
    into a ResolverError is caught (semgrep's no-PII gate watches audit
    payloads, not exception strings).
    """
    sensitive_id = "USER-SENSITIVE-123"
    for status in (400, 401):
        resolver = make_resolver(FakeGoTrueTransport(error_status=status))
        with pytest.raises(ResolverError) as export_error:
            export(resolver, sensitive_id)
        with pytest.raises(ResolverError) as erase_error:
            erase(resolver, sensitive_id)
        assert sensitive_id not in str(export_error.value)
        assert sensitive_id not in str(erase_error.value)
