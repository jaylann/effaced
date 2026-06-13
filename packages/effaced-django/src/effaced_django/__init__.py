"""Django ORM adapter for effaced.

Author PII declarations on Django models with :func:`effaced_model`,
:func:`pii`, and :func:`subject_link`; translate them into the effaced
manifest and a foreign-key-resolved subject graph; and wire every engine with
:class:`DjangoEffacedStack`. Execution reuses the SQLAlchemy executors on a
``MetaData`` derived from ``Model._meta`` (ADR 0006), so erasure/export/audit
semantics are identical to the SQLAlchemy adapter.
"""

from effaced_django.collector import collect_django_data_map
from effaced_django.effaced_stack import DjangoEffacedStack
from effaced_django.errors import EffacedDjangoError
from effaced_django.info import pii, subject_link
from effaced_django.introspection import build_metadata
from effaced_django.registry import (
    AnnotationRegistry,
    ModelAnnotation,
    default_registry,
    effaced_model,
)

__all__ = [
    "AnnotationRegistry",
    "DjangoEffacedStack",
    "EffacedDjangoError",
    "ModelAnnotation",
    "build_metadata",
    "collect_django_data_map",
    "default_registry",
    "effaced_model",
    "pii",
    "subject_link",
]
