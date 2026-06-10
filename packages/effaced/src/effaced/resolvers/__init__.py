"""Resolvers — reach PII in external systems through one interface."""

from effaced.resolvers.base import Resolver
from effaced.resolvers.registry import ResolverRegistry
from effaced.resolvers.results import ResolverErasure, ResolverExport

__all__ = ["Resolver", "ResolverErasure", "ResolverExport", "ResolverRegistry"]
