"""Django authoring helpers — declare PII on Django models.

Unlike the SQLAlchemy adapter (which rides the ``info`` dict on columns),
Django fields carry no per-column metadata slot, so declarations are
attached through a nested ``Effaced`` class on the model and the
:func:`~effaced_django.effaced_model` decorator. These helpers return the
core annotation objects unchanged — the manifest the engine consumes is
identical regardless of which adapter authored it.

Example:
    >>> from django.db import models
    >>> from effaced import PiiCategory
    >>> from effaced_django import effaced_model, pii, subject_link
    >>>
    >>> @effaced_model(subject_link(""))
    ... class User(models.Model):
    ...     email = models.EmailField()
    ...
    ...     class Effaced:
    ...         email = pii(PiiCategory.CONTACT)
"""

from __future__ import annotations

from effaced.annotations import PiiSpec, RetentionPolicy, SubjectLink
from effaced.categories import ErasureStrategy, LegalBasis, PiiCategory


def pii(
    category: PiiCategory,
    *,
    erasure: ErasureStrategy = ErasureStrategy.DELETE,
    retention: RetentionPolicy | None = None,
    legal_basis: LegalBasis | None = None,
    purpose: str | None = None,
    description: str | None = None,
) -> PiiSpec:
    """Declare a Django model field as personal data.

    Assign the result to an attribute named after the field on the model's
    nested ``Effaced`` class. The signature mirrors
    :func:`effaced.pii`; only the return type differs (a bare
    :class:`~effaced.PiiSpec`, since Django has no ``info`` dict to wrap).

    Args:
        category: What kind of personal data the field holds.
        erasure: Erasure behaviour; defaults to deletion.
        retention: Legal retention duty; required for ``RETAIN``.
        legal_basis: Lawful basis, surfaced in Art. 15 exports.
        purpose: Processing purpose, surfaced in Art. 15 exports.
        description: Free-text note for audits.

    Returns:
        The column's :class:`~effaced.PiiSpec`.
    """
    return PiiSpec(
        category=category,
        erasure=erasure,
        retention=retention,
        legal_basis=legal_basis,
        purpose=purpose,
        description=description,
    )


def subject_link(path: str, *, subject_id_column: str = "id") -> SubjectLink:
    """Declare how a Django model reaches the data subject.

    Pass the result to :func:`~effaced_django.effaced_model`. The subject
    model declares ``subject_link("")``; every other model names the dotted
    chain of **target tables** (``Model._meta.db_table`` values) by which a
    foreign key reaches the subject — e.g. a comment two hops away declares
    ``subject_link("blog_post.auth_user")``. This differs from the
    SQLAlchemy adapter (which names ORM relationship attributes) because the
    Django adapter resolves the graph from foreign-key constraints, not ORM
    mappers (:func:`effaced.resolve_subject_graph_from_fk`).

    Args:
        path: Dotted chain of target table names to the subject table.
        subject_id_column: Identifier column on the subject table.

    Returns:
        The :class:`~effaced.SubjectLink`.
    """
    return SubjectLink(path=path, subject_id_column=subject_id_column)
