---
title: "Manifest round-trips preserve every declaration"
scope: "file"
path:
  - "packages/effaced/src/effaced/manifest/**"
  - "packages/effaced/src/effaced/annotations/**"
  - "packages/effaced/src/effaced/categories/**"
severity_min: "medium"
buckets: ["manifest-format"]
enabled: true
---
## Instructions
Round-trip fidelity must hold: `DataMap.from_payload(m.to_payload()) == m`. A
change to `to_payload`/`from_payload` (or any field's serialization) that is not
accompanied by an updated round-trip property test is a blocker — silent
serialization drift loses or mutates declarations. Flag serialization changes
with no matching property-test update.

## Examples
### Bad example
```python
def to_payload(self) -> dict[str, Any]:
    # Drops `retention_policy`; from_payload can't reconstruct it.
    return {"name": self.name, "strategy": self.strategy}
```
### Good example
```python
def to_payload(self) -> dict[str, Any]:
    return {
        "name": self.name,
        "strategy": self.strategy,
        "retention_policy": self.retention_policy,  # full fidelity
    }
```
