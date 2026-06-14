"""Property proofs for :class:`SupabasePostgrestResolver`.

Across randomly generated multi-table / multi-subject stores — with
deliberately overlapping subject-id stems (``"4"`` vs ``"42"``) to catch
substring or operator-injection bugs in the ``eq`` filter — prove the
guarantees the saga runner relies on: no cross-subject bleed, idempotent
convergence, and that export is stable until erasure.
"""

from __future__ import annotations

import asyncio

import pytest
from fake_postgrest_transport import FakePostgrestTransport
from hypothesis import given, settings
from hypothesis import strategies as st

from effaced import PiiCategory, SubjectRef
from effaced_supabase import PostgrestColumn, PostgrestTable, SupabasePostgrestResolver

BASE_URL = "https://project.supabase.co"
KEY = "service-role-test-key"

PROFILES = PostgrestTable(
    name="profiles",
    subject_column="user_id",
    columns=(PostgrestColumn(name="email", category=PiiCategory.CONTACT),),
)
NOTES = PostgrestTable(
    name="notes",
    subject_column="author_id",
    columns=(PostgrestColumn(name="body", category=PiiCategory.COMMUNICATION),),
)
TABLES = (PROFILES, NOTES)

# Short numeric ids force overlapping stems like "4" / "42" / "421".
_ids = st.lists(st.text("0123456789", min_size=1, max_size=3), min_size=2, max_size=6, unique=True)


def _store(subjects: list[str]) -> FakePostgrestTransport:
    return FakePostgrestTransport(
        tables={
            "profiles": [{"user_id": s, "email": f"{s}@example.com"} for s in subjects],
            "notes": [{"author_id": s, "body": f"note-{s}"} for s in subjects],
        }
    )


def _ref(value: str) -> SubjectRef:
    return SubjectRef(kind="supabase_postgrest", value=value)


def _export_fields(resolver: SupabasePostgrestResolver, subject: str) -> set[tuple[str, object]]:
    export = asyncio.run(resolver.export_subject(_ref(subject)))
    return {(r.field, r.value) for r in export.records}


@pytest.mark.property
@settings(deadline=None)
@given(subjects=_ids, data=st.data())
def test_erase_touches_exactly_the_target_subject(subjects: list[str], data: st.DataObject) -> None:
    target = data.draw(st.sampled_from(subjects), label="target")
    resolver = SupabasePostgrestResolver(BASE_URL, KEY, TABLES, transport=_store(subjects))
    bystanders = [s for s in subjects if s != target]
    before = {s: _export_fields(resolver, s) for s in bystanders}

    erasure = asyncio.run(resolver.erase_subject(_ref(target)))

    assert erasure.already_absent is False
    assert _export_fields(resolver, target) == set()
    for bystander in bystanders:
        assert _export_fields(resolver, bystander) == before[bystander]


@pytest.mark.property
@settings(deadline=None)
@given(subjects=_ids, data=st.data())
def test_erase_converges_and_is_idempotent(subjects: list[str], data: st.DataObject) -> None:
    target = data.draw(st.sampled_from(subjects), label="target")
    resolver = SupabasePostgrestResolver(BASE_URL, KEY, TABLES, transport=_store(subjects))

    first = asyncio.run(resolver.erase_subject(_ref(target)))
    second = asyncio.run(resolver.erase_subject(_ref(target)))

    assert first.already_absent is False
    assert second.already_absent is True
    assert _export_fields(resolver, target) == set()
