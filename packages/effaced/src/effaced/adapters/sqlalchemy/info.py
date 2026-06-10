"""SQLAlchemy authoring helpers — attach effaced declarations to models.

Example:
    >>> from sqlalchemy.orm import Mapped, mapped_column
    >>> from effaced import PiiCategory, pii
    >>>
    >>> class User(Base):
    ...     __tablename__ = "users"
    ...     id: Mapped[int] = mapped_column(primary_key=True)
    ...     email: Mapped[str] = mapped_column(info=pii(PiiCategory.CONTACT))
"""

from __future__ import annotations

from typing import Any

from effaced.annotations import PiiSpec, RetentionPolicy, SubjectLink
from effaced.categories import ErasureStrategy, LegalBasis, PiiCategory

INFO_KEY = "effaced"
"""Key under which effaced metadata is stored in SQLAlchemy ``info`` dicts."""


def pii(
    category: PiiCategory,
    *,
    erasure: ErasureStrategy = ErasureStrategy.DELETE,
    retention: RetentionPolicy | None = None,
    legal_basis: LegalBasis | None = None,
    purpose: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Declare a column as personal data.

    Returns an ``info`` dict fragment for ``mapped_column(info=...)`` /
    ``Column(info=...)``. Keeping this a function (not a bare dict) lets the
    manifest format evolve behind a stable call signature.

    Args:
        category: What kind of personal data the column holds.
        erasure: Erasure behaviour; defaults to deletion.
        retention: Legal retention duty; required for ``RETAIN``.
        legal_basis: Lawful basis, surfaced in Art. 15 exports.
        purpose: Processing purpose, surfaced in Art. 15 exports.
        description: Free-text note for audits.

    Returns:
        A dict suitable for SQLAlchemy's ``info`` parameter.
    """
    spec = PiiSpec(
        category=category,
        erasure=erasure,
        retention=retention,
        legal_basis=legal_basis,
        purpose=purpose,
        description=description,
    )
    return {INFO_KEY: spec}


def subject_link(path: str, *, subject_id_column: str = "id") -> dict[str, Any]:
    """Declare how a table reaches the data subject.

    Attach via ``Table.info`` or the mapped class's ``__table_args__``
    info dict. The subject table itself declares ``subject_link("")``.

    Args:
        path: Dotted relationship path to the subject table.
        subject_id_column: Identifier column on the subject table.

    Returns:
        A dict suitable for SQLAlchemy's table-level ``info`` parameter.
    """
    return {INFO_KEY: SubjectLink(path=path, subject_id_column=subject_id_column)}
