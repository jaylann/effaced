"""The data map is collected, serialized, and migrated correctly."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest
from sqlalchemy import MetaData

from effaced import DataMap, ErasureStrategy, ManifestError, collect_data_map
from effaced.manifest import MANIFEST_SCHEMA_VERSION
from effaced.manifest.migration import migrate


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


def test_migrate_writes_anchor_into_each_v1_retention_dict() -> None:
    """``migrate`` lifts a v1 payload by adding ``anchor`` to every retention dict.

    Asserts on the migrated *payload* (not the loaded ``DataMap``, whose
    model default would mask a no-op migration): the v1→v2 branch must walk
    ``tables`` → ``columns`` → ``spec`` → ``retention`` and set ``anchor``
    on each retention dict, leaving non-retention columns and the original
    retention fields untouched.
    """
    payload = {
        "schema_version": 1,
        "tables": [
            {
                "name": "invoices",
                "columns": [
                    {"name": "amount", "spec": {"retention": {"reason": "tax", "duration": "P7Y"}}},
                    {"name": "note", "spec": {"retention": None}},
                ],
            }
        ],
    }
    migrated = migrate(payload)
    assert migrated["schema_version"] == MANIFEST_SCHEMA_VERSION  # lifted all the way forward
    amount_retention = migrated["tables"][0]["columns"][0]["spec"]["retention"]
    assert "anchor" in amount_retention  # the key is added, not merely defaulted on read
    assert amount_retention["anchor"] is None
    assert amount_retention["reason"] == "tax"  # v1 fields preserved
    assert amount_retention["duration"] == "P7Y"
    assert migrated["tables"][0]["columns"][1]["spec"]["retention"] is None


def test_migrate_tolerates_a_sparse_v1_payload() -> None:
    """The v1→v2 walk never crashes on absent ``tables``/``columns``/``spec`` keys.

    A v1 manifest may legitimately omit ``tables`` entirely, hold a table
    with no ``columns``, or a column with no ``spec``. The migration must
    treat each missing level as empty and still lift ``schema_version`` to
    the current version — pinning the ``.get(..., ())`` / ``.get(..., {})``
    defaults against a payload that actually exercises them.
    """
    assert migrate({"schema_version": 1}) == {"schema_version": MANIFEST_SCHEMA_VERSION}
    assert migrate({"schema_version": 1, "tables": [{"name": "t"}]}) == {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "tables": [{"name": "t"}],
    }
    spec_less = {"schema_version": 1, "tables": [{"name": "t", "columns": [{"name": "c"}]}]}
    assert migrate(spec_less)["schema_version"] == MANIFEST_SCHEMA_VERSION


def test_migrate_preserves_an_existing_v1_anchor() -> None:
    """``setdefault`` never overwrites an ``anchor`` already present."""
    payload = {
        "schema_version": 1,
        "tables": [
            {
                "name": "invoices",
                "columns": [
                    {
                        "name": "amount",
                        "spec": {"retention": {"duration": "P7Y", "anchor": "created"}},
                    },
                ],
            }
        ],
    }
    migrated = migrate(payload)
    assert migrated["tables"][0]["columns"][0]["spec"]["retention"]["anchor"] == "created"


def test_migrate_lifts_v2_subject_id_column_into_subject_id_columns() -> None:
    """A v2 manifest's singular ``subject_id_column`` becomes the v3 tuple.

    ADR 0025: the v2→v3 branch lifts each ``tables[].subject_link.
    subject_id_column`` (a ``str``) into ``subject_id_columns`` (a one-element
    list); a link without the singular key (default ``id``) is untouched.
    The whole payload loads as a current :class:`~effaced.DataMap`.
    """
    payload = {
        "schema_version": 2,
        "tables": [
            {"name": "users", "subject_link": {"path": "", "subject_id_column": "uuid"}},
            {"name": "invoices", "subject_link": {"path": "user"}},
        ],
    }
    migrated = migrate(payload)
    assert migrated["schema_version"] == MANIFEST_SCHEMA_VERSION
    users_link = migrated["tables"][0]["subject_link"]
    assert "subject_id_column" not in users_link  # the singular key is removed
    assert users_link["subject_id_columns"] == ["uuid"]
    # A link that never carried the singular key keeps the model default.
    assert "subject_id_column" not in migrated["tables"][1]["subject_link"]
    loaded = DataMap.from_payload(payload)
    assert loaded.tables[0].subject_link is not None
    assert loaded.tables[0].subject_link.subject_id_columns == ("uuid",)
    assert loaded.tables[1].subject_link is not None
    assert loaded.tables[1].subject_link.subject_id_columns == ("id",)


def test_migrate_lifts_an_old_v1_manifest_all_the_way_to_v3() -> None:
    """A v1 manifest migrates 1→2→3 and is never rejected (ADR 0003/0025)."""
    payload = {
        "schema_version": 1,
        "tables": [{"name": "users", "subject_link": {"path": "", "subject_id_column": "id"}}],
    }
    loaded = DataMap.from_payload(payload)
    assert loaded.schema_version == MANIFEST_SCHEMA_VERSION
    assert loaded.tables[0].subject_link is not None
    assert loaded.tables[0].subject_link.subject_id_columns == ("id",)


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
