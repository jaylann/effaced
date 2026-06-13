"""The reachability linter is the exact inverse of subject-graph resolution.

For any generated annotated schema, ``lint_reachability(...) == ()`` if and
only if ``resolve_subject_graph(...)`` succeeds — proven in both directions:
valid-by-construction schemas agree on success, and a randomly drawn
structure-breaking mutation makes both sides fail together.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from schema_strategies import SUBJECT_TABLE, GeneratedSchema, annotated_schemas, scaled_examples

from effaced import (
    SubjectResolutionError,
    collect_data_map,
    lint_reachability,
    resolve_subject_graph,
    subject_link,
)
from effaced.adapters.sqlalchemy import INFO_KEY

pytestmark = pytest.mark.property

_SUBJECT_MUTATIONS = ("noop", "drop_anchor", "missing_subject_id")
_TABLE_MUTATIONS = ("extra_anchor", "bogus_path", "foreign_subject_id")


@settings(max_examples=scaled_examples(4), deadline=None)
@given(schema=annotated_schemas())
def test_findings_empty_iff_resolution_succeeds(schema: GeneratedSchema) -> None:
    """The headline contract, success direction: both sides agree on "ok"."""
    findings = lint_reachability(schema.data_map, schema.mappers)
    # Generated schemas are valid by construction, so both sides agree on "ok".
    assert findings == ()
    assert resolve_subject_graph(schema.data_map, schema.mappers)


@settings(max_examples=scaled_examples(4), deadline=None)
@given(schema=annotated_schemas(), data=st.data())
def test_random_breakage_keeps_linter_and_resolution_aligned(
    schema: GeneratedSchema, data: st.DataObject
) -> None:
    """The full iff under mutation: findings are non-empty exactly when resolution raises.

    A drawn mutation either leaves the schema intact (``noop``) or breaks one
    structural concern the PROOFS row enumerates — missing/extra anchor,
    missing subject-id column, a path segment that is not a relationship, or
    a ``subject_id_column`` declared on a non-subject table. Whatever happens,
    the linter and ``resolve_subject_graph`` must agree.
    """
    non_subject = sorted(name for name in schema.metadata.tables if name != SUBJECT_TABLE)
    choices = _SUBJECT_MUTATIONS + (_TABLE_MUTATIONS if non_subject else ())
    mutation = data.draw(st.sampled_from(choices))
    _apply(mutation, schema, data, non_subject)
    data_map = collect_data_map(schema.metadata)
    findings = lint_reachability(data_map, schema.mappers)
    try:
        resolve_subject_graph(data_map, schema.mappers)
        resolved = True
    except SubjectResolutionError:
        resolved = False
    assert (findings == ()) == resolved
    if mutation != "noop":
        assert findings != ()


def _apply(
    mutation: str, schema: GeneratedSchema, data: st.DataObject, non_subject: list[str]
) -> None:
    """Mutate the schema's annotations in place (mappers stay untouched)."""
    tables = schema.metadata.tables
    if mutation == "noop":
        return
    if mutation == "drop_anchor":
        del tables[SUBJECT_TABLE].info[INFO_KEY]
        return
    if mutation == "missing_subject_id":
        tables[SUBJECT_TABLE].info.update(subject_link("", subject_id_column="zz_missing"))
        return
    target = tables[data.draw(st.sampled_from(non_subject))]
    if mutation == "extra_anchor":
        target.info.update(subject_link(""))
    elif mutation == "bogus_path":
        target.info.update(subject_link("zz_not_a_relationship"))
    else:  # foreign_subject_id: only meaningful on the subject table itself
        link = target.info[INFO_KEY]
        target.info.update(subject_link(link.path, subject_id_column="zz_custom"))
