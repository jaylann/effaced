"""Shared test machinery for resolver packages (public API, ADR 0011).

Import this subpackage from tests only: it pulls in :mod:`pytest`, which
is deliberately not a runtime dependency of effaced — ``import effaced``
never reaches this module.
"""

from effaced.testing.in_memory_resolver import InMemoryResolver
from effaced.testing.resolver_conformance_suite import ResolverConformanceSuite

__all__ = ["InMemoryResolver", "ResolverConformanceSuite"]
