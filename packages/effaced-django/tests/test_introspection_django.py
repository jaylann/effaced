"""The Django -> SQLAlchemy translation carries types, keys, FKs, and specs."""

from __future__ import annotations

import pytest
from django.db import models
from django_fixture_models import TEST_REGISTRY
from sqlalchemy import Integer, String, Text

from effaced import PiiCategory
from effaced.adapters.sqlalchemy import INFO_KEY
from effaced.annotations import PiiSpec, SubjectLink
from effaced_django import AnnotationRegistry, build_metadata, effaced_model, pii, subject_link
from effaced_django.errors import EffacedDjangoError


def test_translates_tables_columns_and_types() -> None:
    metadata = build_metadata(TEST_REGISTRY.annotations)
    assert set(metadata.tables) == {"django_users", "django_posts", "django_comments"}
    users = metadata.tables["django_users"]
    assert users.c.id.primary_key
    assert isinstance(users.c.email.type, String)
    assert isinstance(users.c.id.type, Integer)
    assert isinstance(metadata.tables["django_posts"].c.body.type, Text)


def test_subject_link_and_pii_specs_ride_the_info_dict() -> None:
    metadata = build_metadata(TEST_REGISTRY.annotations)
    users = metadata.tables["django_users"]
    link = users.info[INFO_KEY]
    assert isinstance(link, SubjectLink)
    assert link.is_subject_table
    spec = users.c.email.info[INFO_KEY]
    assert isinstance(spec, PiiSpec)
    assert spec.category is PiiCategory.CONTACT


def test_foreign_key_constraint_is_translated() -> None:
    metadata = build_metadata(TEST_REGISTRY.annotations)
    posts = metadata.tables["django_posts"]
    targets = {fk.referred_table.name for fk in posts.foreign_key_constraints}
    assert targets == {"django_users"}
    # the FK column follows Django's "<field>_id" convention
    assert "author_id" in posts.c


def test_string_max_length_is_carried() -> None:
    metadata = build_metadata(TEST_REGISTRY.annotations)
    name = metadata.tables["django_users"].c.display_name
    assert isinstance(name.type, String)
    assert name.type.length == 100


def test_unmapped_field_type_fails_loudly() -> None:
    registry = AnnotationRegistry()

    @effaced_model(subject_link(""), registry=registry)
    class WithFile(models.Model):
        attachment = models.FileField(upload_to="uploads/")

        class Meta:
            app_label = "effaced_django_tests"
            db_table = "django_with_file"

        class Effaced:
            attachment = pii(PiiCategory.TECHNICAL)

    with pytest.raises(EffacedDjangoError, match="FileField"):
        build_metadata(registry.annotations)
