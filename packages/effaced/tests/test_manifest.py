"""The data map is collected, serialized, and migrated correctly."""

from __future__ import annotations

import json
from copy import deepcopy

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


def test_v1_payload_migrates_forward_to_anchorless_retention(metadata: MetaData) -> None:
    """A v1 manifest (no ``anchor`` key) loads as v2 with ``anchor=None``."""
    payload = collect_data_map(metadata).to_payload()
    payload["schema_version"] = 1
    for table in payload["tables"]:
        for column in table["columns"]:
            retention = column["spec"]["retention"]
            if retention is not None:
                del retention["anchor"]
    before = deepcopy(payload)
    restored = DataMap.from_payload(payload)
    assert restored.schema_version == MANIFEST_SCHEMA_VERSION
    (billing,) = restored.table("invoices").columns
    assert billing.spec.retention is not None
    assert billing.spec.retention.anchor is None
    assert billing.spec.retention.duration is not None  # v1 fields survive the lift
    assert payload == before  # migration never mutates the caller's payload


def test_future_schema_version_is_rejected_loudly(metadata: MetaData) -> None:
    payload = collect_data_map(metadata).to_payload()
    payload["schema_version"] = MANIFEST_SCHEMA_VERSION + 1
    with pytest.raises(ManifestError, match="newer"):
        DataMap.from_payload(payload)


def test_versionless_payload_is_rejected(metadata: MetaData) -> None:
    payload = collect_data_map(metadata).to_payload()
    del payload["schema_version"]
    with pytest.raises(ManifestError, match=r"^manifest has no integer schema_version$"):
        DataMap.from_payload(payload)


def test_structurally_invalid_payload_is_rejected() -> None:
    payload = {"schema_version": MANIFEST_SCHEMA_VERSION, "tables": [{"bogus": True}]}
    with pytest.raises(ManifestError, match="invalid manifest payload"):
        DataMap.from_payload(payload)


def test_payload_is_json_native(metadata: MetaData) -> None:
    """to_payload yields plain JSON types — enums become exactly str, tuples lists."""
    payload = collect_data_map(metadata).to_payload()
    json.dumps(payload)  # must not raise
    assert type(payload["tables"]) is list
    specs = [column["spec"] for table in payload["tables"] for column in table["columns"]]
    assert specs
    for spec in specs:
        assert type(spec["erasure"]) is str
        assert type(spec["category"]) is str
