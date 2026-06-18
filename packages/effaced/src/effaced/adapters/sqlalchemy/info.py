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
from effaced.exceptions import ConfigurationError

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


def subject_link(
    path: str,
    *,
    subject_id_columns: tuple[str, ...] | str = "id",
    subject_id_column: str | None = None,
) -> dict[str, Any]:
    """Declare how a table reaches the data subject.

    Attach via ``Table.info`` or the mapped class's ``__table_args__``
    info dict. The subject table itself declares ``subject_link("")``.

    Args:
        path: Dotted relationship path to the subject table.
        subject_id_columns: Ordered identifier column(s) on the subject
            table. A bare ``str`` is the single-column case (the default,
            ``"id"``); a tuple declares a composite subject key whose order
            aligns to the caller's
            :class:`~effaced.CompositeSubjectId` values (ADR 0025).
        subject_id_column: Deprecated singular alias for the one-column
            case; mutually exclusive with ``subject_id_columns``. Kept so
            existing single-column annotations need no edit.

    Returns:
        A dict suitable for SQLAlchemy's table-level ``info`` parameter.

    Raises:
        ConfigurationError: If both ``subject_id_columns`` and the
            ``subject_id_column`` alias are passed non-default.
    """
    columns = _subject_id_columns(subject_id_columns, subject_id_column)
    return {INFO_KEY: SubjectLink(path=path, subject_id_columns=columns)}


def _subject_id_columns(
    subject_id_columns: tuple[str, ...] | str,
    subject_id_column: str | None,
) -> tuple[str, ...]:
    """Normalize the column argument to a tuple, honouring the singular alias."""
    if subject_id_column is not None:
        if subject_id_columns != "id":
            msg = "pass either subject_id_columns or the subject_id_column alias, not both"
            raise ConfigurationError(msg)
        return (subject_id_column,)
    if isinstance(subject_id_columns, str):
        return (subject_id_columns,)
    return subject_id_columns
