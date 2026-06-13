"""Translate annotated Django models into an effaced-annotated SQLAlchemy schema.

The core engine is storage-agnostic but its executors operate on SQLAlchemy
``Table``/``Session`` objects (ADR 0006). This module builds those tables from
Django's model metadata — column names, types, primary keys, and foreign-key
constraints from ``Model._meta`` — and re-attaches the effaced declarations
collected by :mod:`effaced_django.registry` onto the SQLAlchemy ``info`` dicts
the collector reads. The result feeds :func:`effaced.collect_data_map` and
:func:`effaced.resolve_subject_graph_from_fk` unchanged, so the manifest and
subject graph are identical to a natively-authored SQLAlchemy schema.

The translation is connection-free: it reads declared field metadata, never a
live database, so it runs in tests and at import time without a DB.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db.models import ForeignKey as DjangoForeignKey
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Interval,
    LargeBinary,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    Table,
    Text,
    Time,
    Uuid,
)

from effaced.adapters.sqlalchemy import INFO_KEY
from effaced_django.errors import EffacedDjangoError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.db.models.fields import Field
    from sqlalchemy.types import TypeEngine

    from effaced_django.registry import ModelAnnotation

_TYPE_MAP: dict[str, type[TypeEngine[Any]]] = {
    "AutoField": Integer,
    "SmallAutoField": SmallInteger,
    "BigAutoField": BigInteger,
    "IntegerField": Integer,
    "SmallIntegerField": SmallInteger,
    "BigIntegerField": BigInteger,
    "PositiveIntegerField": Integer,
    "PositiveSmallIntegerField": SmallInteger,
    "PositiveBigIntegerField": BigInteger,
    "BooleanField": Boolean,
    "FloatField": Float,
    "DecimalField": Numeric,
    "CharField": String,
    "TextField": Text,
    "SlugField": String,
    "EmailField": String,
    "URLField": String,
    "GenericIPAddressField": String,
    "DateField": Date,
    "DateTimeField": DateTime,
    "TimeField": Time,
    "DurationField": Interval,
    "UUIDField": Uuid,
    "JSONField": JSON,
    "BinaryField": LargeBinary,
}


def build_metadata(
    annotations: Iterable[ModelAnnotation],
    *,
    metadata: MetaData | None = None,
) -> MetaData:
    """Build an effaced-annotated SQLAlchemy ``MetaData`` from Django models.

    Each annotation's model becomes a ``Table`` carrying the subject link in
    its ``info`` dict and its PII specs on the matching columns, with foreign
    keys translated so :func:`effaced.resolve_subject_graph_from_fk` can walk
    them.

    Args:
        annotations: The model annotations to translate (see
            :class:`effaced_django.AnnotationRegistry`).
        metadata: An existing ``MetaData`` to populate; a fresh one is
            created when omitted.

    Returns:
        The populated ``MetaData``.

    Raises:
        EffacedDjangoError: If a field's type has no SQLAlchemy equivalent.
    """
    target = metadata if metadata is not None else MetaData()
    for annotation in annotations:
        _build_table(annotation, target)
    return target


def _build_table(annotation: ModelAnnotation, metadata: MetaData) -> None:
    """Translate one annotated model into a ``Table`` on the metadata."""
    meta = annotation.model._meta  # _meta is Django's public model-introspection API
    columns = [
        _build_column(field, annotation)
        for field in meta.concrete_fields
        if field.column is not None
    ]
    table_info = {INFO_KEY: annotation.subject_link} if annotation.subject_link is not None else {}
    Table(meta.db_table, metadata, *columns, info=table_info)


def _build_column(field: Field[Any, Any], annotation: ModelAnnotation) -> Column[Any]:
    """Translate one Django field into a SQLAlchemy ``Column``."""
    spec = annotation.column_specs.get(field.name)
    info = {INFO_KEY: spec} if spec is not None else {}
    fk_args = _foreign_key(field)
    return Column(
        field.column,
        _sa_type(field),
        *fk_args,
        primary_key=bool(field.primary_key),
        nullable=bool(field.null),
        info=info,
    )


def _foreign_key(field: Field[Any, Any]) -> tuple[ForeignKey, ...]:
    """Return the SQLAlchemy ``ForeignKey`` for a Django relation field, if any."""
    if not isinstance(field, DjangoForeignKey):
        return ()
    target_field = field.target_field
    target_table = target_field.model._meta.db_table  # Django public introspection API
    return (ForeignKey(f"{target_table}.{target_field.column}"),)


def _sa_type(field: Field[Any, Any]) -> TypeEngine[Any]:
    """Map a Django field to a SQLAlchemy column type.

    Foreign keys take the type of the column they reference, so the join
    columns line up.
    """
    source: Field[Any, Any] = field
    if isinstance(field, DjangoForeignKey):
        source = field.target_field
    internal = source.get_internal_type()
    base = _TYPE_MAP.get(internal)
    if base is None:
        msg = (
            f"field {field.name!r} on {field.model.__name__!r} has type "
            f"{internal!r}, which effaced-django cannot map to a SQLAlchemy "
            f"type; the engine would not know how to scope or anonymize it"
        )
        raise EffacedDjangoError(msg)
    if base is String:
        max_length = source.max_length
        return String(max_length) if max_length is not None else String()
    return base()
