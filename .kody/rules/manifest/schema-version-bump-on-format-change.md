---
title: "Manifest format changes bump MANIFEST_SCHEMA_VERSION and add a migration"
scope: "file"
path:
  - "packages/effaced/src/effaced/manifest/**"
  - "packages/effaced/src/effaced/annotations/**"
  - "packages/effaced/src/effaced/categories/**"
severity_min: "critical"
buckets: ["manifest-format", "breaking-change"]
enabled: true
---
## Instructions
Any change to the serialized manifest format requires a `MANIFEST_SCHEMA_VERSION`
bump plus an explicit forward-migration branch in `migration.py`. Old manifests
are migrated, never rejected. Flag a serialized-shape change (new/removed/renamed
persisted field, changed encoding) that lacks the version bump or the migration.

## Examples
### Bad example
```python
class DataMap(BaseModel):
    tables: list[TableMap]
    region: str   # new persisted field, but MANIFEST_SCHEMA_VERSION unchanged
```
### Good example
```python
MANIFEST_SCHEMA_VERSION = 3   # was 2

def _migrate_v2_to_v3(payload: dict[str, Any]) -> dict[str, Any]:
    payload["region"] = payload.get("region", "unknown")
    return payload
```
