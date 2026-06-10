"""The :class:`SubjectRef` model — an opaque reference to one data subject."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SubjectRef(BaseModel):
    """Opaque reference to one data subject, passed to resolvers.

    Resolvers receive *references* (e.g. a Stripe customer id), never the
    subject's rich PII — the library moves identifiers, not data.

    Attributes:
        kind: Namespace of the identifier (``"stripe_customer"``, ``"email"``).
        value: The identifier itself.
        extra: Additional identifiers a resolver may need (string-typed on
            purpose — refs must stay loggable and PII-light).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str = Field(min_length=1)
    value: str = Field(min_length=1)
    extra: dict[str, str] = Field(default_factory=dict)
