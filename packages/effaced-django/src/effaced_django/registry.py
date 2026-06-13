"""Collect effaced declarations off Django models into an annotation registry.

Django has no per-column metadata slot, so a model declares its personal
data on a nested ``Effaced`` class (field name -> :func:`effaced_django.pii`)
and its subject reachability through the :func:`effaced_model` decorator's
argument. The decorator records each model's declarations into an
:class:`AnnotationRegistry`; the introspection layer turns that registry into
SQLAlchemy metadata the core engine understands.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

from effaced.annotations import PiiSpec

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from django.db.models import Model

    from effaced.annotations import SubjectLink

    M = TypeVar("M", bound=Model)


@dataclass(frozen=True)
class ModelAnnotation:
    """One Django model's effaced declarations.

    Attributes:
        model: The declared Django model class.
        subject_link: How the model reaches the subject, or ``None`` if the
            model carries only PII columns and no declared link (an error
            the resolver raises loudly).
        column_specs: PII specs keyed by Django **field name** (not the DB
            column name — the introspector maps field names to columns).
    """

    model: type[Model]
    subject_link: SubjectLink | None
    column_specs: Mapping[str, PiiSpec]


class AnnotationRegistry:
    """An append-only collection of :class:`ModelAnnotation` records.

    Registration is explicit (never auto-discovered) — the registry is the
    auditable "where is my PII" declaration, mirroring the resolver registry.
    """

    def __init__(self) -> None:
        """Create an empty registry."""
        self._annotations: list[ModelAnnotation] = []

    def register(self, annotation: ModelAnnotation) -> None:
        """Add one model's declarations to the registry."""
        self._annotations.append(annotation)

    @property
    def annotations(self) -> tuple[ModelAnnotation, ...]:
        """The registered annotations, in registration order."""
        return tuple(self._annotations)

    def clear(self) -> None:
        """Drop every registration (used to isolate test modules)."""
        self._annotations.clear()


default_registry = AnnotationRegistry()
"""The process-wide registry that bare :func:`effaced_model` writes into."""


def effaced_model(
    link: SubjectLink | None = None,
    *,
    registry: AnnotationRegistry | None = None,
) -> Callable[[type[M]], type[M]]:
    """Register a Django model's effaced declarations, returning it unchanged.

    Reads the model's nested ``Effaced`` class for per-field
    :class:`~effaced.PiiSpec` declarations and records them together with the
    subject link.

    Args:
        link: How this model reaches the subject (see
            :func:`effaced_django.subject_link`); ``None`` for a model that
            declares no reachability.
        registry: Registry to record into; defaults to the process-wide
            :data:`default_registry`.

    Returns:
        A decorator that records the model and returns it unmodified.
    """
    target = registry if registry is not None else default_registry

    def decorate(cls: type[M]) -> type[M]:
        target.register(
            ModelAnnotation(
                model=cls,
                subject_link=link,
                column_specs=_read_effaced_specs(cls),
            )
        )
        return cls

    return decorate


def _read_effaced_specs(cls: type[Model]) -> dict[str, PiiSpec]:
    """Collect ``PiiSpec`` attributes off the model's nested ``Effaced`` class."""
    nested = cls.__dict__.get("Effaced")
    if nested is None:
        return {}
    return {name: value for name, value in vars(nested).items() if isinstance(value, PiiSpec)}
