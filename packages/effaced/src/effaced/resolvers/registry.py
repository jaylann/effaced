"""The :class:`ResolverRegistry` — explicit, auditable registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from effaced.exceptions import ResolverError

if TYPE_CHECKING:
    from effaced.resolvers.base import Resolver


class ResolverRegistry:
    """Holds every resolver an application has wired in.

    Registration is explicit on purpose — no auto-discovery, no entry-point
    magic. The registry doubles as the auditable declaration of *every
    place this application holds PII outside its own database*.
    """

    def __init__(self) -> None:
        """Create an empty registry."""
        self._resolvers: dict[str, Resolver] = {}

    def register(self, resolver: Resolver) -> None:
        """Add one resolver.

        Args:
            resolver: The resolver to add.

        Raises:
            ResolverError: If a resolver with the same name is already
                registered — silent replacement would falsify the audit
                picture.
        """
        if resolver.name in self._resolvers:
            msg = f"resolver {resolver.name!r} is already registered"
            raise ResolverError(msg)
        self._resolvers[resolver.name] = resolver

    def get(self, name: str) -> Resolver:
        """Return one resolver by name.

        Args:
            name: The resolver's stable name.

        Raises:
            ResolverError: If no resolver with that name is registered.
        """
        try:
            return self._resolvers[name]
        except KeyError:
            msg = f"no resolver registered under {name!r}"
            raise ResolverError(msg) from None

    def all(self) -> tuple[Resolver, ...]:
        """Every registered resolver, in registration order."""
        return tuple(self._resolvers.values())
