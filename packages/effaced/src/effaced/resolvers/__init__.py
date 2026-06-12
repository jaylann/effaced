"""Resolvers — reach PII in external systems through one interface."""

from effaced.resolvers.base import Resolver
from effaced.resolvers.erasure import ResolverErasure
from effaced.resolvers.export import ResolverExport
from effaced.resolvers.rectification import ResolverRectification
from effaced.resolvers.rectifying import RectifyingResolver
from effaced.resolvers.registry import ResolverRegistry

__all__ = [
    "RectifyingResolver",
    "Resolver",
    "ResolverErasure",
    "ResolverExport",
    "ResolverRectification",
    "ResolverRegistry",
]
