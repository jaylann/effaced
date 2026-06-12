"""The :class:`ResolverSpec` — one declarative resolver-from-settings rule."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from effaced.resolvers.base import Resolver


class ResolverSpec(BaseModel):
    """A declarative rule that turns configuration into one registered resolver.

    A spec names the settings keys a resolver needs and how to build it from
    their values. :func:`~effaced.registry_from_settings` evaluates a sequence
    of specs against an application's settings and registers the resolvers
    whose required keys are all present — so adding a resolver becomes a
    configuration change, not wiring code.

    This is *declarative wiring, not discovery*: every resolver an application
    can register is named in a spec the application authors by hand. There is
    no entry-point scanning and no import-time magic — the spec list stays the
    auditable "where could my PII go" declaration.

    Attributes:
        name: The resolver's stable name. It MUST equal the ``.name`` of the
            resolver that :attr:`build` returns; the mismatch is rejected so
            the declared spec list always matches what actually registers.
        settings_keys: The settings keys this resolver requires, treated
            all-or-nothing. An empty tuple means the resolver is always built
            (it needs no configuration).
        optional_keys: Settings keys the resolver can use when present but does
            not require. Present optional keys are passed to :attr:`build`;
            absent ones simply are not.
        build: A callable that receives a mapping containing exactly the
            declared keys that were present (every required key plus any
            present optional keys) and returns the resolver to register.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    settings_keys: tuple[str, ...] = ()
    optional_keys: tuple[str, ...] = ()
    build: Callable[[Mapping[str, str]], Resolver]

    @model_validator(mode="after")
    def _keys_are_disjoint_and_unique(self) -> ResolverSpec:
        """Reject duplicate keys within or overlapping between the key tuples.

        A key that appears twice — within ``settings_keys``, within
        ``optional_keys``, or across the two — would make the "exactly the
        declared keys" build contract ambiguous, so it is a spec authoring
        error caught at construction.
        """
        if len(set(self.settings_keys)) != len(self.settings_keys):
            msg = f"resolver spec {self.name!r} has duplicate settings_keys"
            raise ValueError(msg)
        if len(set(self.optional_keys)) != len(self.optional_keys):
            msg = f"resolver spec {self.name!r} has duplicate optional_keys"
            raise ValueError(msg)
        overlap = set(self.settings_keys) & set(self.optional_keys)
        if overlap:
            shared = ", ".join(sorted(overlap))
            msg = f"resolver spec {self.name!r} keys overlap: {shared}"
            raise ValueError(msg)
        return self
