"""For any generated schema, findings exactly complement the collected map.

The linter's published contract is set-theoretic: every table is either in
the data map, whole-table flagged, or ``effaced_``-exempt — and within a
mapped table every column is annotated, structural (PK/FK), or flagged.
Nothing is double-counted, nothing falls through.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from schema_strategies import GeneratedSchema, annotated_schemas, scaled_examples
from sqlalchemy import Column, Integer, String, Table

from effaced import lint_completeness

pytestmark = pytest.mark.property


@settings(max_examples=scaled_examples(8), deadline=None)
@given(schema=annotated_schemas())
def test_findings_exactly_complement_the_data_map(schema: GeneratedSchema) -> None:
    Table(
        "zz_undeclared",
        schema.metadata,
        Column("id", Integer, primary_key=True),
        Column("note", String),
    )
    Table(
        "effaced_owned_extra",
        schema.metadata,
        Column("id", Integer, primary_key=True),
        Column("payload", String),
    )
    findings = lint_completeness(schema.metadata)
    mapped = {
        entry.name: {column.name for column in entry.columns} for entry in schema.data_map.tables
    }

    flagged_tables = {finding.table for finding in findings if finding.column is None}
    all_tables = {table.name for table in schema.metadata.sorted_tables}
    assert flagged_tables.isdisjoint(mapped)
    assert flagged_tables | set(mapped) == all_tables - {"effaced_owned_extra"}

    for table in schema.metadata.sorted_tables:
        if table.name not in mapped:
            continue
        flagged_columns = {
            finding.column
            for finding in findings
            if finding.table == table.name and finding.column is not None
        }
        structural = {
            column.name for column in table.columns if column.primary_key or column.foreign_keys
        }
        annotated = mapped[table.name]
        assert flagged_columns.isdisjoint(annotated | structural)
        assert annotated | structural | flagged_columns == {column.name for column in table.columns}
