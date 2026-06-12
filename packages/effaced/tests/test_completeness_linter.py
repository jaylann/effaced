"""The completeness linter flags exactly what the data map does not cover."""

from __future__ import annotations

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table

from effaced import (
    CompletenessFinding,
    PiiCategory,
    bind_tables,
    lint_completeness,
    pii,
    subject_link,
)
from effaced.adapters.sqlalchemy import INFO_KEY
from effaced.exceptions import ManifestError
from effaced.testing import assert_data_map_complete


def _annotated_metadata() -> MetaData:
    """A fresh one-table schema with every plain column annotated."""
    metadata = MetaData()
    Table(
        "people",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("email", String, info=pii(PiiCategory.CONTACT)),
        info=subject_link(""),
    )
    return metadata


def test_unannotated_tables_are_flagged_whole(metadata: MetaData) -> None:
    findings = lint_completeness(metadata)
    flagged_tables = {finding.table for finding in findings if finding.column is None}
    assert flagged_tables == {"tags", "user_tags", "app_settings"}


def test_unannotated_plain_columns_in_annotated_tables_are_flagged(metadata: MetaData) -> None:
    findings = lint_completeness(metadata)
    flagged_columns = {
        (finding.table, finding.column) for finding in findings if finding.column is not None
    }
    assert flagged_columns == {("users", "theme"), ("invoices", "closed_at")}


def test_primary_and_foreign_key_columns_are_never_flagged(metadata: MetaData) -> None:
    structural = {("orders", "id"), ("orders", "user_id"), ("comments", "parent_id")}
    findings = {(finding.table, finding.column) for finding in lint_completeness(metadata)}
    assert findings.isdisjoint(structural)


def test_effaced_owned_tables_are_exempt() -> None:
    metadata = _annotated_metadata()
    bind_tables(metadata)
    assert lint_completeness(metadata) == ()


def test_fully_annotated_metadata_yields_no_findings() -> None:
    assert lint_completeness(_annotated_metadata()) == ()


def test_finding_messages_name_the_gap() -> None:
    table_finding = CompletenessFinding(table="tags")
    column_finding = CompletenessFinding(table="users", column="theme")
    assert "tags" in table_finding.message
    assert "users.theme" in column_finding.message


def test_assert_data_map_complete_passes_on_complete_metadata() -> None:
    assert_data_map_complete(_annotated_metadata())


def test_assert_data_map_complete_lists_every_finding(metadata: MetaData) -> None:
    with pytest.raises(AssertionError) as excinfo:
        assert_data_map_complete(metadata)
    message = str(excinfo.value)
    for expected in ("tags", "user_tags", "app_settings", "users.theme"):
        assert expected in message


def test_assert_data_map_complete_honours_exemptions(metadata: MetaData) -> None:
    assert_data_map_complete(
        metadata,
        exempt_tables=("tags", "user_tags", "app_settings"),
        exempt_columns=("users.theme", "invoices.closed_at"),
    )


def test_exempt_table_silences_its_column_findings() -> None:
    metadata = _annotated_metadata()
    Table(
        "profiles",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("person_id", Integer, ForeignKey("people.id")),
        Column("bio", String, info=pii(PiiCategory.IDENTITY)),
        Column("locale", String),
        info=subject_link("person"),
    )
    assert_data_map_complete(metadata, exempt_tables=("profiles",))


def test_stale_exemptions_fail_loudly() -> None:
    with pytest.raises(AssertionError, match="long_gone"):
        assert_data_map_complete(_annotated_metadata(), exempt_tables=("long_gone",))


def test_malformed_table_annotation_raises_like_the_collector() -> None:
    """The complement contract holds on malformed input: both sides reject it."""
    metadata = MetaData()
    Table(
        "broken",
        metadata,
        Column("id", Integer, primary_key=True),
        info={INFO_KEY: "not a SubjectLink"},
    )
    with pytest.raises(ManifestError, match="broken"):
        lint_completeness(metadata)
    with pytest.raises(ManifestError, match="broken"):
        assert_data_map_complete(metadata)
