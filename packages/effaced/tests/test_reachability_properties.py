"""The reachability linter is the exact inverse of subject-graph resolution.

For any generated annotated schema, ``lint_reachability(...) == ()`` if and
only if ``resolve_subject_graph(...)`` succeeds — proven over arbitrary drawn
schemas, plus a deliberately broken variation that must fail both ways.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from schema_strategies import SUBJECT_TABLE, GeneratedSchema, annotated_schemas, scaled_examples

from effaced import (
    SubjectResolutionError,
    collect_data_map,
    lint_reachability,
    resolve_subject_graph,
)
from effaced.adapters.sqlalchemy import INFO_KEY

pytestmark = pytest.mark.property


@settings(max_examples=scaled_examples(4), deadline=None)
@given(schema=annotated_schemas())
def test_findings_empty_iff_resolution_succeeds(schema: GeneratedSchema) -> None:
    """The headline contract: empty findings ⇔ a resolvable subject graph."""
    findings = lint_reachability(schema.data_map, schema.mappers)
    # Generated schemas are valid by construction, so both sides agree on "ok".
    assert findings == ()
    assert resolve_subject_graph(schema.data_map, schema.mappers)


@settings(max_examples=scaled_examples(4), deadline=None)
@given(schema=annotated_schemas())
def test_dropping_the_anchor_breaks_both_sides(schema: GeneratedSchema) -> None:
    """Strip the subject anchor: the linter flags it and resolution raises."""
    subject = schema.metadata.tables[SUBJECT_TABLE]
    # Remove the subject_link annotation so no table anchors the graph.
    del subject.info[INFO_KEY]
    broken_map = collect_data_map(schema.metadata)
    assert lint_reachability(broken_map, schema.mappers) != ()
    with pytest.raises(SubjectResolutionError):
        resolve_subject_graph(broken_map, schema.mappers)
