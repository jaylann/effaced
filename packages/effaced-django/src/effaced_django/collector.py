"""Collect a :class:`~effaced.DataMap` from annotated Django models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from effaced.adapters.sqlalchemy.collector import collect_data_map
from effaced_django.introspection import build_metadata
from effaced_django.registry import default_registry

if TYPE_CHECKING:
    from collections.abc import Iterable

    from effaced.manifest import DataMap
    from effaced_django.registry import ModelAnnotation


def collect_django_data_map(
    annotations: Iterable[ModelAnnotation] | None = None,
) -> DataMap:
    """Collect the manifest from annotated Django models.

    Translates the models to SQLAlchemy metadata and reuses the core
    collector, so the manifest is identical to a natively-authored
    SQLAlchemy schema.

    Args:
        annotations: The model annotations to collect; defaults to the
            process-wide :data:`effaced_django.default_registry`.

    Returns:
        The collected :class:`~effaced.DataMap`.

    Raises:
        EffacedDjangoError: If a field type cannot be mapped.
        ManifestError: If the annotations are invalid (propagated from
            :func:`effaced.collect_data_map`).
    """
    resolved = tuple(annotations) if annotations is not None else default_registry.annotations
    return collect_data_map(build_metadata(resolved))
