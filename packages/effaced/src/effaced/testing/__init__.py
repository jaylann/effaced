"""Shared test machinery — resolver conformance and CI gates (public API, ADR 0018).

Import this subpackage from tests only: it pulls in :mod:`pytest`, which
is deliberately not a runtime dependency of effaced — ``import effaced``
never reaches this module.
"""

from effaced.testing.completeness_gate import assert_data_map_complete
from effaced.testing.in_memory_resolver import InMemoryResolver
from effaced.testing.resolver_conformance_suite import ResolverConformanceSuite

__all__ = ["InMemoryResolver", "ResolverConformanceSuite", "assert_data_map_complete"]
