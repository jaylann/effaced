"""The :func:`registry_from_settings` helper — config-driven registration."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from effaced.exceptions import ConfigurationError
from effaced.resolvers.registry import ResolverRegistry
from effaced.resolvers.registry_build import RegistryBuild
from effaced.resolvers.spec_outcome import SpecOutcome

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from effaced.resolvers.spec import ResolverSpec


def _is_present(settings: Mapping[str, str], key: str) -> bool:
    """Whether ``key`` is configured: present in the mapping and non-blank.

    A key whose value is blank or whitespace counts as ABSENT — the
    compose-file convention where ``KEY=`` (or ``KEY="   "``) disables a
    feature without removing the line.
    """
    value = settings.get(key)
    return value is not None and value.strip() != ""


def registry_from_settings(
    specs: Sequence[ResolverSpec],
    settings: Mapping[str, str] | None = None,
) -> RegistryBuild:
    """Build a :class:`~effaced.ResolverRegistry` from application settings.

    Each spec names the settings keys a resolver needs; this evaluates the
    specs in order against ``settings`` and registers the resolvers whose
    required keys are all present. Adding a resolver becomes a configuration
    change — a Stripe key in config registers the Stripe resolver — rather
    than wiring code.

    This is *declarative wiring, not discovery*. Registration stays explicit
    and auditable: every resolver that can register is named in a spec the
    application authored. There is no entry-point scanning, no import-time
    discovery, and no resolver is registered that no spec declared. The
    returned :attr:`~effaced.RegistryBuild.outcomes` are the audit surface —
    log them at startup to record what was wired and what was skipped.

    Presence rule:
        A key is *present* when it is in ``settings`` AND its value is
        non-blank after :meth:`str.strip`. A blank or whitespace-only value
        counts as ABSENT (the compose-file ``KEY=`` disable convention); the
        resulting skip is recorded in the outcomes, never silent.

    Per spec, exactly one of:
        - **All required keys present** (or none required): ``build`` is
          called with a mapping containing EXACTLY the declared keys that are
          present — every required key plus any present optional keys, and
          nothing else, so a spec cannot quietly read undeclared settings —
          then the resolver is registered. The outcome is ``registered=True``.
        - **Zero required keys present:** the spec is skipped and its outcome
          records ``registered=False`` with the missing required key names.
        - **Some but not all required keys present:** a
          :class:`~effaced.exceptions.ConfigurationError` is raised naming the
          spec and the missing key NAMES. Key VALUES never appear in the
          message — partial configuration is an authoring error surfaced
          loudly, not a silent skip.

    A spec with zero required keys always builds (its resolver needs no
    configuration). An empty spec list yields an empty registry and an empty
    outcomes tuple — both are valid.

    Args:
        specs: The resolver specs to evaluate, in priority order. Their order
            is the registration order and the outcomes order.
        settings: The configuration mapping. When ``None``, a snapshot of
            :data:`os.environ` is used.

    Returns:
        A :class:`~effaced.RegistryBuild` holding the populated registry and
        one :class:`~effaced.SpecOutcome` per spec, in spec order.

    Raises:
        ConfigurationError: If a spec has some but not all required keys
            present, or if a built resolver's ``name`` does not equal the
            spec's declared ``name`` (the declared list must match what
            registers).
        ResolverError: Via :meth:`ResolverRegistry.register` if two specs
            build resolvers with the same name — silent replacement would
            falsify the audit picture.
    """
    resolved: Mapping[str, str] = dict(os.environ) if settings is None else settings
    registry = ResolverRegistry()
    outcomes: list[SpecOutcome] = []

    for spec in specs:
        present_required = [key for key in spec.settings_keys if _is_present(resolved, key)]
        missing_required = [key for key in spec.settings_keys if not _is_present(resolved, key)]

        if spec.settings_keys and not present_required:
            outcomes.append(
                SpecOutcome(name=spec.name, registered=False, missing_keys=tuple(missing_required))
            )
            continue

        if missing_required:
            names = ", ".join(missing_required)
            msg = (
                f"resolver spec {spec.name!r} is partially configured: "
                f"missing required settings {names}"
            )
            raise ConfigurationError(msg)

        present_optional = [key for key in spec.optional_keys if _is_present(resolved, key)]
        declared = (*spec.settings_keys, *present_optional)
        build_settings: Mapping[str, str] = {key: resolved[key] for key in declared}

        resolver = spec.build(build_settings)
        if resolver.name != spec.name:
            msg = (
                f"resolver spec {spec.name!r} built a resolver named "
                f"{resolver.name!r}; the declared name must match what registers"
            )
            raise ConfigurationError(msg)

        registry.register(resolver)
        outcomes.append(SpecOutcome(name=spec.name, registered=True))

    return RegistryBuild(registry=registry, outcomes=tuple(outcomes))
