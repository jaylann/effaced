"""Shared annotated Django models for the adapter tests.

Defined once (Django forbids two model classes with the same app_label and
name) and imported by every test module. A three-level chain — user <- post
<- comment — exercises the subject table, a single foreign-key hop, and a
two-hop path resolved through foreign keys.
"""

from __future__ import annotations

import django_test_setup  # noqa: F401  # configures Django before the models below are defined
from django.db import models
from django_test_setup import ensure_configured

from effaced import PiiCategory
from effaced_django import AnnotationRegistry, effaced_model, pii, subject_link

ensure_configured()  # Django must be set up before the model classes below are defined.

TEST_REGISTRY = AnnotationRegistry()
"""Isolated registry for the fixture models (keeps the default one clean)."""


@effaced_model(subject_link(""), registry=TEST_REGISTRY)
class User(models.Model):
    """The data subject."""

    email = models.EmailField()
    display_name = models.CharField(max_length=100)

    class Meta:
        app_label = "effaced_django_tests"
        db_table = "django_users"

    class Effaced:
        email = pii(PiiCategory.CONTACT)
        display_name = pii(PiiCategory.IDENTITY)


@effaced_model(subject_link("django_users"), registry=TEST_REGISTRY)
class Post(models.Model):
    """Reaches the subject via its foreign key to ``django_users``."""

    author = models.ForeignKey(User, on_delete=models.CASCADE)
    body = models.TextField()

    class Meta:
        app_label = "effaced_django_tests"
        db_table = "django_posts"

    class Effaced:
        body = pii(PiiCategory.BEHAVIORAL)


@effaced_model(subject_link("django_posts.django_users"), registry=TEST_REGISTRY)
class Comment(models.Model):
    """Reaches the subject two hops out: comment -> post -> user."""

    post = models.ForeignKey(Post, on_delete=models.CASCADE)
    text = models.TextField()

    class Meta:
        app_label = "effaced_django_tests"
        db_table = "django_comments"

    class Effaced:
        text = pii(PiiCategory.BEHAVIORAL)
