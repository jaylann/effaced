"""Property proofs: registration is all-or-nothing per spec, never partial."""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from hypothesis import given
from hypothesis import strategies as st

from effaced import ResolverSpec, registry_from_settings
from effaced.exceptions import ConfigurationError
from effaced.testing import InMemoryResolver

pytestmark = pytest.mark.property

key_names = st.text(alphabet="ABCDEFGHIJKLMNOP_", min_size=1, max_size=8)
required_key_sets = st.lists(key_names, min_size=1, max_size=5, unique=True)


def _spec(name: str, settings_keys: tuple[str, ...]) -> ResolverSpec:
    def build(_: Mapping[str, str]) -> InMemoryResolver:
        return InMemoryResolver(name=name)

    return ResolverSpec(name=name, settings_keys=settings_keys, build=build)


@given(keys=required_key_sets, data=st.data())
def test_registered_iff_all_required_present(keys: list[str], data: st.DataObject) -> None:
    """A spec registers exactly when every required key is present."""
    spec = _spec("r", tuple(keys))
    present = data.draw(st.lists(st.sampled_from(keys), unique=True))
    settings = dict.fromkeys(present, "v")

    if set(present) == set(keys):
        build = registry_from_settings([spec], settings)
        assert build.outcomes[0].registered is True
        assert build.registry.get("r").name == "r"
    elif not present:
        build = registry_from_settings([spec], settings)
        assert build.outcomes[0].registered is False
        assert set(build.outcomes[0].missing_keys) == set(keys)
    else:
        with pytest.raises(ConfigurationError):
            registry_from_settings([spec], settings)


@given(keys=st.lists(key_names, min_size=2, max_size=5, unique=True), data=st.data())
def test_nonempty_proper_subset_raises(keys: list[str], data: st.DataObject) -> None:
    """Some-but-not-all required keys present always raises — never a silent skip."""
    spec = _spec("r", tuple(keys))
    present = data.draw(
        st.lists(st.sampled_from(keys), min_size=1, max_size=len(keys) - 1, unique=True)
    )
    settings = dict.fromkeys(present, "v")

    with pytest.raises(ConfigurationError):
        registry_from_settings([spec], settings)


@given(
    specs=st.lists(st.tuples(st.integers(0, 4)), min_size=0, max_size=4),
    data=st.data(),
)
def test_outcomes_align_with_specs(specs: list[tuple[int]], data: st.DataObject) -> None:
    """One outcome per spec in order; registered names == registry names."""
    # Disjoint, per-spec key namespaces so a spec is only present when chosen —
    # cross-spec key sharing cannot produce a partial-present raise here.
    built = [
        _spec(f"r{index}", tuple(f"K{index}_{n}" for n in range(count + 1)))
        for index, (count,) in enumerate(specs)
    ]
    settings: dict[str, str] = {}
    for spec in built:
        if data.draw(st.booleans()):
            for key in spec.settings_keys:
                settings[key] = "v"

    build = registry_from_settings(built, settings)

    assert len(build.outcomes) == len(built)
    assert [outcome.name for outcome in build.outcomes] == [spec.name for spec in built]
    registered = {outcome.name for outcome in build.outcomes if outcome.registered}
    assert {resolver.name for resolver in build.registry.all()} == registered
