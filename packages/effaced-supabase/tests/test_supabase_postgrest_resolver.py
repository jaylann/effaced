"""Behaviour tests for :class:`SupabasePostgrestResolver`.

Drives the resolver against the fake PostgREST transport: multi-column /
multi-row export, null-cell skipping, multi-table aggregation, erase
idempotency, the construction guard, and that a hostile subject id is
encoded and matched literally without bleeding into a sibling.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fake_postgrest_transport import FakePostgrestTransport

from effaced import PiiCategory, SubjectRef
from effaced.exceptions import ConfigurationError, ResolverError
from effaced_supabase import PostgrestColumn, PostgrestTable, SupabasePostgrestResolver

BASE_URL = "https://project.supabase.co"
KEY = "service-role-test-key"

PROFILES = PostgrestTable(
    name="profiles",
    subject_column="user_id",
    columns=(
        PostgrestColumn(name="full_name", category=PiiCategory.IDENTITY),
        PostgrestColumn(name="email", category=PiiCategory.CONTACT),
    ),
)
NOTES = PostgrestTable(
    name="notes",
    subject_column="author_id",
    columns=(PostgrestColumn(name="body", category=PiiCategory.COMMUNICATION),),
)


def _resolver(
    fake: FakePostgrestTransport, tables: tuple[PostgrestTable, ...]
) -> SupabasePostgrestResolver:
    return SupabasePostgrestResolver(BASE_URL, KEY, tables, transport=fake)


def _ref(value: str) -> SubjectRef:
    return SubjectRef(kind="supabase_postgrest", value=value)


def test_export_aggregates_columns_rows_and_tables() -> None:
    fake = FakePostgrestTransport(
        tables={
            "profiles": [{"user_id": "u1", "full_name": "Ada", "email": "ada@example.com"}],
            "notes": [
                {"author_id": "u1", "body": "first"},
                {"author_id": "u1", "body": "second"},
            ],
        }
    )
    export = asyncio.run(_resolver(fake, (PROFILES, NOTES)).export_subject(_ref("u1")))

    by_field = sorted((r.source, r.field, r.value, r.category) for r in export.records)
    assert by_field == [
        ("notes", "notes.body", "first", PiiCategory.COMMUNICATION),
        ("notes", "notes.body", "second", PiiCategory.COMMUNICATION),
        ("profiles", "profiles.email", "ada@example.com", PiiCategory.CONTACT),
        ("profiles", "profiles.full_name", "Ada", PiiCategory.IDENTITY),
    ]


def test_export_skips_null_and_missing_cells() -> None:
    fake = FakePostgrestTransport(
        tables={"profiles": [{"user_id": "u1", "full_name": None, "email": "ada@example.com"}]}
    )
    export = asyncio.run(_resolver(fake, (PROFILES,)).export_subject(_ref("u1")))

    assert [r.field for r in export.records] == ["profiles.email"]


def test_export_of_absent_subject_is_empty() -> None:
    fake = FakePostgrestTransport(tables={"profiles": [{"user_id": "other", "email": "x@y.z"}]})
    export = asyncio.run(_resolver(fake, (PROFILES,)).export_subject(_ref("u1")))

    assert export.records == ()


def test_erase_deletes_across_tables_and_is_idempotent() -> None:
    fake = FakePostgrestTransport(
        tables={
            "profiles": [{"user_id": "u1", "full_name": "Ada", "email": "ada@example.com"}],
            "notes": [{"author_id": "u1", "body": "first"}],
        }
    )
    resolver = _resolver(fake, (PROFILES, NOTES))

    first = asyncio.run(resolver.erase_subject(_ref("u1")))
    second = asyncio.run(resolver.erase_subject(_ref("u1")))

    assert first.already_absent is False
    assert second.already_absent is True
    assert fake.tables == {"profiles": [], "notes": []}


def test_erase_of_absent_subject_reports_already_absent() -> None:
    fake = FakePostgrestTransport(tables={"profiles": [{"user_id": "other"}]})
    erasure = asyncio.run(_resolver(fake, (PROFILES,)).erase_subject(_ref("u1")))

    assert erasure.already_absent is True
    assert fake.tables == {"profiles": [{"user_id": "other"}]}


def test_empty_tables_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError):
        SupabasePostgrestResolver(BASE_URL, KEY, ())


def test_nonretryable_status_raises_resolver_error() -> None:
    fake = FakePostgrestTransport(error_status=403)
    resolver = _resolver(fake, (PROFILES,))

    with pytest.raises(ResolverError, match="supabase postgrest"):
        asyncio.run(resolver.export_subject(_ref("u1")))
    with pytest.raises(ResolverError, match="supabase postgrest"):
        asyncio.run(resolver.erase_subject(_ref("u1")))


def test_rate_limit_propagates_for_retry() -> None:
    fake = FakePostgrestTransport(error_status=429)
    resolver = _resolver(fake, (PROFILES,))

    with pytest.raises(httpx.HTTPStatusError) as error:
        asyncio.run(resolver.export_subject(_ref("u1")))
    assert not isinstance(error.value, ResolverError)


def test_connection_fault_propagates_for_retry() -> None:
    fake = FakePostgrestTransport(connection_error=True)
    resolver = _resolver(fake, (PROFILES,))

    with pytest.raises(httpx.ConnectError):
        asyncio.run(resolver.erase_subject(_ref("u1")))


def test_hostile_subject_id_is_matched_literally_without_bleed() -> None:
    fake = FakePostgrestTransport(
        tables={
            "profiles": [
                {"user_id": "users/4", "email": "target@example.com"},
                {"user_id": "users/42", "email": "sibling@example.com"},
            ]
        }
    )
    resolver = _resolver(fake, (PROFILES,))

    erasure = asyncio.run(resolver.erase_subject(_ref("users/4")))

    assert erasure.already_absent is False
    assert fake.tables == {"profiles": [{"user_id": "users/42", "email": "sibling@example.com"}]}
