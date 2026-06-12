"""The :class:`RegistryBuild` — a built registry plus its per-spec outcomes."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from effaced.resolvers.registry import ResolverRegistry
from effaced.resolvers.spec_outcome import SpecOutcome


class RegistryBuild(BaseModel):
    """The result of building a registry from settings.

    Returned by :func:`~effaced.registry_from_settings`. The
    :attr:`outcomes` tuple is the audit surface: one
    :class:`~effaced.SpecOutcome` per evaluated spec, in spec order, recording
    every registration and every skip.

    Attributes:
        registry: The populated registry, ready to wire into the exporter and
            planner.
        outcomes: One outcome per spec, in the order the specs were given.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    registry: ResolverRegistry
    outcomes: tuple[SpecOutcome, ...]
