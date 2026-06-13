"""The Django collector yields the same manifest shape as the SQLAlchemy one."""

from __future__ import annotations

from django_fixture_models import TEST_REGISTRY

from effaced import PiiCategory
from effaced_django import collect_django_data_map


def test_collects_only_annotated_tables_and_columns() -> None:
    data_map = collect_django_data_map(TEST_REGISTRY.annotations)
    assert {entry.name for entry in data_map.tables} == {
        "django_users",
        "django_posts",
        "django_comments",
    }
    users = data_map.table("django_users")
    assert users.subject_link is not None
    assert users.subject_link.is_subject_table
    assert {column.name for column in users.columns} == {"email", "display_name"}


def test_pii_categories_survive_collection() -> None:
    data_map = collect_django_data_map(TEST_REGISTRY.annotations)
    users = data_map.table("django_users")
    by_name = {column.name: column.spec.category for column in users.columns}
    assert by_name == {
        "email": PiiCategory.CONTACT,
        "display_name": PiiCategory.IDENTITY,
    }
