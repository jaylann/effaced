"""Settings-driven registration is all-or-nothing per spec, and never silent."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from effaced import RegistryBuild, ResolverSpec, SpecOutcome, registry_from_settings
from effaced.exceptions import ConfigurationError, ResolverError
from effaced.testing import InMemoryResolver

SENTINEL = "sk_live_SECRETVALUE"


def _spec(
    name: str = "memory",
    *,
    settings_keys: tuple[str, ...] = (),
    optional_keys: tuple[str, ...] = (),
    built_name: str | None = None,
) -> ResolverSpec:
    """A spec whose builder returns an InMemoryResolver named ``built_name``."""
    resolver_name = name if built_name is None else built_name

    def build(_: Mapping[str, str]) -> InMemoryResolver:
        return InMemoryResolver(name=resolver_name)

    return ResolverSpec(
        name=name, settings_keys=settings_keys, optional_keys=optional_keys, build=build
    )


def test_registers_when_all_required_present() -> None:
    spec = _spec("stripe", settings_keys=("STRIPE_API_KEY",))
    build = registry_from_settings([spec], {"STRIPE_API_KEY": "rk_live_x"})

    assert build.registry.get("stripe").name == "stripe"
    assert build.outcomes == (SpecOutcome(name="stripe", registered=True),)


def test_skips_with_missing_keys_when_all_absent() -> None:
    spec = _spec("stripe", settings_keys=("STRIPE_API_KEY", "STRIPE_ACCOUNT"))
    build = registry_from_settings([spec], {})

    assert build.registry.all() == ()
    assert build.outcomes == (
        SpecOutcome(
            name="stripe",
            registered=False,
            missing_keys=("STRIPE_API_KEY", "STRIPE_ACCOUNT"),
        ),
    )


def test_partial_configuration_raises_naming_missing_keys() -> None:
    spec = _spec("s3", settings_keys=("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"))

    with pytest.raises(ConfigurationError) as excinfo:
        registry_from_settings([spec], {"AWS_ACCESS_KEY_ID": SENTINEL})

    message = str(excinfo.value)
    assert "AWS_SECRET_ACCESS_KEY" in message
    assert "s3" in message


def test_configuration_error_never_leaks_a_value() -> None:
    spec = _spec("s3", settings_keys=("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"))

    with pytest.raises(ConfigurationError) as excinfo:
        registry_from_settings([spec], {"AWS_ACCESS_KEY_ID": SENTINEL})

    assert SENTINEL not in str(excinfo.value)


def test_blank_value_counts_as_absent() -> None:
    spec = _spec("stripe", settings_keys=("STRIPE_API_KEY",))
    build = registry_from_settings([spec], {"STRIPE_API_KEY": "   "})

    assert build.registry.all() == ()
    assert build.outcomes == (
        SpecOutcome(name="stripe", registered=False, missing_keys=("STRIPE_API_KEY",)),
    )


def test_optional_keys_passed_only_when_present() -> None:
    captured: list[Mapping[str, str]] = []

    def build(settings: Mapping[str, str]) -> InMemoryResolver:
        captured.append(dict(settings))
        return InMemoryResolver(name="s3")

    spec = ResolverSpec(
        name="s3",
        settings_keys=("AWS_ACCESS_KEY_ID",),
        optional_keys=("AWS_REGION", "AWS_ENDPOINT_URL"),
        build=build,
    )
    registry_from_settings(
        [spec],
        {"AWS_ACCESS_KEY_ID": "id", "AWS_REGION": "eu-central-1", "AWS_ENDPOINT_URL": "   "},
    )

    assert captured == [{"AWS_ACCESS_KEY_ID": "id", "AWS_REGION": "eu-central-1"}]


def test_builder_receives_exactly_the_declared_keys() -> None:
    captured: list[Mapping[str, str]] = []

    def build(settings: Mapping[str, str]) -> InMemoryResolver:
        captured.append(dict(settings))
        return InMemoryResolver(name="s3")

    spec = ResolverSpec(
        name="s3",
        settings_keys=("AWS_ACCESS_KEY_ID",),
        optional_keys=("AWS_REGION",),
        build=build,
    )
    # An undeclared key present in settings must never reach the builder.
    registry_from_settings(
        [spec],
        {"AWS_ACCESS_KEY_ID": "id", "AWS_REGION": "eu", "UNDECLARED": "leak"},
    )

    assert captured == [{"AWS_ACCESS_KEY_ID": "id", "AWS_REGION": "eu"}]


def test_zero_required_keys_always_builds() -> None:
    spec = _spec("memory", settings_keys=())
    build = registry_from_settings([spec], {})

    assert build.registry.get("memory").name == "memory"
    assert build.outcomes == (SpecOutcome(name="memory", registered=True),)


def test_name_mismatch_raises() -> None:
    spec = _spec("stripe", settings_keys=("K",), built_name="not-stripe")

    with pytest.raises(ConfigurationError) as excinfo:
        registry_from_settings([spec], {"K": "v"})

    assert "stripe" in str(excinfo.value)
    assert "not-stripe" in str(excinfo.value)


def test_duplicate_names_across_specs_raise_resolver_error() -> None:
    one = _spec("dup", settings_keys=("A",))
    two = _spec("dup", settings_keys=("B",))

    with pytest.raises(ResolverError):
        registry_from_settings([one, two], {"A": "1", "B": "2"})


def test_settings_none_reads_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVKEY", "present")
    spec = _spec("env", settings_keys=("ENVKEY",))

    build = registry_from_settings([spec])

    assert build.registry.get("env").name == "env"
    assert build.outcomes == (SpecOutcome(name="env", registered=True),)


def test_outcomes_order_and_length_match_specs() -> None:
    specs = [
        _spec("a", settings_keys=("A",)),
        _spec("b", settings_keys=("B",)),
        _spec("c", settings_keys=("C",)),
    ]
    build = registry_from_settings(specs, {"A": "1", "C": "3"})

    assert [outcome.name for outcome in build.outcomes] == ["a", "b", "c"]
    assert len(build.outcomes) == len(specs)
    assert [outcome.registered for outcome in build.outcomes] == [True, False, True]


def test_empty_spec_list_yields_empty_build() -> None:
    build = registry_from_settings([], {"ANY": "value"})

    assert isinstance(build, RegistryBuild)
    assert build.registry.all() == ()
    assert build.outcomes == ()


def test_spec_rejects_overlapping_keys() -> None:
    with pytest.raises(ValueError, match="overlap"):
        ResolverSpec(
            name="x",
            settings_keys=("K",),
            optional_keys=("K",),
            build=lambda _: InMemoryResolver(name="x"),
        )
