"""Forward migration of serialized manifests.

Old manifests are auto-migrated forward, never rejected (see
``docs/decisions/0003-widened-semver.md``). Each historical schema version
gets an explicit upgrade branch in :func:`migrate`.
"""

from __future__ import annotations

from typing import Any

from effaced.exceptions import ManifestError

MANIFEST_SCHEMA_VERSION = 1
"""Current manifest schema version. Bump on ANY format change, with a
matching upgrade branch in :func:`migrate` — this is a MAJOR release."""


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Lift a serialized manifest to :data:`MANIFEST_SCHEMA_VERSION`.

    Args:
        data: A manifest payload of any historical schema version.

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
    # version 1 is current — future versions add upgrade branches above this line
    return data
