"""The :class:`DataMap` — the full picture of where personal data lives."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from effaced.exceptions import ManifestError
from effaced.manifest.migration import MANIFEST_SCHEMA_VERSION, migrate
from effaced.manifest.table_entry import TableEntry


class DataMap(BaseModel):
    """The complete, versioned manifest for one application.

    The manifest is *derived*, never authored: adapters (e.g.
    :func:`effaced.adapters.sqlalchemy.collect_data_map`) walk your models
    and build it from the annotations they find. Serialize with
    :meth:`to_payload` for audit snapshots, diffing, and tooling; load with
    :meth:`from_payload`, which migrates old versions forward.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tables: tuple[TableEntry, ...] = ()
    schema_version: int = MANIFEST_SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible payload."""
        return self.model_dump(mode="json")

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> DataMap:
        """Deserialize a manifest, migrating old schema versions forward.

        Args:
            data: A payload produced by :meth:`to_payload` (any version).

        Returns:
            The manifest, lifted to the current schema version.

        Raises:
            ManifestError: If the payload is structurally invalid or newer
                than this library understands.
        """
        migrated = migrate(data)
        try:
            return cls.model_validate(migrated)
        except ValidationError as exc:
            msg = f"invalid manifest payload: {exc}"
            raise ManifestError(msg) from exc

    def table(self, name: str) -> TableEntry:
        """Return the entry for one store.

        Raises:
            ManifestError: If the store is not in the manifest.
        """
        for entry in self.tables:
            if entry.name == name:
                return entry
        msg = f"table {name!r} is not in the data map"
        raise ManifestError(msg)
