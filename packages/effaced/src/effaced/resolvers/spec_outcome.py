"""The :class:`SpecOutcome` — what happened to one spec during a build."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SpecOutcome(BaseModel):
    """The auditable record of evaluating one :class:`~effaced.ResolverSpec`.

    :func:`~effaced.registry_from_settings` emits one outcome per spec, in
    spec order, so a startup audit can show *which* configured resolvers were
    registered and which were skipped for missing configuration. A skip is
    recorded here — never silent.

    Attributes:
        name: The spec's declared resolver name.
        registered: Whether the resolver was registered. ``True`` means its
            required keys were all present and it joined the registry; a
            skipped spec is ``False``.
        missing_keys: When ``registered`` is ``False`` because configuration
            was absent, the required key names that were missing. Empty for a
            registered spec.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    registered: bool
    missing_keys: tuple[str, ...] = ()
