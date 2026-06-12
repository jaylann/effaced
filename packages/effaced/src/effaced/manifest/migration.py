"""Forward migration of serialized manifests.

Old manifests are auto-migrated forward, never rejected (see
``docs/decisions/0003-widened-semver.md``). Each historical schema version
gets an explicit upgrade branch in :func:`migrate`.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from effaced.exceptions import ManifestError

MANIFEST_SCHEMA_VERSION = 2
"""Current manifest schema version. Bump on ANY format change, with a
matching upgrade branch in :func:`migrate`. Adding a field behind a forward
migration is MINOR; removing or renaming serialized fields is MAJOR (old
manifests must keep loading forever)."""


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Lift a serialized manifest to :data:`MANIFEST_SCHEMA_VERSION`.

    Args:
        data: A manifest payload of any historical schema version. Never
            mutated: upgrades operate on a deep copy.

    Returns:
        The payload upgraded to the current schema version.

    Raises:
        ManifestError: If the manifest is newer than this library
            understands, or carries no recognisable version.
    """
    version = data.get("schema_version")
    if not isinstance(version, int):
        msg = "manifest has no integer schema_version"
        raise ManifestError(msg)
    if version > MANIFEST_SCHEMA_VERSION:
        msg = (
            f"manifest schema_version {version} is newer than this effaced "
            f"release understands ({MANIFEST_SCHEMA_VERSION}); upgrade effaced"
        )
        raise ManifestError(msg)
    data = deepcopy(data)
    if version == 1:
        # v2 added RetentionPolicy.anchor (ADR 0012): old policies have no clock.
        for table in data.get("tables", ()):
            for column in table.get("columns", ()):
                retention = column.get("spec", {}).get("retention")
                if retention is not None:
                    retention.setdefault("anchor", None)
        data["schema_version"] = 2
    # version 2 is current — future versions add upgrade branches above this line
    return data
