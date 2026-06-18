"""The :class:`Subject` of a request and the provider that resolves it."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict

# SubjectIdentifier is imported at runtime (not under TYPE_CHECKING) because
# Subject appears in FastAPI route/dependency signatures: every type there must
# resolve at runtime, not only for the type checker (python.md FastAPI rule).
# The alias is str | CompositeSubjectId, so the composite class resolves through
# it without a second import.
from effaced import SubjectIdentifier, SubjectRef

__all__ = ["Subject", "SubjectProvider"]


class Subject(BaseModel):
    """Who the request is about — resolved by your auth, never by effaced.

    The router never authenticates and never guesses where a subject lives
    outside the database: both are application knowledge. Your subject
    provider (any FastAPI dependency returning this model) supplies the
    subject id your models are keyed by and the external refs — e.g. the
    Stripe customer id you stored at signup — that route the subject's
    export and erasure to registered resolvers.

    Attributes:
        subject_id: The id the annotated models key the subject by — a
            single-column ``str`` or a composite
            :class:`~effaced.CompositeSubjectId` (ADR 0025), passed through
            to the engines unchanged.
        refs: Where the subject lives in external systems; each ref's
            ``kind`` names the resolver that handles it (ADR 0008).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_id: SubjectIdentifier
    refs: tuple[SubjectRef, ...] = ()


SubjectProvider: TypeAlias = Callable[..., Subject] | Callable[..., Awaitable[Subject]]
"""A FastAPI dependency resolving the request's :class:`Subject`.

Sync or async, and free to depend on your own auth dependencies — a plain
``def`` route can depend on an ``async def`` provider; FastAPI resolves
the dependency tree on the event loop either way.
"""
