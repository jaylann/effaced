"""Core annotation models — the data map vocabulary, storage-agnostic.

Authoring helpers that attach these to concrete ORMs live in
``effaced.adapters`` (SQLAlchemy first). The models here are pure data:
they validate, serialize, and never import a database library.
"""

from effaced.annotations.composite_subject_id import CompositeSubjectId
from effaced.annotations.correction import Correction
from effaced.annotations.pii_spec import PiiSpec
from effaced.annotations.retention_policy import RetentionPolicy
from effaced.annotations.subject_identifier import (
    SubjectIdentifier,
    canonical_subject_id,
    parse_canonical,
)
from effaced.annotations.subject_link import SubjectLink
from effaced.annotations.subject_ref import SubjectRef

__all__ = [
    "CompositeSubjectId",
    "Correction",
    "PiiSpec",
    "RetentionPolicy",
    "SubjectIdentifier",
    "SubjectLink",
    "SubjectRef",
    "canonical_subject_id",
    "parse_canonical",
]
