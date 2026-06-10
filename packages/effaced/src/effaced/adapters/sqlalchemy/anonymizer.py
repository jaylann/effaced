"""The :class:`SurrogateRegistry` — type-valid, irreversible replacements."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, Numeric, String, Uuid

from effaced.exceptions import AnonymizationError

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.types import TypeEngine


class SurrogateRegistry:
    """Maps SQLAlchemy column types to surrogate-value factories.

    Anonymization replaces a value with an irreversible surrogate instead
    of ``NULL`` — surrogates stay valid under ``NOT NULL`` and unique
    constraints, which is why factories are invoked once per cell (string
    and UUID surrogates are unique per call). Lookup walks the column
    type's MRO, so registering :class:`~sqlalchemy.String` also covers
    ``Text`` and every other ``String`` subclass.

    Unlike :class:`~effaced.resolvers.ResolverRegistry`, re-registering a
    type silently overrides it (last wins): replacing a default surrogate
    with your own is the very point of extensibility, and nothing audited
    depends on which factory produced a surrogate.

    The registry is consumed by the erasure executor, never by
    :meth:`~effaced.erasure.ErasurePlanner.plan` — plans carry no values,
    which keeps them deterministic and side-effect-free.
    """

    def __init__(self) -> None:
        """Create an empty registry (see :func:`default_surrogate_registry`)."""
        self._factories: dict[type[TypeEngine[Any]], Callable[[], object]] = {}

    def register(self, sa_type: type[TypeEngine[Any]], factory: Callable[[], object]) -> None:
        """Map one SQLAlchemy type (and its subclasses) to a factory.

        Args:
            sa_type: The type class to cover, e.g. ``sqlalchemy.String``.
            factory: Zero-argument callable producing one surrogate value;
                called once per anonymized cell.
        """
        self._factories[sa_type] = factory

    def surrogate_for(self, column_type: TypeEngine[Any]) -> object:
        """Produce one surrogate value for a column of the given type.

        Args:
            column_type: The column's type instance, e.g. ``Text()``.

        Returns:
            A fresh, type-valid surrogate value.

        Raises:
            AnonymizationError: If neither the type nor any of its base
                classes has a registered factory.
        """
        for klass in type(column_type).__mro__:
            factory = self._factories.get(klass)
            if factory is not None:
                return factory()
        msg = (
            f"no surrogate registered for column type "
            f"{type(column_type).__name__!r}; add one with SurrogateRegistry.register"
        )
        raise AnonymizationError(msg)


def default_surrogate_registry() -> SurrogateRegistry:
    """A registry covering the common SQLAlchemy scalar types.

    Strings become unique opaque tokens (``anon-…``), numbers and booleans
    become zero-values, dates collapse to the Unix epoch (naive — override
    for timezone-aware columns), and UUIDs become fresh random UUIDs.

    Returns:
        A new, independently extensible registry.
    """
    registry = SurrogateRegistry()
    registry.register(String, lambda: f"anon-{uuid4().hex}")
    registry.register(Integer, lambda: 0)
    registry.register(Boolean, lambda: False)
    registry.register(Float, lambda: 0.0)
    registry.register(Numeric, lambda: Decimal("0"))
    registry.register(Date, lambda: date(1970, 1, 1))
    registry.register(
        DateTime, lambda: datetime(1970, 1, 1)
    )  # epoch sentinel; tz-aware columns override
    registry.register(Uuid, uuid4)
    return registry
