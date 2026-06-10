---
title: "Domain models stay frozen; no model_construct in production code"
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
Domain models are frozen pydantic `BaseModel`s with `extra="forbid"`; invariants
live in validators. Flag `model_construct()` in production code — it bypasses
validators and can build an instance that violates a manifest invariant.

## Examples
### Bad example
```python
# Skips validation — an invalid DataMap can now exist.
dm = DataMap.model_construct(tables=raw_tables)
```
### Good example
```python
dm = DataMap(tables=raw_tables)        # validators run
dm2 = DataMap.from_payload(payload)    # validated construction path
```
