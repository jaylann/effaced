"""Property-based guarantees on the manifest format."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from effaced import DataMap, PiiCategory, PiiSpec
from effaced.manifest import ColumnEntry, TableEntry

pytestmark = pytest.mark.property

identifiers = st.text(
    alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
    min_size=1,
    max_size=30,
)

specs = st.builds(
    PiiSpec,
    category=st.sampled_from(PiiCategory),
    purpose=st.none() | identifiers,
)

tables = st.lists(
    st.builds(
        TableEntry,
        name=identifiers,
        columns=st.lists(
            st.builds(ColumnEntry, name=identifiers, spec=specs),
            max_size=5,
        ).map(tuple),
    ),
    max_size=5,
).map(tuple)


@given(tables=tables)
def test_any_manifest_round_trips(tables: tuple[TableEntry, ...]) -> None:
    """Serialization never loses or mutates a declaration."""
    data_map = DataMap(tables=tables)
    assert DataMap.from_payload(data_map.to_payload()) == data_map
