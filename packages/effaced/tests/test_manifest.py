"""The data map is collected, serialized, and migrated correctly."""

from __future__ import annotations

import pytest
from sqlalchemy import MetaData

from effaced import DataMap, ErasureStrategy, ManifestError, collect_data_map
from effaced.manifest import MANIFEST_SCHEMA_VERSION


def test_collects_only_annotated_tables(metadata: MetaData) -> None:
    data_map = collect_data_map(metadata)
    names = [table.name for table in data_map.tables]
    assert "users" in names
    assert "invoices" in names
    assert "app_settings" not in names


def test_collects_only_annotated_columns(metadata: MetaData) -> None:
    data_map = collect_data_map(metadata)
    users = data_map.table("users")
    assert sorted(column.name for column in users.columns) == ["email", "name"]


def test_subject_links_are_read(metadata: MetaData) -> None:
    data_map = collect_data_map(metadata)
    assert data_map.table("users").subject_link is not None
    assert data_map.table("users").subject_link.is_subject_table  # type: ignore[union-attr]
    assert data_map.table("invoices").subject_link.path == "user"  # type: ignore[union-attr]


def test_retention_survives_collection(metadata: MetaData) -> None:
    data_map = collect_data_map(metadata)
    (billing,) = data_map.table("invoices").columns
    assert billing.spec.erasure is ErasureStrategy.RETAIN
    assert billing.spec.retention is not None


def test_payload_round_trip(metadata: MetaData) -> None:
    data_map = collect_data_map(metadata)
    restored = DataMap.from_payload(data_map.to_payload())
    assert restored == data_map
    assert restored.schema_version == MANIFEST_SCHEMA_VERSION


def test_unknown_table_raises(metadata: MetaData) -> None:
    data_map = collect_data_map(metadata)
    with pytest.raises(ManifestError, match="not in the data map"):
        data_map.table("ghosts")


def test_future_schema_version_is_rejected_loudly(metadata: MetaData) -> None:
    payload = collect_data_map(metadata).to_payload()
    payload["schema_version"] = MANIFEST_SCHEMA_VERSION + 1
    with pytest.raises(ManifestError, match="newer"):
        DataMap.from_payload(payload)


def test_versionless_payload_is_rejected(metadata: MetaData) -> None:
    payload = collect_data_map(metadata).to_payload()
    del payload["schema_version"]
    with pytest.raises(ManifestError, match="schema_version"):
        DataMap.from_payload(payload)
