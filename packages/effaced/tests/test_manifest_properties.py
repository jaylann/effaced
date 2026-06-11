"""Property-based guarantees on the manifest format."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from schema_strategies import GeneratedSchema, annotated_schemas, scaled_examples

from effaced import ColumnEntry, DataMap, PiiCategory, PiiSpec, TableEntry

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


@given(schema=annotated_schemas())
@settings(max_examples=scaled_examples(8), deadline=None)
def test_collected_manifest_from_any_schema_round_trips(schema: GeneratedSchema) -> None:
    """Manifests collected from real metadata round-trip too.

    Covers the dimensions the hand-built strategy above does not draw:
    subject links, retention policies, erasure strategies, and legal bases.
    """
    assert DataMap.from_payload(schema.data_map.to_payload()) == schema.data_map
