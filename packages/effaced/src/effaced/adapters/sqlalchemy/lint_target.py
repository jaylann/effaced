"""Load the :class:`LintTarget` a CLI lints from a ``module:attribute`` spec."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import MetaData

from effaced.exceptions import ConfigurationError

if TYPE_CHECKING:
    from sqlalchemy.orm import registry


@dataclass(frozen=True, slots=True)
class LintTarget:
    """The live SQLAlchemy handles a lint run needs.

    Loaded by :func:`load_lint_target` from a ``module:attribute`` spec, the
    way Alembic and Gunicorn locate an app object. Holds live handles — not a
    serialized copy — so the linters walk the same metadata the application
    runs against.

    Attributes:
        metadata: The ``MetaData`` holding the mapped tables — the input to
            :func:`collect_data_map` and :func:`lint_completeness`.
        orm_registry: The ORM registry holding the mapped classes, or ``None``
            when the spec resolved to a bare ``MetaData`` (no mappers).
            Reachability linting needs the registry; with it ``None`` the
            caller can lint completeness only.
    """

    metadata: MetaData
    orm_registry: registry | None


def load_lint_target(spec: str) -> LintTarget:
    """Import and resolve a ``module.path:attribute`` spec into a lint target.

    The attribute may be a declarative ``Base`` (anything exposing both
    ``.metadata`` and ``.registry``) or a bare ``MetaData``. A ``Base`` yields a
    target carrying both handles; a bare ``MetaData`` yields one with
    ``orm_registry`` set to ``None`` (completeness-only linting).

    Args:
        spec: ``module.path:attribute`` — e.g. ``myapp.models:Base`` or
            ``myapp.db:metadata``.

    Returns:
        The resolved :class:`LintTarget`.

    Raises:
        ConfigurationError: If the spec is not ``module:attribute``, the module
            cannot be imported, the attribute is missing, or the attribute is
            neither a declarative ``Base`` nor a ``MetaData`` — every failure
            names what to fix, never guesses.
    """
    module_path, attribute = _split(spec)
    module = _import(module_path, spec)
    target = _attribute(module, attribute, spec)
    return _as_lint_target(target, spec)


def _split(spec: str) -> tuple[str, str]:
    """Split ``module:attribute``, refusing anything malformed."""
    module_path, separator, attribute = spec.partition(":")
    if not separator or not module_path or not attribute:
        msg = (
            f"lint target {spec!r} is not 'module.path:attribute' — give the "
            f"import path of your declarative Base or MetaData, e.g. 'myapp.models:Base'"
        )
        raise ConfigurationError(msg)
    return module_path, attribute


def _import(module_path: str, spec: str) -> object:
    """Import the module half of a spec, or fail with the original spec."""
    try:
        return importlib.import_module(module_path)
    except ImportError as exc:
        msg = f"lint target {spec!r}: cannot import module {module_path!r} ({exc})"
        raise ConfigurationError(msg) from exc


def _attribute(module: object, attribute: str, spec: str) -> object:
    """Read the attribute half of a spec off the imported module."""
    try:
        return getattr(module, attribute)
    except AttributeError as exc:
        msg = f"lint target {spec!r}: module has no attribute {attribute!r}"
        raise ConfigurationError(msg) from exc


def _as_lint_target(target: object, spec: str) -> LintTarget:
    """Coerce a resolved attribute into a :class:`LintTarget`."""
    if isinstance(target, MetaData):
        return LintTarget(metadata=target, orm_registry=None)
    metadata = getattr(target, "metadata", None)
    orm_registry = getattr(target, "registry", None)
    if isinstance(metadata, MetaData) and orm_registry is not None:
        return LintTarget(metadata=metadata, orm_registry=orm_registry)
    msg = (
        f"lint target {spec!r} resolved to {type(target).__name__}, which is "
        f"neither a SQLAlchemy MetaData nor a declarative Base (expected "
        f"'.metadata' and '.registry' attributes)"
    )
    raise ConfigurationError(msg)
