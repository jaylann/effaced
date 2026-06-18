"""The :class:`CompositeSubjectId` model — a multi-column subject identity."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CompositeSubjectId(BaseModel):
    """A data subject identified by an ordered tuple of column values.

    Subjects whose identity spans several columns — the common multi-tenant
    ``(tenant_id, user_id)`` shape, or any natural composite key — carry one
    of these instead of a bare ``str`` (ADR 0025). The values are
    **positional**: their order aligns left-to-right with the columns the
    manifest declares in
    :attr:`~effaced.SubjectLink.subject_id_columns`. effaced always matches
    the *whole* ordered key, never a partial one, so the arity of this tuple
    must equal the number of declared columns at the call boundary.

    A single-column subject is just a ``str`` — this model is only for the
    multi-column case. See :data:`~effaced.SubjectIdentifier` for the union
    every engine entry point accepts, and
    :func:`~effaced.canonical_subject_id` for the deterministic,
    collision-free serialization used in storage and the audit trail.

    Attributes:
        values: The subject's key-column values, in declared column order;
            at least one, none empty.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    values: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _values_are_non_empty(self) -> CompositeSubjectId:
        """Every key element must be a non-empty string.

        An empty element cannot carry a column value and would make the
        canonical serialization ambiguous against a shorter key.
        """
        if any(value == "" for value in self.values):
            msg = "CompositeSubjectId values must all be non-empty strings"
            raise ValueError(msg)
        return self
