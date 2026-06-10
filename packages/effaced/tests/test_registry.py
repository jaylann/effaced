"""The resolver registry is explicit and refuses silent replacement."""

from __future__ import annotations

import pytest

from effaced import Resolver, ResolverErasure, ResolverError, ResolverExport, ResolverRegistry
from effaced.annotations import SubjectRef


class FakeResolver:
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def export_subject(self, ref: SubjectRef) -> ResolverExport:
        return ResolverExport(resolver=self._name)

    async def erase_subject(self, ref: SubjectRef) -> ResolverErasure:
        return ResolverErasure(resolver=self._name, already_absent=True)


def test_fake_satisfies_protocol() -> None:
    assert isinstance(FakeResolver("fake"), Resolver)


def test_register_and_get() -> None:
    registry = ResolverRegistry()
    resolver = FakeResolver("fake")
    registry.register(resolver)
    assert registry.get("fake") is resolver
    assert registry.all() == (resolver,)


def test_duplicate_registration_raises() -> None:
    registry = ResolverRegistry()
    registry.register(FakeResolver("fake"))
    with pytest.raises(ResolverError, match="already registered"):
        registry.register(FakeResolver("fake"))


def test_unknown_name_raises() -> None:
    registry = ResolverRegistry()
    with pytest.raises(ResolverError, match="no resolver"):
        registry.get("ghost")
