"""Resolvers — reach PII in external systems through one interface."""

from effaced.resolvers.base import Resolver
from effaced.resolvers.rectifying import RectifyingResolver
from effaced.resolvers.registry import ResolverRegistry
from effaced.resolvers.results import ResolverErasure, ResolverExport, ResolverRectification

__all__ = [
    "RectifyingResolver",
    "Resolver",
    "ResolverErasure",
    "ResolverExport",
    "ResolverRectification",
    "ResolverRegistry",
]
