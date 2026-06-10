"""The surrogate registry produces type-valid, irreversible replacements."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Uuid,
)
from sqlalchemy.types import TypeEngine

from effaced import AnonymizationError, SurrogateRegistry, default_surrogate_registry


def test_string_surrogate_is_opaque_and_unique() -> None:
    registry = default_surrogate_registry()
    first = registry.surrogate_for(String())
    second = registry.surrogate_for(String())
    assert isinstance(first, str)
    assert first.startswith("anon-")
    assert first != second


def test_text_resolves_through_string() -> None:
    assert isinstance(default_surrogate_registry().surrogate_for(Text()), str)


def test_integer_family_resolves_through_integer() -> None:
    registry = default_surrogate_registry()
    assert registry.surrogate_for(Integer()) == 0
    assert registry.surrogate_for(SmallInteger()) == 0


def test_scalar_defaults() -> None:
    registry = default_surrogate_registry()
    assert registry.surrogate_for(Boolean()) is False
    assert registry.surrogate_for(Float()) == 0.0
    assert registry.surrogate_for(Numeric()) == Decimal("0")
    assert registry.surrogate_for(Date()) == date(1970, 1, 1)
    assert registry.surrogate_for(DateTime()) == datetime(1970, 1, 1)


def test_uuid_surrogate_is_unique() -> None:
    registry = default_surrogate_registry()
    first = registry.surrogate_for(Uuid())
    assert isinstance(first, UUID)
    assert first != registry.surrogate_for(Uuid())


class MoneyType(TypeEngine[int]):
    """A custom column type the defaults know nothing about."""


def test_unknown_type_raises_loudly() -> None:
    with pytest.raises(AnonymizationError, match="no surrogate registered"):
        default_surrogate_registry().surrogate_for(MoneyType())


def test_register_extends_the_registry() -> None:
    registry = default_surrogate_registry()
    registry.register(MoneyType, lambda: -1)
    assert registry.surrogate_for(MoneyType()) == -1


def test_register_overrides_last_wins() -> None:
    registry = default_surrogate_registry()
    registry.register(String, lambda: "redacted")
    assert registry.surrogate_for(String()) == "redacted"
    assert registry.surrogate_for(Text()) == "redacted"


def test_empty_registry_has_no_defaults() -> None:
    with pytest.raises(AnonymizationError, match="no surrogate registered"):
        SurrogateRegistry().surrogate_for(String())
